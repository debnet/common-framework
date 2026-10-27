# coding: utf-8
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from common.api.api_views import metadata, user_infos
from common.models import Global, Webhook


@override_settings(
    REST_FRAMEWORK={
        "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"]
    }
)
class UtilityViewsPermissionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("reader")
        cls.other = get_user_model().objects.create_user("other")
        cls.staff = get_user_model().objects.create_user("staff", is_staff=True)
        cls.superuser = get_user_model().objects.create_user("admin", is_superuser=True)
        cls.entity = Webhook.objects.create(
            name="Permissions", url="https://example.com/hook"
        )
        cls.uid = uuid.uuid4()
        Global.objects.create(
            content_type=ContentType.objects.get_for_model(Webhook),
            object_id=cls.entity.pk,
            object_uid=cls.uid,
        )
        cls.entity.set_metadata("existing", "before")

    def call(self, view, user=None, data=None, **kwargs):
        factory = APIRequestFactory()
        request = (
            factory.get("/") if data is None else factory.post("/", data, format="json")
        )
        if user is not None:
            force_authenticate(request, user=user)
        return view(request, **kwargs)

    def grant(self, *codenames):
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="common", codename__in=codenames
            )
        )
        self.user = get_user_model().objects.get(pk=self.user.pk)

    def test_user_can_read_only_self(self):
        for kwargs in ({}, {"user_id": self.user.pk}):
            response = self.call(user_infos, self.user, **kwargs)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["id"], self.user.pk)
        for user_id in (self.other.pk, 999999):
            with patch("common.api.api_views.get_object_or_404") as lookup:
                response = self.call(user_infos, self.user, user_id=user_id)
                self.assertEqual(response.status_code, 403)
                lookup.assert_not_called()

    def test_administrators_can_read_other_users(self):
        for admin in (self.staff, self.superuser):
            response = self.call(user_infos, admin, user_id=self.other.pk)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["id"], self.other.pk)

    def test_user_infos_does_not_expose_password_through_crud_parameters(self):
        self.user.set_password("Private-password-for-test-42!")
        self.user.save(update_fields=["password"])
        for actor in (self.user, self.staff, self.superuser):
            request = APIRequestFactory().get(
                "/", {"fields": "password", "min": "password", "simple": "true"}
            )
            force_authenticate(request, user=actor)
            response = user_infos(request, user_id=self.user.pk)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertNotIn("password", response.data)
            self.assertNotIn("min__password", response.data)
            self.assertNotIn(self.user.password, str(response.data))

    def test_authentication_is_required_even_with_allow_any_defaults(self):
        for view, kwargs in ((user_infos, {}), (metadata, {"uuid": self.uid})):
            response = self.call(view, **kwargs)
            self.assertIn(response.status_code, (401, 403))

    def test_metadata_read_requires_view_permission_before_lookup(self):
        with patch("common.api.api_views.Global.objects.from_uuid") as lookup:
            response = self.call(metadata, self.user, uuid=self.uid)
            self.assertEqual(response.status_code, 403)
            lookup.assert_not_called()
        self.grant("view_metadata")
        response = self.call(metadata, self.user, uuid=self.uid)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"existing": "before"})

    def test_metadata_write_requires_permission_for_each_operation(self):
        self.grant("view_metadata")
        for payload in ({"new": "value"}, {"existing": "after"}, {"existing": None}):
            response = self.call(metadata, self.user, data=payload, uuid=self.uid)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(self.entity.get_metadata(), {"existing": "before"})
        self.grant("add_metadata")
        response = self.call(metadata, self.user, data={"new": "value"}, uuid=self.uid)
        self.assertEqual(response.status_code, 200)
        self.grant("change_metadata")
        response = self.call(
            metadata, self.user, data={"existing": "after"}, uuid=self.uid
        )
        self.assertEqual(response.status_code, 200)
        self.grant("delete_metadata")
        response = self.call(
            metadata, self.user, data={"existing": None}, uuid=self.uid
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.entity.get_metadata(), {"new": "value"})

    def test_missing_permission_does_not_allow_partial_writes(self):
        self.grant("view_metadata", "add_metadata")
        response = self.call(
            metadata, self.user, data={"new": "value", "existing": None}, uuid=self.uid
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.entity.get_metadata(), {"existing": "before"})

    def test_write_response_requires_view_permission(self):
        self.grant("add_metadata", "change_metadata", "delete_metadata")
        response = self.call(metadata, self.user, data={"new": "value"}, uuid=self.uid)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.entity.get_metadata(), {"existing": "before"})

    def test_invalid_payloads_cannot_delete_all_metadata(self):
        self.grant(
            "view_metadata", "add_metadata", "change_metadata", "delete_metadata"
        )
        for payload in (["existing"], {"": None}, {"x" * 101: "value"}):
            response = self.call(metadata, self.user, data=payload, uuid=self.uid)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.entity.get_metadata(), {"existing": "before"})

    def test_unknown_uuid_returns_not_found_for_authorized_user(self):
        self.grant("view_metadata")
        response = self.call(metadata, self.user, uuid=uuid.uuid4())
        self.assertEqual(response.status_code, 404)
