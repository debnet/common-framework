"""Vérifie les restrictions réelles du CRUD utilisateur standard."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient


class UserCrudPermissionsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("self-user")
        self.other = get_user_model().objects.create_user("other-user")
        self.admin = get_user_model().objects.create_superuser("user-admin")
        self.client = APIClient()
        self.list_url = reverse("common-api:user-list")

    def detail(self, user):
        return reverse("common-api:user-detail", args=[user.pk])

    def test_anonymous_cannot_list_users(self):
        self.assertIn(self.client.get(self.list_url).status_code, (401, 403))

    def test_regular_user_sees_only_self(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], self.user.pk)
        self.assertNotIn("password", response.data["results"][0])

    def test_regular_user_cannot_retrieve_other_account(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(self.detail(self.other))
        self.assertIn(response.status_code, (403, 404))

    def test_view_user_permission_allows_listing_all_accounts(self):
        self.user.user_permissions.add(Permission.objects.get(codename="view_user"))
        self.client.force_authenticate(self.user)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 3)

    def test_self_update_does_not_grant_staff_or_superuser(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(
            self.detail(self.user),
            {"first_name": "Updated", "is_staff": True, "is_superuser": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Updated")
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)

    def test_regular_user_cannot_modify_other_account(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(
            self.detail(self.other), {"first_name": "Unexpected"}, format="json"
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.other.refresh_from_db()
        self.assertEqual(self.other.first_name, "")

    def test_administrator_can_list_all_accounts(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 3)

    def test_anonymous_registration_does_not_grant_privileges(self):
        response = self.client.post(
            self.list_url,
            {
                "username": "registered-user",
                "password": "A-strong-test-password-42!",
                "is_staff": True,
                "is_superuser": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        user = get_user_model().objects.get(username="registered-user")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertNotIn("password", response.data)
