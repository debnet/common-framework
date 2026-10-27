"""Teste la branche COMMON_JSONFIELD sans remplacer le module canonique en mémoire."""

import importlib.util

from django.db import connection, models
from django.test import TransactionTestCase, override_settings
from django.test.utils import isolate_apps

from common import fields


class LegacyJsonTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        registry = isolate_apps("common")
        registry.enable()
        cls.addClassCleanup(registry.disable)
        spec = importlib.util.spec_from_file_location(
            "common.tests.models._legacy_fields", fields.__file__
        )
        cls.legacy = importlib.util.module_from_spec(spec)
        with override_settings(COMMON_JSONFIELD=True):
            spec.loader.exec_module(cls.legacy)
        from model_bakery.generators import default_mapping

        for field in (cls.legacy.JsonField, cls.legacy.CustomDecimalField):
            cls.addClassCleanup(default_mapping.pop, field, None)
        legacy_field = cls.legacy.JsonField

        class LegacyRecord(models.Model):
            payload = legacy_field(default=dict, null=True)

            class Meta:
                app_label = "common"

        cls.model = LegacyRecord
        with connection.schema_editor() as editor:
            editor.create_model(cls.model)

    @classmethod
    def tearDownClass(cls):
        try:
            with connection.schema_editor() as editor:
                editor.delete_model(cls.model)
        finally:
            super().tearDownClass()

    def tearDown(self):
        self.model.objects.all().delete()
        super().tearDown()

    def test_legacy_json_values_round_trip(self):
        for value in ({"active": False, "count": 0}, [1, 2], "text", 0, False, None):
            with self.subTest(value=value):
                instance = self.model.objects.create(payload=value)
                self.assertEqual(self.model.objects.get(pk=instance.pk).payload, value)

    def test_key_and_nested_lookups_execute_on_sqlite(self):
        self.model.objects.create(
            payload={"nested": {"name": "Alice"}, "enabled": True}
        )
        self.model.objects.create(payload={"other": "value"})
        self.assertEqual(self.model.objects.filter(payload__has="nested").count(), 1)
        self.assertEqual(
            self.model.objects.filter(payload__hasall=["nested", "enabled"]).count(), 1
        )
        self.assertEqual(
            self.model.objects.filter(payload__hasany=["missing", "other"]).count(), 1
        )
        self.assertEqual(
            self.model.objects.filter(payload__nested__name="Alice").count(), 1
        )
        self.assertEqual(
            self.model.objects.filter(payload__nested__name__icontains="ali").count(), 1
        )

    def test_conversion_wrappers_and_base_values(self):
        field = self.legacy.JsonField()
        for raw, expected in (
            ('{"a": 1}', {"a": 1}),
            ("[1, 2]", [1, 2]),
            ('"text"', "text"),
        ):
            wrapped = field.to_python(raw)
            self.assertEqual(wrapped.base, expected)
            self.assertIsInstance(repr(wrapped), str)
        self.assertEqual(field.to_python(None), {})
        self.assertIsNone(self.legacy.JsonField(null=True).to_python(None))

    def test_json_paths_escape_special_keys(self):
        self.assertEqual(
            self.legacy.compile_json_path(["items", "0", "name"]), '$."items"[0]."name"'
        )
        self.assertEqual(self.legacy.compile_json_path(['a"b']), '$."a\\"b"')
