from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs

from django.http import QueryDict
from django.template import Context, RequestContext, Template
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIRequestFactory

from common.models import Webhook
from common.templatetags import tools


class FilterTests(SimpleTestCase):
    def test_get_supports_mapping_sequence_and_attribute(self):
        for value, key, expected in (
            ({"a": 1}, "a", 1),
            ([2], 0, 2),
            (SimpleNamespace(a=3), "a", 3),
            (None, "a", None),
        ):
            self.assertEqual(tools.filter_get(value, key), expected)
        self.assertIsNone(tools.filter_get({}, "missing"))
        self.assertIsNone(tools.filter_get(SimpleNamespace(), "missing"))

    def test_get_out_of_range_returns_none(self):
        """Régression AUDIT-05 : les index absents ne cassent pas le rendu."""
        for value, key in (([], 5), ([1], -2), ((), 0), ("text", 10)):
            self.assertIsNone(tools.filter_get(value, key))
        self.assertEqual(tools.filter_get([1, 2], -1), 2)

    def test_out_of_range_template_filter_renders_fallback(self):
        template = Template(
            '{% load tools %}{{ items|get:5|default_if_none:"missing" }}'
        )
        self.assertEqual(template.render(Context({"items": []})), "missing")

    def test_metadata_and_date_filters(self):
        self.assertEqual(
            tools.filter_meta(
                SimpleNamespace(get_metadata=lambda key: key.upper()), "key"
            ),
            "KEY",
        )
        self.assertIsNone(tools.filter_meta(object(), "key"))
        self.assertEqual(
            tools.filter_parsedate("2026-01-02", "date_only=true").year, 2026
        )

    def test_gather_split_sort_reverse(self):
        self.assertEqual(tools.filter_gather(["a", "b"], sep="|"), "a|b")
        self.assertEqual(
            tools.filter_gather([{"name": "a"}, {"name": "b"}], "name", "/"), "a/b"
        )
        self.assertEqual(tools.filter_split("a,b", ","), ["a", "b"])
        self.assertEqual(tools.filter_sorted([2, 1]), [1, 2])
        self.assertEqual(
            tools.filter_sorted([{"n": 2}, {"n": 1}], "n"), [{"n": 1}, {"n": 2}]
        )
        objects = [SimpleNamespace(n=2), SimpleNamespace(n=1)]
        self.assertEqual([item.n for item in tools.filter_sorted(objects, "n")], [1, 2])
        self.assertEqual(list(tools.filter_reversed([1, 2])), [2, 1])
        self.assertEqual(tools.filter_sorted([]), [])
        self.assertEqual(tools.filter_reversed([]), [])

    def test_drop_preserves_multivalued_parameters(self):
        for value in ("keep=1&keep=2&secret=x", QueryDict("keep=1&keep=2&secret=x")):
            self.assertEqual(
                parse_qs(tools.filter_drop(value, "secret")), {"keep": ["1", "2"]}
            )

    def test_json_filters_preserve_invalid_values(self):
        self.assertEqual(tools.filter_json_decode('{"a": 1}'), {"a": 1})
        self.assertEqual(tools.filter_json_decode("invalid"), "invalid")
        self.assertEqual(tools.filter_json_encode({"a": 1}), '{"a": 1}')
        value = object()
        self.assertIs(tools.filter_json_encode(value), value)

    @override_settings(LANGUAGE_CODE="fr")
    def test_configuration_and_render_tags(self):
        self.assertEqual(tools.filter_conf("LANGUAGE_CODE"), "fr")
        self.assertEqual(
            tools.tag_render({"name": "Alice"}, "Hello {{ name }}"), "Hello Alice"
        )
        self.assertIn("1", str(tools.filter_localize(1)))

    def test_permission_tags_render_both_branches(self):
        request = APIRequestFactory().get("/")
        request.user = Mock()
        for tag in ("perm", "anyperm"):
            template = Template(
                "{% load tools %}{% "
                + tag
                + ' "common.view_webhook" %}yes{% else %}no{% end'
                + tag
                + " %}"
            )
            request.user.has_perm.return_value = True
            request.user.has_perms.return_value = True
            self.assertEqual(template.render(RequestContext(request)), "yes")
            request.user.has_perm.return_value = False
            request.user.has_perms.return_value = False
            self.assertEqual(template.render(RequestContext(request)), "no")
            self.assertEqual(template.render(Context()), "")

    def test_obfuscation_tags_hide_plaintext(self):
        for arguments in (' "fixed-key"', ""):
            output = Template(
                "{% load tools %}{% obfuscate"
                + arguments
                + " %}private text{% endobfuscate %}"
            ).render(Context())
            self.assertIn("data-encoded=", output)
            self.assertNotIn("private text", output)
        self.assertEqual(tools.ObfuscatorNode(None).encode("abc", "x"), "GRob")


class QueryTagTests(TestCase):
    def setUp(self):
        Webhook.objects.create(name="Alpha", url="https://example.com", timeout=10)
        Webhook.objects.create(name="Beta", url="https://example.com", timeout=20)

    def test_filter_order_fields_and_limit(self):
        context = {}
        self.assertEqual(
            tools.tag_query(
                context,
                Webhook.objects.all(),
                save="rows",
                order_by="-timeout",
                fields="name",
                limit="1",
            ),
            "",
        )
        self.assertEqual([row.name for row in context["rows"]], ["Beta"])

    def test_annotations_and_aggregates(self):
        context = {}
        tools.tag_query(
            context,
            Webhook.objects.all(),
            save="rows",
            lower="name|label",
            name="Alpha",
        )
        self.assertEqual(context["rows"].get().label, "alpha")
        self.assertEqual(
            tools.tag_query({}, Webhook.objects.all(), sum="timeout"),
            {"sum__timeout": 30},
        )

    def test_grouped_aggregate_and_complex_filter(self):
        context = {}
        tools.tag_query(
            context,
            Webhook.objects.all(),
            save="rows",
            group_by="is_create",
            count="id",
            filters="timeout__gte:15",
        )
        self.assertEqual(list(context["rows"]), [{"is_create": True, "count__id": 1}])

    def test_non_queryset_is_returned_unchanged(self):
        self.assertEqual(tools.tag_query({}, [1, 2]), [1, 2])
