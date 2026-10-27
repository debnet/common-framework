# coding: utf-8
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import connection, connections
from django.http import JsonResponse
from django.test import (
    TestCase,
    TransactionTestCase,
    override_settings,
    skipUnlessDBFeature,
)
from django.urls import path, resolve
from django.utils.timezone import now
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory

from common.api.permissions import ServiceUsagePermission
from common.api.utils import (
    api_view_with_serializer,
    create_model_serializer_and_viewset,
)
from common.middleware import ServiceUsageMiddleware
from common.models import ServiceUsage, Webhook
from common.utils import reserve_service_usage

effects = Mock()


def django_action(request):
    effects()
    return JsonResponse({"ok": True})


def django_failure(request):
    effects()
    return JsonResponse({"error": True}, status=500)


@api_view(["GET"])
@permission_classes([IsAuthenticated, ServiceUsagePermission])
def token_action(request):
    effects()
    return Response({"ok": True})


@api_view_with_serializer(["GET"])
@permission_classes([IsAuthenticated])
def common_action(request):
    effects()
    return Response({"ok": True})


Serializer, ViewSet = create_model_serializer_and_viewset(Webhook, hyperlinked=False)

urlpatterns = [
    path("django/", django_action, name="django-action"),
    path("failure/", django_failure, name="django-failure"),
    path("token/", token_action, name="token-action"),
    path("common/", common_action, name="common-action"),
    path("viewset/", ViewSet.as_view({"get": "list"}), name="common-viewset"),
]


@override_settings(SERVICE_USAGE=True, ROOT_URLCONF=__name__)
class ServiceUsageTests(TestCase):
    def setUp(self):
        effects.reset_mock()
        self.user = get_user_model().objects.create_user("quota-user")

    def request(self, user=None, params=None):
        request = APIRequestFactory().get("/django/", params or {})
        request.user = user or self.user
        request.resolver_match = resolve(request.path)
        return request

    def usage(self, **kwargs):
        return ServiceUsage.objects.create(
            name="django-action", user=self.user, address="", **kwargs
        )

    def test_limit_blocks_before_view_and_does_not_increment(self):
        usage = self.usage(limit=1)
        middleware = ServiceUsageMiddleware(django_action)
        first = self.request()
        middleware.process_view(first, django_action, (), {})
        middleware(first)
        with self.assertRaises(PermissionDenied):
            middleware.process_view(self.request(), django_action, (), {})
        usage.refresh_from_db()
        self.assertEqual(usage.count, 1)
        effects.assert_called_once()

    def test_zero_limit_denies_all_calls(self):
        usage = self.usage(limit=0)
        with self.assertRaises(PermissionDenied):
            reserve_service_usage(self.request())
        usage.refresh_from_db()
        self.assertEqual(usage.count, 0)

    def test_unlimited_usage_and_request_deduplication(self):
        request = self.request()
        reserve_service_usage(request)
        reserve_service_usage(request)
        usage = ServiceUsage.objects.get(user=self.user)
        self.assertEqual(usage.count, 1)
        reserve_service_usage(self.request())
        usage.refresh_from_db()
        self.assertEqual(usage.count, 2)
        self.assertEqual(
            sum(item["count"] for item in usage.extra["addresses"].values()), 2
        )

    def test_expired_counter_resets_before_reservation(self):
        usage = self.usage(limit=1, reset=ServiceUsage.RESET_HOURLY)
        ServiceUsage.objects.filter(pk=usage.pk).update(
            count=1, reset_date=now() - timedelta(seconds=1)
        )
        reserve_service_usage(self.request())
        usage.refresh_from_db()
        self.assertEqual(usage.count, 1)
        self.assertGreater(usage.reset_date, now())

    def test_unexpired_counter_is_not_reset(self):
        usage = self.usage(
            limit=1,
            count=1,
            reset=ServiceUsage.RESET_HOURLY,
            reset_date=now() + timedelta(hours=1),
        )
        with self.assertRaises(PermissionDenied):
            reserve_service_usage(self.request())
        usage.refresh_from_db()
        self.assertEqual(usage.count, 1)

    @override_settings(SERVICE_USAGE_DEFAULT={"limit": 1})
    def test_defaults_apply_to_new_usage(self):
        reserve_service_usage(self.request())
        with self.assertRaises(PermissionDenied):
            reserve_service_usage(self.request())

    @override_settings(SERVICE_USAGE_LIMIT_ONLY=True)
    def test_limit_only_does_not_create_rows(self):
        reserve_service_usage(self.request())
        self.assertFalse(ServiceUsage.objects.exists())
        self.usage(limit=0)
        with self.assertRaises(PermissionDenied):
            reserve_service_usage(self.request())

    @override_settings(SERVICE_USAGE=False)
    def test_disabled_usage_does_not_query(self):
        with self.assertNumQueries(0):
            reserve_service_usage(self.request())

    def test_anonymous_usage_does_not_query(self):
        with self.assertNumQueries(0):
            reserve_service_usage(self.request(user=AnonymousUser()))

    @override_settings(SERVICE_USAGE_LOG_DATA=True)
    def test_statistics_store_keys_but_not_payload_values(self):
        reserve_service_usage(self.request(params={"filter": "private-value"}))
        usage = ServiceUsage.objects.get(user=self.user)
        self.assertEqual(usage.extra["data"]["filter"]["count"], 1)
        self.assertNotIn("private-value", str(usage.extra))


