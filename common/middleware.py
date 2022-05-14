# coding: utf-8
from django.core.exceptions import PermissionDenied
from django.urls import Resolver404, resolve
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from common.models import ServiceUsage
from common.settings import settings
from common.utils import get_client_ip


class ServiceUsageMiddleware:
    """
    Middleware des statistiques d'utilisation des services HTTP
    """

    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        response = None
        if hasattr(self, "process_request"):
            response = self.process_request(request)
        if not response:
            response = self.get_response(request)
        if hasattr(self, "process_response"):
            return self.process_response(request, response)
        return response

    def process_response(self, request, response):
        if settings.SERVICE_USAGE:
            try:
                request.resolver_match = getattr(request, "resolver_match", None) or resolve(request.path)
            except Resolver404:
                return response
            if (
                request.resolver_match
                and hasattr(request, "user")
                and request.user.is_authenticated
                and response.status_code in range(200, 300)
            ):
                service_name = getattr(request.resolver_match, "view_name", request.resolver_match)
                defaults = settings.SERVICE_USAGE_DATA.get(service_name) or settings.SERVICE_USAGE_DEFAULT or {}
                if settings.SERVICE_USAGE_LIMIT_ONLY:
                    usage = ServiceUsage.objects.filter(name=service_name, user=request.user).first()
                    if not usage:
                        return response
                else:
                    usage, created = ServiceUsage.objects.get_or_create(
                        name=service_name, user=request.user, defaults=defaults
                    )
                date = now()
                usage.count += 1
                usage.address = get_client_ip(request)
                extra = usage.extra or dict(addresses={}, data={}, params={})
                address = extra["addresses"].setdefault(usage.address, {})
                address.update(date=date, method=request.method, count=address.get("count", 0) + 1)
                if settings.SERVICE_USAGE_LOG_DATA:
                    for method in ("GET", "POST"):
                        for key, value in getattr(request, method, {}).items():
                            if not value:
                                continue
                            data = extra["data"].setdefault(key, {})
                            data.update(date=date, method=method, count=data.get("count", 0) + 1)
                    for key, value in request.resolver_match.kwargs.items():
                        params = extra["params"].setdefault(key, {})
                        params.update(date=date, method=request.method, count=params.get("count", 0) + 1)
                usage.extra = extra
                usage.save()
                try:
                    if usage.limit and usage.limit < usage.count:
                        if usage.reset_date:
                            text = _(
                                "Le nombre maximal d'appels ({limit}) de ce service pour cet utilisateur "
                                "({user}) a été atteint et sera réinitialisé le {date:%d/%m/%Y %H:%M:%S}."
                            ).format(limit=usage.limit, user=request.user, date=usage.reset_date)
                            raise PermissionDenied(text)
                        text = _(
                            "Le nombre maximal d'appels ({limit}) de ce service pour cet utilisateur "
                            "({user}) a été atteint et ne peut plus être utilisé."
                        ).format(limit=usage.limit, user=request.user)
                        raise PermissionDenied(text)
                except PermissionDenied as exception:
                    if hasattr(response, "data"):
                        # Django REST Framework 403
                        from rest_framework.exceptions import PermissionDenied as ApiPermissionDenied
                        from rest_framework.views import exception_handler

                        api_response = exception_handler(ApiPermissionDenied(exception), None)
                        api_response.accepted_renderer = response.accepted_renderer
                        api_response.accepted_media_type = response.accepted_media_type
                        api_response.renderer_context = response.renderer_context
                        api_response.exception = True
                        api_response.render()
                        return api_response
                    else:
                        raise
        return response
