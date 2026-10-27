from django.db.models import F, Q, Value, functions
from django.test import SimpleTestCase, TestCase

from common.api.utils import convert_arg, parse_arg_value, parse_filters, url_value
from common.models import Webhook


class QueryArgumentTests(SimpleTestCase):
    def test_field_reference_and_cast(self):
        self.assertEqual(parse_arg_value("[owner.name]"), F("owner__name"))
        cast = parse_arg_value("[timeout:int]")
        self.assertIsInstance(cast, functions.Cast)
        self.assertEqual(cast.get_source_expressions()[0], F("timeout"))
        self.assertIsNone(parse_arg_value("literal"))
        self.assertEqual(parse_arg_value("literal", keep=True), "literal")

    def test_url_values(self):
        cases = [
            ("id__in", "1,2", (1, 2)),
            ("id__in", "1", (1,)),
            ("name__in", "a,b", ["a", "b"]),
            ("field__isnull", "true", True),
            ("field__isnull", "false", False),
            ("value__contains", '{"a": 1}', {"a": 1}),
            ("value__contains", "a:b", {"a": "b"}),
            ("value__contains", "a,b", ["a", "b"]),
            ("name", "plain", "plain"),
            (None, "12", 12),
            ("id", 3, 3),
        ]
        for lookup, raw, expected in cases:
            with self.subTest(lookup=lookup, raw=raw):
                self.assertEqual(url_value(lookup, raw), expected)

    def test_convert_literal_and_field_argument(self):
        value = convert_arg("unknown", 1, "42")
        self.assertIsInstance(value, Value)
        self.assertEqual(value.value, 42)
        self.assertEqual(convert_arg("unknown", 1, "[name]"), F("name"))

    def test_dictionary_and_nested_filters(self):
        self.assertEqual(parse_filters({"name": "value"}), Q(name="value"))
        self.assertEqual(
            parse_filters("or(name:first,name:second)"),
            Q(name="first") | Q(name="second"),
        )
        self.assertEqual(
            parse_filters("and(name:first,not(timeout:0))"),
            Q(name="first") & ~Q(timeout=0),
        )

    def test_invalid_filter_is_rejected(self):
        with self.assertRaises(Exception):
            parse_filters("and(broken")


class QueryExecutionTests(TestCase):
    def test_nested_filter_executes_with_expected_results(self):
        Webhook.objects.create(name="first", url="https://example.com", timeout=10)
        Webhook.objects.create(name="second", url="https://example.com", timeout=20)
        result = Webhook.objects.filter(
            parse_filters("and(or(name:first,name:second),timeout__gte:15)")
        )
        self.assertEqual(list(result.values_list("name", flat=True)), ["second"])