@override_settings(
    SERVICE_USAGE=True,
    SERVICE_USAGE_DEFAULT={"limit": 1},
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "common.middleware.ServiceUsageMiddleware",
    ],
)
class ServiceUsageIntegrationTests(TestCase):
    def setUp(self):
        effects.reset_mock()
        self.user = get_user_model().objects.create_superuser("quota-admin")
        self.token, _ = Token.objects.get_or_create(user=self.user)

    def test_django_request_is_rejected_before_side_effect(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/django/").status_code, 200)
        self.assertEqual(self.client.get("/django/").status_code, 403)
        effects.assert_called_once()
        self.assertEqual(ServiceUsage.objects.get().count, 1)

    def test_failed_view_still_consumes_quota(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/failure/").status_code, 500)
        self.assertEqual(self.client.get("/failure/").status_code, 403)
        effects.assert_called_once()
        self.assertEqual(ServiceUsage.objects.get().count, 1)

    def test_drf_token_and_common_api_views_enforce_quotas(self):
        for url in ("/token/", "/common/", "/viewset/"):
            with self.subTest(url=url):
                first = self.client.get(
                    url, HTTP_AUTHORIZATION="Token " + self.token.key
                )
                second = self.client.get(
                    url, HTTP_AUTHORIZATION="Token " + self.token.key
                )
                self.assertEqual(first.status_code, 200, first.content)
                self.assertEqual(second.status_code, 403, second.content)
                usage = ServiceUsage.objects.get(
                    name=resolve(url).view_name, user=self.user
                )
                self.assertEqual(usage.count, 1)

    def test_drf_session_is_not_counted_twice(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/token/").status_code, 200)
        self.assertEqual(ServiceUsage.objects.get().count, 1)
        self.assertEqual(self.client.get("/token/").status_code, 403)
        effects.assert_called_once()

    def test_failed_authentication_does_not_consume_quota(self):
        response = self.client.get("/token/", HTTP_AUTHORIZATION="Token invalid")
        self.assertIn(response.status_code, (401, 403))
        self.assertFalse(ServiceUsage.objects.exists())
        effects.assert_not_called()


@override_settings(SERVICE_USAGE=True, ROOT_URLCONF=__name__)
class ServiceUsageTransactionsTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("concurrent-quota")
        self.usage = ServiceUsage.objects.create(
            name="django-action", user=self.user, address="", limit=1
        )

    def request(self, user):
        request = APIRequestFactory().get("/django/")
        request.user = user
        request.resolver_match = resolve(request.path)
        return request

    def test_reservation_finishes_transaction_before_view(self):
        reserve_service_usage(self.request(self.user))
        self.assertFalse(connection.in_atomic_block)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_requests_cannot_both_reserve_last_slot(self):
        barrier = Barrier(2)
        user_pk = self.user.pk

        def reserve(_):
            try:
                user = get_user_model().objects.get(pk=user_pk)
                request = self.request(user)
                barrier.wait(timeout=10)
                try:
                    reserve_service_usage(request)
                    return True
                except PermissionDenied:
                    return False
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.usage.refresh_from_db()
        self.assertEqual(self.usage.count, 1)
