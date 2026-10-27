from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase

from common.api.serializers import UserInfosSerializer, UserSerializer


class UserSerializerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("relations")
        self.group = Group.objects.create(name="group")
        self.permission = Permission.objects.first()
        self.user.groups.add(self.group)
        self.user.user_permissions.add(self.permission)

    def test_empty_lists_clear_relations(self):
        serializer = UserSerializer(
            self.user, data={"groups": [], "user_permissions": []}, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.assertFalse(self.user.groups.exists())
        self.assertFalse(self.user.user_permissions.exists())

    def test_omitted_relations_are_preserved(self):
        serializer = UserSerializer(
            self.user, data={"first_name": "Updated"}, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.assertEqual(list(self.user.groups.all()), [self.group])
        self.assertEqual(list(self.user.user_permissions.all()), [self.permission])

    def test_user_infos_excludes_password_completely(self):
        self.user.set_password("Private-password-for-test-42!")
        serializer = UserInfosSerializer(self.user)
        self.assertNotIn("password", serializer.fields)
        self.assertNotIn("password", serializer.data)
        self.assertNotIn(self.user.password, str(serializer.data))

    def test_password_update_remains_write_only(self):
        password = "Private-password-for-test-42!"
        serializer = UserSerializer(
            self.user, data={"password": password}, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(password))
        self.assertNotIn("password", serializer.data)
        self.assertNotIn(self.user.password, str(serializer.data))
