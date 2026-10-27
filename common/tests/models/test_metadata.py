"""Métadonnées réelles en base, sans mocks de l'ORM."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils.timezone import now

from common.models import GroupMetaData, MetaData, UserMetaData, Webhook


class MetadataTests(TestCase):
    def setUp(self):
        self.entity = Webhook.objects.create(name="Metadata", url="https://example.com")

    def test_set_updates_instead_of_duplicating(self):
        first = self.entity.set_metadata("setting", {"value": False})
        second = self.entity.set_metadata("setting", {"value": 0})
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.entity.get_metadata("setting"), {"value": 0})
        self.assertEqual(self.entity.metadata.count(), 1)

    def test_raw_and_missing_metadata(self):
        self.assertIsNone(self.entity.get_metadata("missing"))
        self.entity.set_metadata("key", "value")
        self.assertIsInstance(self.entity.get_metadata("key", raw=True), MetaData)
        self.assertEqual(
            list(self.entity.get_metadata(raw=True).values_list("key", flat=True)),
            ["key"],
        )

    def test_expired_metadata_can_be_read_explicitly(self):
        self.entity.set_metadata("old", "value", date=now() - timedelta(days=1))
        self.assertEqual(self.entity.get_metadata(), {})
        self.assertEqual(self.entity.get_metadata(valid=False), {"old": "value"})
        self.assertEqual(self.entity.metadata.select_valid(valid=False).count(), 1)
        self.assertEqual(self.entity.metadata.select_valid(valid=None).count(), 1)

    def test_logical_and_physical_removal(self):
        self.entity.set_metadata("first", 1)
        self.entity.set_metadata("second", 2)
        self.entity.del_metadata("first", logic=True)
        self.assertEqual(self.entity.get_metadata(), {"second": 2})
        self.assertEqual(self.entity.metadata.count(), 2)
        self.entity.del_metadata("second")
        self.assertEqual(self.entity.metadata.count(), 1)
        self.entity.del_metadata()
        self.assertFalse(self.entity.metadata.exists())

    def test_add_merges_dictionaries_and_lists(self):
        self.entity.set_metadata("mapping", {"a": 1})
        self.entity.add_metadata("mapping", {"b": 2})
        self.assertEqual(self.entity.get_metadata("mapping"), {"a": 1, "b": 2})
        self.entity.add_metadata("list", "a")
        self.entity.add_metadata("list", ["b", "c"])
        self.assertEqual(self.entity.get_metadata("list"), ["a", "b", "c"])
        self.entity.set_metadata("number", 2)
        self.entity.add_metadata("number", 3)
        self.assertEqual(self.entity.get_metadata("number"), 5)

    def test_search_by_entity_type_and_key(self):
        self.entity.set_metadata("key", "value")
        self.assertEqual(
            MetaData.objects.search(id=self.entity.pk, type=Webhook, key="key").count(),
            1,
        )
        content_type_id = self.entity.metadata.get().content_type_id
        self.assertEqual(MetaData.objects.search(type=content_type_id).count(), 1)

    def test_unsaved_entity_is_rejected(self):
        with self.assertRaises(AssertionError):
            Webhook().set_metadata("key", "value")


class AccountMetadataTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("metadata-account")
        self.group = Group.objects.create(name="metadata-group")
        self.user.groups.add(self.group)

    def test_group_defaults_are_overridden_by_user_values(self):
        GroupMetaData.set(self.group, theme="light", nested={"group": True})
        UserMetaData.set(self.user, theme="dark", nested={"user": True})
        self.assertEqual(
            UserMetaData.get(self.user),
            {"theme": "dark", "nested": {"group": True, "user": True}},
        )
        self.assertEqual(UserMetaData.get(self.user, "theme"), "dark")
        self.assertEqual(
            UserMetaData.get(self.user, groups=False),
            {"theme": "dark", "nested": {"user": True}},
        )

    def test_user_merge_and_remove(self):
        UserMetaData.set(self.user, nested={"a": 1}, remove=True)
        UserMetaData.merge(self.user, {"nested": {"b": 2}}, extra=3)
        self.assertEqual(
            UserMetaData.get(self.user, groups=False)["nested"], {"a": 1, "b": 2}
        )
        UserMetaData.remove(self.user, "remove")
        self.assertNotIn("remove", UserMetaData.get(self.user))

    def test_group_merge_and_remove(self):
        GroupMetaData.set(self.group, nested={"a": 1}, remove=True)
        GroupMetaData.merge(self.group, {"nested": {"b": 2}})
        self.assertEqual(GroupMetaData.get(self.group, "nested"), {"a": 1, "b": 2})
        GroupMetaData.remove(self.group, "remove")
        self.assertNotIn("remove", GroupMetaData.get(self.group))
