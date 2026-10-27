from types import SimpleNamespace

from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from common.api.fields import (
    AsymetricRelatedField,
    ChoiceDisplayField,
    CustomHyperlinkedRelatedField,
    JsonField,
    QuerySetChoiceField,
    ReadOnlyObjectField,
)


class BasicFieldTests(SimpleTestCase):
    def test_json_field_validates_nested_data(self):
        field = JsonField()
        self.assertEqual(
            field.run_validation({"items": [False, 0, None]}),
            {"items": [False, 0, None]},
        )
        with self.assertRaises(ValidationError):
            field.run_validation({"invalid": object()})

    def test_choice_display_and_unknown_values(self):
        field = ChoiceDisplayField(choices=[("A", "Active")])
        self.assertEqual(field.to_representation("A"), "Active")
        self.assertIsNone(field.to_representation("unknown"))
        self.assertTrue(field.read_only)

    def test_read_only_object_preserves_simple_values(self):
        field = ReadOnlyObjectField()
        for value in (0, False, None, {"value": 1}):
            self.assertEqual(field.to_representation(value), value)

    def test_read_only_object_resolves_urls(self):
        class Output(serializers.Serializer):
            value = ReadOnlyObjectField()

        request = APIRequestFactory().get("/")
        for value in ({"url": "/file.txt"}, SimpleNamespace(url="/file.txt")):
            self.assertEqual(
                Output({"value": value}, context={"request": request}).data["value"],
                "http://testserver/file.txt",
            )
        self.assertEqual(
            Output({"value": {"url": "/file.txt"}}).data["value"], "/file.txt"
        )

    def test_model_without_to_dict_is_represented_by_primary_key(self):
        self.assertEqual(ReadOnlyObjectField().to_representation(Group(pk=42)), 42)


class RelatedFieldTests(TestCase):
    def setUp(self):
        self.first = Group.objects.create(name="Alpha")
        self.second = Group.objects.create(name="Beta")

    def test_queryset_choices_filter_and_order(self):
        field = QuerySetChoiceField(Group, value="pk", label="name", order_by="-name")
        self.assertEqual(
            list(field.choices.items()),
            [(self.second.pk, "Beta"), (self.first.pk, "Alpha")],
        )
        filtered = QuerySetChoiceField(
            Group, value="pk", label="name", filters={"name": "Alpha"}
        )
        self.assertEqual(list(filtered.choices.items()), [(self.first.pk, "Alpha")])

    def test_queryset_choices_remain_available_after_initialization(self):
        """Régression AUDIT-07 : la colonne de libellé est distincte du label DRF."""
        field = QuerySetChoiceField(Group, value="pk", label="name")
        self.assertEqual(
            field.values, [(self.first.pk, "Alpha"), (self.second.pk, "Beta")]
        )

    def test_serializer_label_does_not_change_choice_column(self):
        class Input(serializers.Serializer):
            group = QuerySetChoiceField(Group, value="pk", label="name")

        field = Input().fields["group"]
        self.assertEqual(field.label, "Group")
        self.assertEqual(field.label_field, "name")
        field.label = "Translated group label"
        self.assertEqual(
            field.values, [(self.first.pk, "Alpha"), (self.second.pk, "Beta")]
        )
        self.assertEqual(field.run_validation(self.first.pk), self.first.pk)

    def test_values_can_be_recalculated_with_filters_and_order(self):
        field = QuerySetChoiceField(
            Group,
            value="pk",
            label="name",
            filters={"name__startswith": "A"},
            order_by="-name",
        )
        third = Group.objects.create(name="Another")
        self.assertEqual(
            field.values, [(third.pk, "Another"), (self.first.pk, "Alpha")]
        )

    def test_unavailable_choice_source_is_empty(self):
        self.assertEqual(QuerySetChoiceField(None, value="pk", label="name").values, [])

    def test_asymmetric_field_reads_object_and_writes_id(self):
        class Output(serializers.ModelSerializer):
            class Meta:
                model = Group
                fields = ["id", "name"]

        Field = AsymetricRelatedField.from_serializer(Output)
        field = Field()
        self.assertEqual(
            field.to_representation(self.first), {"id": self.first.pk, "name": "Alpha"}
        )
        self.assertEqual(field.run_validation(self.second.pk), self.second)
        self.assertEqual(field.get_choices(cutoff=1), {self.first.pk: "Alpha"})

    def test_hyperlinked_field_accepts_primary_key(self):
        field = CustomHyperlinkedRelatedField(
            view_name="group-detail", queryset=Group.objects.all()
        )
        self.assertEqual(field.to_internal_value(self.first.pk), self.first)
        self.assertEqual(field.get_name(self.first), str(self.first.pk))
        self.assertIsNone(field.get_url(Group(), "group-detail", None, None))
        self.assertIsNone(
            field.get_url(
                self.first, "unknown-detail", APIRequestFactory().get("/"), None
            )
        )
