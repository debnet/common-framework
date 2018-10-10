# coding: utf-8
import socket

from django.core.exceptions import PermissionDenied
from django.urls import resolve, Resolver404
from django.utils.translation import ugettext_lazy as _

from common.models import ServiceUsage
from common.settings import settings


# Ordre des métadonnées de requêtes pour l'identification de l'adresse IP du client
REQUEST_META_ORDER = (
    'HTTP_X_FORWARDED_FOR',
    'X_FORWARDED_FOR',
    'HTTP_CLIENT_IP',
    'HTTP_X_REAL_IP',
    'HTTP_X_FORWARDED',
    'HTTP_X_CLUSTER_CLIENT_IP',
    'HTTP_FORWARDED_FOR',
    'HTTP_FORWARDED',
    'HTTP_VIA',
    'REMOTE_ADDR',
)

# Liste des préfixes d'adresses IP dites "privées"
PRIVATE_IP_PREFIXES = (
    '0.',  # externally non-routable
    '10.',  # class A private block
    '169.254.',  # link-local block
    '172.16.', '172.17.', '172.18.', '172.19.',
    '172.20.', '172.21.', '172.22.', '172.23.',
    '172.24.', '172.25.', '172.26.', '172.27.',
    '172.28.', '172.29.', '172.30.', '172.31.',  # class B private blocks
    '192.0.2.',  # reserved for documentation and example code
    '192.168.',  # class C private block
    '255.255.255.',  # IPv4 broadcast address
    '2001:db8:',  # reserved for documentation and example code
    'fc00:',  # IPv6 private block
    'fe80:',  # link-local unicast
    'ff00:',  # IPv6 multicast
)

LOOPBACK_PREFIXES = (
    '127.',  # IPv4 loopback device
    '::1',  # IPv6 loopback device
)

NON_PUBLIC_IP_PREFIXES = PRIVATE_IP_PREFIXES + LOOPBACK_PREFIXES


def is_valid_ipv4(ip_str):
    """
    Vérifie qu'une adresse IPv4 est valide
    """
    try:
        socket.inet_pton(socket.AF_INET, ip_str)
    except AttributeError:
        try:  # Fall-back on legacy API or False
            socket.inet_aton(ip_str)
        except (AttributeError, socket.error):
            return False
        return ip_str.count('.') == 3
    except socket.error:
        return False
    return True


def is_valid_ipv6(ip_str):
    """
    Vérifie qu'une adresse IPv6 est valide
    """
    try:
        socket.inet_pton(socket.AF_INET6, ip_str)
    except socket.error:
        return False
    return True


def is_valid_ip(ip_str):
    """
    Vérifie qu'une adresse IP est valide
    """
    return is_valid_ipv4(ip_str) or is_valid_ipv6(ip_str)


def get_ip(request, real_ip_only=False, right_most_proxy=False):
    """
    Returns client's best-matched ip-address, or None
    """
    best_matched_ip = None
    for key in REQUEST_META_ORDER:
        value = request.META.get(key, request.META.get(key.replace('_', '-'), '')).strip()
        if value is not None and value != '':
            ips = [ip.strip().lower() for ip in value.split(',')]
            if right_most_proxy and len(ips) > 1:
                ips = reversed(ips)
            for ip_str in ips:
                if ip_str and is_valid_ip(ip_str):
                    if not ip_str.startswith(NON_PUBLIC_IP_PREFIXES):
                        return ip_str
                    if not real_ip_only:
                        loopback = LOOPBACK_PREFIXES
                        if best_matched_ip is None:
                            best_matched_ip = ip_str
                        elif best_matched_ip.startswith(loopback) and not ip_str.startswith(loopback):
                            best_matched_ip = ip_str
    return best_matched_ip


class ServiceUsageMiddleware:
    """
    Middleware des statistiques d'utilisation des services HTTP
    """

    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        response = None
        if hasattr(self, 'process_request'):
            response = self.process_request(request)
        if not response:
            response = self.get_response(request)
        if hasattr(self, 'process_response'):
            return self.process_response(request, response)
        return response

    def process_response(self, request, response):
        if settings.SERVICE_USAGE:
            try:
                request.resolver_match = getattr(request, 'resolver_match', None) or resolve(request.path)
            except Resolver404:
                return response
            if request.resolver_match and hasattr(request, 'user') and request.user.is_authenticated and \
                    response.status_code in range(200, 300):
                service_name = getattr(request.resolver_match, 'view_name', request.resolver_match)
                defaults = settings.SERVICE_USAGE_DATA.get(service_name) or settings.SERVICE_USAGE_DEFAULT or {}
                if settings.SERVICE_USAGE_LIMIT_ONLY:
                    usage = ServiceUsage.objects.filter(
                        name=service_name, user=request.user).first()
                    if not usage:
                        return response
                else:
                    usage, created = ServiceUsage.objects.get_or_create(
                        name=service_name, user=request.user, defaults=defaults)
                usage.count += 1
                usage.address = get_ip(request)
                usage.save()
                try:
                    if usage.limit and usage.limit < usage.count:
                        if usage.reset_date:
                            text = _("Le nombre maximal d'appels ({limit}) de ce service pour cet utilisateur "
                                     "({user}) a été atteint et sera réinitialisé le {date:%d/%m/%Y %H:%M:%S}.").format(
                                limit=usage.limit, user=request.user, date=usage.reset_date)
                            raise PermissionDenied(text)
                        text = _("Le nombre maximal d'appels ({limit}) de ce service pour cet utilisateur "
                                 "({user}) a été atteint et ne peut plus être utilisé.").format(
                            limit=usage.limit, user=request.user)
                        raise PermissionDenied(text)
                except PermissionDenied as exception:
                    if hasattr(response, 'data'):
                        # Django REST Framework 403
                        from rest_framework.views import exception_handler
                        from rest_framework.exceptions import PermissionDenied as ApiPermissionDenied
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
