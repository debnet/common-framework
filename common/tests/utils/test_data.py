"""Contrats des conversions, collections, sérialisation et décorateurs."""

from datetime import datetime, timedelta
from decimal import Decimal
from threading import Lock
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from common import utils


class ConversionTests(SimpleTestCase):
    def test_boolean_inputs(self):
        for value in (True, 1, "true", "YES", "oui", "1"):
            self.assertIs(utils.str_to_bool(value), True)
        for value in (False, 0, None, "", "false", "NO", "non", "0"):
            self.assertIs(utils.str_to_bool(value), False)
        self.assertIsNone(utils.str_to_bool("unknown"))

    def test_numeric_inputs(self):
        for raw, expected in (
            ("42", 42),
            ("1.25", 1.25),
            ("1e3", 1000),
            (0, 0),
            (2.5, 2.5),
        ):
            self.assertEqual(utils.str_to_num(raw), expected)
        self.assertIsNone(utils.str_to_num("invalid"))
        self.assertEqual(utils.str_to_int("3.9"), 3)

    def test_decimal_rounding(self):
        self.assertEqual(utils.decimal(None), Decimal(0))
        self.assertEqual(utils.decimal(""), Decimal(0))
        self.assertEqual(utils.decimal(1.25), Decimal("1.25"))
        self.assertEqual(utils.decimal("1.235", precision=2), Decimal("1.24"))
        self.assertEqual(
            utils.decimal("1.234", precision=Decimal("0.01")), Decimal("1.23")
        )

    def test_decimal_text(self):
        self.assertEqual(utils.decimal_to_str(None), "")
        self.assertEqual(utils.decimal_to_str("100"), "100")
        self.assertEqual(utils.decimal_to_str(Decimal("12.3400")), "12.34")

    def test_decimal_text_preserves_integer_trailing_zeroes(self):
        """Régression AUDIT-01 : ne retirer que les zéros fractionnaires."""
        for raw, expected in (
            ("100", "100"),
            ("100.000", "100"),
            ("0", "0"),
            ("0.00", "0"),
            ("-100", "-100"),
            ("1E+3", "1000"),
            ("1E-3", "0.001"),
            ("12.3400", "12.34"),
        ):
            with self.subTest(value=raw):
                self.assertEqual(utils.decimal_to_str(Decimal(raw)), expected)

    def test_decimal_dictionary_keys_do_not_collide(self):
        value = utils.idict({Decimal("100"): "hundred", Decimal("1"): "one"})
        self.assertEqual(len(value), 2)
        self.assertEqual(value["100"], "hundred")
        self.assertEqual(value[Decimal("100.00")], "hundred")
        self.assertEqual(value["1"], "one")

    def test_first_preserves_falsy_values(self):
        self.assertEqual(utils.get_first(None, 0, 1), 0)
        self.assertEqual(utils.get_first(0, 1, condition=bool), 1)
        self.assertEqual(utils.get_first(None, default="fallback"), "fallback")

    def test_assertion_helper(self):
        utils._assert(True)
        with self.assertRaisesRegex(AssertionError, "reason"):
            utils._assert(False, "reason")
        with self.assertRaises(AssertionError):
            utils._assert(False)

    def test_base64_unicode_and_bytes(self):
        for value in ("été", "text", 123):
            self.assertEqual(
                utils.base64_decode(utils.base64_encode(value)), str(value)
            )

    def test_json_nested_and_special_values(self):
        value = {
            "items": [False, None, {"amount": Decimal("1.25")}],
            "empty": utils.null,
        }
        decoded = utils.json_decode(utils.json_encode(value).encode())
        self.assertEqual(
            decoded,
            {"items": [False, None, {"amount": Decimal("1.25")}], "empty": None},
        )

    def test_python_evaluation_is_only_for_trusted_code(self):
        self.assertEqual(utils.evaluate("a + 2", _globals={}, _locals={"a": 3}), 5)
        context = {}
        utils.execute("answer = 42", _globals={}, _locals=context)
        self.assertEqual(context["answer"], 42)
        # Retirer certains builtins ne constitue pas une sandbox Python.
        self.assertEqual(
            utils.evaluate("().__class__.__base__.__name__", _globals={}, _locals={}),
            "object",
        )

    def test_websearch_expression(self):
        self.assertEqual(utils.web_to_raw_tsquery("alpha beta"), "alpha&beta")
        self.assertEqual(
            utils.web_to_raw_tsquery('"alpha beta" OR gamma'),
            "(alpha <-> beta) | gamma",
        )


