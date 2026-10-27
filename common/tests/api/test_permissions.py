# coding: utf-8
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db.models import QuerySet
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from common.api.utils import (
    api_paginate,
    api_view_with_serializer,
    create_model_serializer_and_viewset,
)

Serializer, ViewSet = create_model_serializer_and_viewset(LogEntry, hyperlinked=False)


@api_view_with_serializer(["GET"])
def paginated_entries(request):
    return api_paginate(request, LogEntry.objects.all(), Serializer)


@override_settings(ENABLE_API_PERMISSIONS=True)
class AggregatePermissionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("reader")
        cls.other = get_user_model().objects.create_user("other")
        cls.user.user_permissions.add(Permission.objects.get(codename="view_logentry"))
        cls.group = Group.objects.create(name="test group")
        cls.user.groups.add(cls.group)
        for user in (cls.user, cls.other):
            LogEntry.objects.create(user=user, object_repr="entry", action_flag=1)

    def call(self, endpoint, params):
        request = APIRequestFactory().get("/entries/", params)
        force_authenticate(request, user=get_user_model().objects.get(pk=self.user.pk))
        view = (
            ViewSet.as_view({"get": "list"})
            if endpoint == "viewset"
            else paginated_entries
        )
        return view(request)

    def grant(self, codename):
        self.user.user_permissions.add(Permission.objects.get(codename=codename))

    def test_related_aggregate_is_denied_before_execution_even_when_silent(self):
        for endpoint in ("viewset", "paginate"):
            for silent in ("false", "true"):
                with (
                    self.subTest(endpoint=endpoint, silent=silent),
                    patch.object(QuerySet, "aggregate") as aggregate,
                ):
                    response = self.call(
                        endpoint, {"min": "user.password", "silent": silent}
                    )
                    self.assertEqual(response.status_code, 403, response.data)
                    self.assertIn("view_user", response.data)
                    aggregate.assert_not_called()

    def test_related_aggregate_is_allowed_with_model_permission(self):
        self.grant("view_user")
        for endpoint in ("viewset", "paginate"):
            with self.subTest(endpoint=endpoint):
                response = self.call(endpoint, {"min": "user.username"})
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data, {"min__user__username": "other"})

    def test_local_aggregate_does_not_require_related_permissions(self):
        for endpoint in ("viewset", "paginate"):
            with self.subTest(endpoint=endpoint):
                response = self.call(endpoint, {"count": "id"})
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data, {"count__id": 2})

    def test_models_introduced_by_filters_are_checked(self):
        for endpoint in ("viewset", "paginate"):
            with self.subTest(endpoint=endpoint):
                response = self.call(
                    endpoint, {"count": "id", "user__username": "reader"}
                )
                self.assertEqual(response.status_code, 403, response.data)

    def test_all_models_on_a_multi_hop_path_are_checked(self):
        self.grant("view_user")
        for endpoint in ("viewset", "paginate"):
            with self.subTest(endpoint=endpoint):
                response = self.call(endpoint, {"min": "user.groups.name"})
                self.assertEqual(response.status_code, 403, response.data)
                self.assertIn("view_group", response.data)
        self.grant("view_group")
        for endpoint in ("viewset", "paginate"):
            with self.subTest(endpoint=endpoint, permitted=True):
                response = self.call(endpoint, {"min": "user.groups.name"})
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(
                    response.data, {"min__user__groups__name": self.group.name}
                )

    def test_aggregate_alias_can_match_an_existing_model_field(self):
        self.grant("view_user")
        for endpoint in ("viewset", "paginate"):
            with self.subTest(endpoint=endpoint):
                response = self.call(endpoint, {"min": "user.id|id"})
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(
                    response.data, {"id": min(self.user.pk, self.other.pk)}
                )

    def test_empty_result_does_not_skip_permission_checks(self):
        for endpoint in ("viewset", "paginate"):
            with self.subTest(endpoint=endpoint):
                response = self.call(endpoint, {"min": "user.password", "id": -1})
                self.assertEqual(response.status_code, 403, response.data)

    def test_grouped_aggregate_still_checks_permissions(self):
        for endpoint in ("viewset", "paginate"):
            with self.subTest(endpoint=endpoint):
                response = self.call(
                    endpoint, {"group_by": "user.username", "count": "id"}
                )
                self.assertEqual(response.status_code, 403, response.data)

    @override_settings(ENABLE_API_PERMISSIONS=False)
    def test_permission_checks_can_still_be_disabled_explicitly(self):
        for endpoint in ("viewset", "paginate"):
            with self.subTest(endpoint=endpoint):
                response = self.call(endpoint, {"min": "user.username"})
                self.assertEqual(response.status_code, 200, response.data)
