from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.exceptions import ValidationError as ApiValidationError

from common.api.serializers import BaseCommonModelSerializer
from common.models import Serialized, Webhook, from_dict


class CommonModelTests(TestCase):
    def setUp(self):
        self.entity = Webhook.objects.create(name="Original", url="https://example.com")

    def test_instance_serialization_round_trip(self):
        serialized = self.entity.serialize()
        self.assertTrue(serialized.single)
        self.assertEqual(serialized.count, 1)
        restored = serialized.deserialize()
        self.assertEqual(restored.pk, self.entity.pk)
        self.assertEqual(restored.name, "Original")
        self.assertEqual(str(serialized), serialized.data)
        self.assertIn("Webhook", repr(serialized))

    def test_queryset_serialization_round_trip(self):
        serialized = Webhook.objects.all().serialize()
        self.assertIsInstance(serialized, Serialized)
        self.assertFalse(serialized.single)
        self.assertIn("SELECT", serialized.query)
        self.assertEqual(
            [item.pk for item in serialized.deserialize()], [self.entity.pk]
        )

    def test_queryset_and_instance_dictionary(self):
        value = self.entity.to_dict(includes=["id", "name"])
        self.assertEqual(value, {"id": self.entity.pk, "name": "Original"})
        self.assertEqual(
            Webhook.objects.all().to_dict(includes=["id", "name"]), [value]
        )

    def test_only_modified_fields_are_persisted(self):
        loaded = Webhook.objects.get(pk=self.entity.pk)
        Webhook.objects.filter(pk=loaded.pk).update(timeout=99)
        loaded.name = "Changed"
        loaded.save()
        loaded.refresh_from_db()
        self.assertEqual(loaded.name, "Changed")
        self.assertEqual(loaded.timeout, 99)

    def test_full_update_can_be_requested_explicitly(self):
        Webhook.objects.filter(pk=self.entity.pk).update(timeout=99)
        self.entity.name = "Changed"
        self.entity.save(_full_update=True)
        self.entity.refresh_from_db()
        self.assertEqual(self.entity.timeout, 30)

    def test_from_dict_builds_model_without_persisting(self):
        value = from_dict(
            {"name": "Unsaved", "url": "https://example.com"}, model=Webhook
        )
        self.assertIsInstance(value, Webhook)
        self.assertIsNone(value.pk)
        self.assertEqual(value.name, "Unsaved")
        self.assertEqual(Webhook.objects.count(), 1)

    def test_model_validation_rejects_invalid_url(self):
        self.entity.url = "not-a-url"
        with self.assertRaises(ValidationError):
            self.entity.full_clean()


class ModelSerializerValidationTests(TestCase):
    def serializer(self):
        class Serializer(BaseCommonModelSerializer):
            class Meta:
                model = Webhook
                fields = ["id", "name", "url"]

        return Serializer()

    def test_create_runs_model_validation(self):
        with self.assertRaises(ApiValidationError):
            self.serializer().create({"name": "invalid", "url": "not-a-url"})
        self.assertFalse(Webhook.objects.exists())

    def test_update_runs_model_validation(self):
        instance = Webhook.objects.create(name="Original", url="https://example.com")
        with self.assertRaises(ApiValidationError):
            self.serializer().update(instance, {"url": "not-a-url"})
        instance.refresh_from_db()
        self.assertEqual(instance.url, "https://example.com")