class CollectionTests(SimpleTestCase):
    def test_idict_normalizes_keys(self):
        mapping = utils.idict({1: "one", (2, 3): "pair"})
        self.assertEqual(mapping["1"], "one")
        self.assertEqual(mapping[("2", "3")], "pair")
        mapping[4] = "four"
        self.assertEqual(mapping["4"], "four")
        del mapping[4]
        self.assertEqual(len(mapping), 2)
        self.assertEqual(set(mapping), {"1", ("2", "3")})
        self.assertIn("one", repr(mapping))
        self.assertIn("one", str(mapping))
        self.assertEqual(mapping.__getattr__("1"), "one")

    def test_recursive_merge(self):
        target = {"nested": {"a": 1}, "list": [1]}
        result = utils.merge_dict(target, {"nested": {"b": 2}, "list": [2]}, extra=True)
        self.assertIs(result, target)
        self.assertEqual(
            result, {"nested": {"a": 1, "b": 2}, "list": [2], "extra": True}
        )
        self.assertEqual(utils.merge_dict(None, {"a": 1}), {"a": 1})

    def test_sorted_dictionary(self):
        """Régression AUDIT-04 : le tri est demandé explicitement à json.dumps."""
        self.assertEqual(list(utils.sort_dict({"z": 1, "a": 2})), ["a", "z"])

    def test_sorted_dictionary_sorts_nested_mappings_without_mutation(self):
        original = {"z": {"b": 2, "a": 1}, "a": [{"d": 4, "c": 3}]}
        result = utils.sort_dict(original)
        self.assertEqual(list(result), ["a", "z"])
        self.assertEqual(list(result["z"]), ["a", "b"])
        self.assertEqual(list(result["a"][0]), ["c", "d"])
        self.assertEqual(list(original), ["z", "a"])
        self.assertEqual(list(original["z"]), ["b", "a"])

    def test_tuple_and_namedtuple_round_trip(self):
        value = {"nested": {"a": 1}, "items": [1, 2]}
        converted = utils.to_namedtuple(value)
        self.assertTrue(utils.is_namedtuple(converted))
        self.assertEqual(converted.nested.a, 1)
        self.assertEqual(
            utils.to_dict(converted), {"nested": {"a": 1}, "items": (1, 2)}
        )
        self.assertEqual(utils.to_tuple({"b": 2, "a": [1]}), (("a", (1,)), ("b", 2)))
        self.assertFalse(utils.is_namedtuple((1, 2)))
        self.assertEqual(utils.to_dict([converted])[0]["nested"], {"a": 1})

    def test_object_access(self):
        value = utils.to_object(
            {"nested": {"key": 3}, "children": [{"key": 4}]}, default="missing"
        )
        self.assertEqual(value.nested.key, 3)
        self.assertEqual(value.children[0].key, 4)
        self.assertEqual(value.absent, "missing")
        self.assertEqual(value["nested"]["key"], 3)

    def test_null_object(self):
        self.assertIs(utils.Null(), utils.null)
        self.assertFalse(utils.null)
        self.assertEqual(len(utils.null), 0)
        self.assertEqual(list(utils.null), [])
        self.assertEqual(str(utils.null), "")
        self.assertEqual(repr(utils.null), "null")
        self.assertEqual(utils.null, None)
        self.assertIs(utils.null.missing()["key"] + 2, utils.null)

    def test_cartesian_product(self):
        rows = list(
            utils.recursive_dict_product(
                {"name": "root", "items": [{"value": 1}, {"value": 2}]}, auto_id=None
            )
        )
        self.assertEqual(
            rows, [{"name": "root", "item_value": 1}, {"name": "root", "item_value": 2}]
        )

    def test_size_handles_cycles(self):
        value = []
        value.append(value)
        self.assertGreater(utils.get_size(value), 0)


class DecoratorTests(SimpleTestCase):
    def test_singleton_initializes_once(self):
        factory = Mock(return_value=object())
        singleton = utils.singleton(factory)
        self.assertIs(singleton(1), singleton(2))
        factory.assert_called_once_with(1)

    def test_timed_cache_expiration(self):
        initial = datetime(2026, 1, 1)
        with patch("common.utils.datetime") as clock:
            clock.utcnow.return_value = initial
            call = Mock(side_effect=lambda value: value * 2)
            cached = utils.timed_cache(seconds=5)(call)
            self.assertEqual(cached(3), 6)
            self.assertEqual(cached(3), 6)
            self.assertEqual(call.call_count, 1)
            clock.utcnow.return_value = initial + timedelta(seconds=6)
            self.assertEqual(cached(3), 6)
            self.assertEqual(call.call_count, 2)

    def test_synchronized_releases_lock_after_exception(self):
        lock = Lock()

        @utils.synchronized(lock)
        def fail():
            raise ValueError("test")

        with self.assertRaises(ValueError):
            fail()
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()

    def test_fake_celery_runs_each_entrypoint(self):
        @utils.CeleryFake().task()
        def add(a, b=0):
            return a + b

        self.assertEqual(add(1, 2), 3)
        self.assertEqual(add.apply(args=[1], kwargs={"b": 3}), 4)
        self.assertEqual(add.apply_async(args=[2], kwargs={"b": 3}), 5)
