import importlib.util
import json
from decimal import Decimal
from unittest.mock import patch

from django import forms
from django.db.models import Q
from django.test import SimpleTestCase, TestCase, override_settings

from common import fields as model_fields
from common import forms as common_forms
from common.forms import (
    BaseFilterForm,
    JsonField,
    get_formset,
    get_model_form,
    get_model_formset,
)
from common.models import Webhook
from common.utils import JsonDecoder, JsonEncoder


class JsonFormFieldTests(SimpleTestCase):
    def test_json_field_accepts_valid_json_with_native_django_jsonfield(self):
        """Régression AUDIT-02 : aucune dépendance envers JsonString en mode natif."""
        self.assertEqual(JsonField().clean('{"active": false}'), {"active": False})

    def test_native_field_uses_common_codecs(self):
        field = JsonField()
        self.assertIsInstance(field, forms.JSONField)
        self.assertIs(field.encoder, JsonEncoder)
        self.assertIs(field.decoder, JsonDecoder)
        self.assertEqual(field.clean('{"amount": 1.25}'), {"amount": Decimal("1.25")})
        self.assertIsInstance(field.clean("1.25"), Decimal)
        self.assertEqual(
            field.prepare_value({"amount": Decimal("1.25")}), '{"amount": 1.25}'
        )

    def test_explicit_codecs_can_override_common_defaults(self):
        field = JsonField(encoder=json.JSONEncoder, decoder=json.JSONDecoder)
        self.assertIs(field.encoder, json.JSONEncoder)
        self.assertIsInstance(field.clean("1.25"), float)
        self.assertIsNone(JsonField(encoder=None, decoder=None).encoder)

    @override_settings(COMMON_JSONFIELD=True)
    def test_native_form_field_also_supports_legacy_model_storage(self):
        self.assertIsInstance(JsonField(), forms.JSONField)
        self.assertEqual(JsonField().clean('{"key": 0}'), {"key": 0})

    def test_invalid_json_is_a_validation_error(self):
        with self.assertRaises(forms.ValidationError) as error:
            JsonField().clean("not-json")
        self.assertEqual(error.exception.code, "invalid")

    def test_json_scalars_and_empty_values(self):
        field = JsonField(required=False)
        for raw, expected in (
            ('"text"', "text"),
            ("false", False),
            ("0", 0),
            ("null", None),
            ("", None),
            (None, None),
        ):
            self.assertEqual(field.clean(raw), expected)

    def test_disabled_field_preserves_value(self):
        field = JsonField(disabled=True)
        self.assertEqual(field.to_python({"value": 1}), {"value": 1})
        self.assertEqual(
            field.bound_data("ignored", {"initial": True}), {"initial": True}
        )

    def test_invalid_bound_data_is_preserved_for_redisplay(self):
        field = JsonField()
        value = field.bound_data("not-json", {})
        self.assertIsInstance(value, JsonField.InvalidInput)
        self.assertEqual(field.prepare_value(value), "not-json")

    def test_json_preparation_and_binding(self):
        field = JsonField()
        self.assertEqual(field.bound_data('{"value": 0}', {}), {"value": 0})
        self.assertEqual(field.prepare_value({"value": 0}), '{"value": 0}')


class LegacyJsonFormFieldTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        spec = importlib.util.spec_from_file_location(
            "common.tests.forms._legacy_forms", common_forms.__file__
        )
        module = importlib.util.module_from_spec(spec)
        # Exerce la sélection de branche, sans prétendre exécuter un ancien Django.
        with patch("django.VERSION", (3, 0)):
            spec.loader.exec_module(module)
        cls.field_class = module.JsonField
        cls.json_string = type("JsonString", (str,), {})
        wrapper = patch.object(model_fields, "JsonString", cls.json_string, create=True)
        wrapper.start()
        cls.addClassCleanup(wrapper.stop)

    def test_old_django_selects_legacy_field(self):
        field = self.field_class()
        self.assertNotIsInstance(field, forms.JSONField)
        self.assertEqual(field.clean('{"value": false}'), {"value": False})
        self.assertIsInstance(field.clean('"text"'), self.json_string)
        self.assertEqual(field.to_python(self.json_string("text")), "text")

    def test_legacy_validation_and_redisplay(self):
        field = self.field_class(required=False)
        self.assertIsNone(field.clean(""))
        with self.assertRaises(forms.ValidationError):
            field.clean("invalid")
        invalid = field.bound_data("invalid", {})
        self.assertIsInstance(invalid, field.InvalidInput)
        self.assertEqual(field.prepare_value(invalid), "invalid")
        self.assertEqual(
            field.prepare_value({"amount": Decimal("1.25")}), '{"amount": 1.25}'
        )


