from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from common.api.pagination import CustomPageNumberPagination, unpaginated_requested
from common.api.utils import (
    api_paginate,
    api_view_with_serializer,
    create_model_serializer_and_viewset,
)
from common.models import Webhook


@override_settings(API_MAX_PAGE_SIZE=25)
class PaginationTests(SimpleTestCase):
    def request(self, params=None, admin=False):
        request = Request(APIRequestFactory().get("/", params or {}))
        request.user = SimpleNamespace(
            is_authenticated=True, is_staff=admin, is_superuser=False
        )
        return request

    def test_page_size_is_capped(self):
        paginator = CustomPageNumberPagination()
        self.assertEqual(paginator.get_page_size(self.request({"page_size": 100})), 25)
        self.assertEqual(paginator.get_page_size(self.request({"page_size": 5})), 5)

    def test_default_page_size_is_capped(self):
        paginator = CustomPageNumberPagination()
        paginator.page_size = 100
        self.assertEqual(paginator.get_page_size(self.request()), 25)

    def test_missing_default_does_not_disable_pagination(self):
        paginator = CustomPageNumberPagination()
        paginator.page_size = None
        self.assertEqual(paginator.get_page_size(self.request()), 25)

    def test_invalid_page_size_does_not_bypass_limit(self):
        for value in ("0", "-1", "invalid"):
            size = CustomPageNumberPagination().get_page_size(
                self.request({"page_size": value})
            )
            self.assertGreater(size, 0)
            self.assertLessEqual(size, 25)

    def test_administrators_can_exceed_limit(self):
        self.assertEqual(
            CustomPageNumberPagination().get_page_size(
                self.request({"page_size": 100}, admin=True)
            ),
            100,
        )
        request = self.request({"page_size": 100})
        request.user.is_superuser = True
        self.assertEqual(CustomPageNumberPagination().get_page_size(request), 100)

    def test_all_requires_administrator(self):
        with self.assertRaises(PermissionDenied):
            unpaginated_requested(self.request(), "true")
        self.assertTrue(unpaginated_requested(self.request(admin=True), "true"))
        self.assertFalse(unpaginated_requested(self.request(), "false"))

    @override_settings(API_MAX_PAGE_SIZE=0)
    def test_invalid_limit_fails_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            CustomPageNumberPagination().get_page_size(self.request())


PaginationSerializer, PaginationViewSet = create_model_serializer_and_viewset(
    Webhook, hyperlinked=False
)


@api_view_with_serializer(["GET"])
def paginated_webhooks(request):
    return api_paginate(request, Webhook.objects.all(), PaginationSerializer)


@override_settings(API_MAX_PAGE_SIZE=2)
class PaginationIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("pagination")
        cls.user.user_permissions.add(Permission.objects.get(codename="view_webhook"))
        cls.admin = get_user_model().objects.create_superuser("pagination-admin")
        for index in range(3):
            Webhook.objects.create(name=str(index), url="https://example.com/hook")

    def call(self, endpoint, user, params):
        request = APIRequestFactory().get("/", params)
        force_authenticate(request, user=user)
        view = (
            PaginationViewSet.as_view({"get": "list"})
            if endpoint == "viewset"
            else paginated_webhooks
        )
        return view(request)

    def test_viewsets_and_api_paginate_enforce_limit(self):
        for endpoint in ("viewset", "paginate"):
            with self.subTest(endpoint=endpoint):
                response = self.call(endpoint, self.user, {"page_size": 100})
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data["page_size"], 2)
                self.assertEqual(len(response.data["results"]), 2)
                self.assertEqual(response.data["count"], 3)

    def test_administrators_can_exceed_limit_on_both_endpoints(self):
        for endpoint in ("viewset", "paginate"):
            with self.subTest(endpoint=endpoint):
                response = self.call(endpoint, self.admin, {"page_size": 100})
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(len(response.data["results"]), 3)

    def test_all_cannot_bypass_limit_on_either_endpoint(self):
        for endpoint in ("viewset", "paginate"):
            with self.subTest(endpoint=endpoint):
                response = self.call(endpoint, self.user, {"all": "true"})
                self.assertEqual(response.status_code, 403, response.data)
                response = self.call(endpoint, self.admin, {"all": "true"})
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(len(response.data), 3)
