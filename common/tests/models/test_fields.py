import pickle
from decimal import Decimal

from django.test import SimpleTestCase

from common.fields import CustomDecimalField, JsonField, PickleField


class PickleFieldTests(SimpleTestCase):
    def test_falsy_values_round_trip(self):
        for nullable in (False, True):
            field = PickleField(null=nullable)
            for value in (False, 0, 0.0, [], {}, (), "", b""):
                with self.subTest(nullable=nullable, value=repr(value)):
                    restored = field.from_db_value(field.get_prep_value(value))
                    self.assertEqual(restored, value)
                    self.assertIs(type(restored), type(value))

    def test_nullable_none(self):
        field = PickleField(null=True)
        self.assertIsNone(field.get_prep_value(None))
        self.assertIsNone(field.to_python(None))

    def test_prepared_and_python_values(self):
        field = PickleField()
        self.assertEqual(field.get_prep_value(pickle.dumps(False)), pickle.dumps(False))
        for value in (False, 0, [], {}, "", b""):
            self.assertEqual(field.to_python(value), value)


class ModelFieldTests(SimpleTestCase):
    def test_custom_decimal_normalization(self):
        field = CustomDecimalField(max_digits=8, decimal_places=3)
        self.assertEqual(field._transform_decimal(Decimal("100.000")), Decimal("100"))
        self.assertEqual(str(field._transform_decimal(Decimal("1.250"))), "1.25")

    def test_native_json_encoder_and_lookups(self):
        field = JsonField()
        self.assertTrue(field.is_django)
        self.assertEqual(field.encoder().encode({"a": Decimal("1.25")}), '{"a": 1.25}')
        for name in ("has", "hasall", "hasany", "hasdict", "indict", "isempty"):
            self.assertIsNotNone(field.get_lookup(name))