class FormsetTests(SimpleTestCase):
    def test_context_is_passed_to_each_form(self):
        class ContextForm(forms.Form):
            text = forms.CharField()

            def __init__(self, *args, marker=None, **kwargs):
                self.marker = marker
                super().__init__(*args, **kwargs)

        formset = get_formset(ContextForm, extra=2)(context={"marker": "context"})
        self.assertEqual([form.marker for form in formset], ["context", "context"])


class ModelFormTests(TestCase):
    def test_generated_form_creates_and_updates_model(self):
        Form = get_model_form(Webhook, fields=["name", "url"])
        form = Form(data={"name": "Created", "url": "https://example.com/hook"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.error_count(), 0)
        instance = form.save()
        update = Form(data={"name": "Updated", "url": instance.url}, instance=instance)
        self.assertTrue(update.is_valid(), update.errors)
        update.save()
        instance.refresh_from_db()
        self.assertEqual(instance.name, "Updated")
        self.assertEqual(form.inlines, [])
        self.assertIsInstance(str(form.media), str)

    def test_invalid_model_form_reports_errors(self):
        form = get_model_form(Webhook, fields=["url"])(data={"url": "invalid"})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.error_count(), 1)

    def test_model_formset_validates_and_saves(self):
        Formset = get_model_formset(Webhook, fields=["name", "url"], extra=1)
        formset = Formset(
            data={
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "0",
                "form-0-name": "Formset",
                "form-0-url": "https://example.com",
            }
        )
        self.assertTrue(formset.is_valid(), formset.errors)
        self.assertEqual(formset.error_count(), 0)
        self.assertEqual(len(formset.save()), 1)
        self.assertEqual(Webhook.objects.get().name, "Formset")

    def test_get_model_form_can_return_formset(self):
        Formset = get_model_form(Webhook, fields=["name"], formset=True)
        self.assertTrue(hasattr(Formset(), "management_form"))


class FilterFormTests(TestCase):
    def setUp(self):
        Webhook.objects.create(name="alpha", url="https://example.com", timeout=10)
        Webhook.objects.create(name="beta", url="https://example.com", timeout=20)

    def form(self, data):
        class SearchForm(BaseFilterForm):
            name = forms.CharField(required=False)
            minimum = forms.IntegerField(required=False)
            enabled = forms.NullBooleanField(required=False)
            _lookups = {
                "name": "name__icontains",
                "minimum": ("timeout__gte", True),
                "enabled": "is_create",
            }

        return SearchForm(data)

    def test_filters_are_applied_and_counted(self):
        form = self.form({"name": "beta", "minimum": 15})
        result = form.apply(Webhook.objects.all())
        self.assertEqual(list(result.values_list("name", flat=True)), ["beta"])
        self.assertEqual(form.count, 2)
        self.assertTrue(result.query.distinct)
        self.assertEqual(form.filled, {"name": "beta", "minimum": 15})

    def test_invalid_filter_returns_empty_queryset(self):
        self.assertFalse(
            self.form({"minimum": "invalid"}).apply(Webhook.objects.all()).exists()
        )

    def test_empty_filter_keeps_all_rows(self):
        self.assertEqual(self.form({}).apply(Webhook.objects.all()).count(), 2)

    def test_callable_can_add_sequential_filters(self):
        form = self.form({"name": "alpha"})

        def lookup(form, value, args, kwargs, sequential):
            args.append(Q(timeout__gte=5))
            sequential.append(Q(name=value))
            return True

        form._lookups = {"name": lookup}
        result = form.apply(Webhook.objects.all())
        self.assertEqual(list(result.values_list("name", flat=True)), ["alpha"])
