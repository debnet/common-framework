# coding: utf-8
"""Pagination bornée et autorisation explicite des exports sans pagination."""

from collections import OrderedDict

from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param

from common.settings import settings
from common.utils import str_to_bool


def is_pagination_admin(request):
    """Indique si l'acteur authentifié est staff ou superuser pour les exports."""
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def unpaginated_requested(request, value):
    """Interprète all et refuse le mode non paginé hors administration.

    Renvoie False pour une valeur non vraie, True pour un administrateur ;
    lève PermissionDenied sinon. Ne dispense pas des permissions sur la ressource.
    """
    if not str_to_bool(value):
        return False
    if not is_pagination_admin(request):
        raise PermissionDenied("Unpaginated results are restricted to administrators.")
    return True


class CustomPageNumberPagination(PageNumberPagination):
    """
    Pagination personnalisée pour les API et les API views
    """

    page_query_param = "page"
    page_size_query_param = "page_size"
    _query_params = [page_query_param, page_size_query_param]
    additional_data = {}

    def get_page_size(self, request):
        """Borne la taille demandée/définie par défaut avec API_MAX_PAGE_SIZE.

        Sans taille DRF, le plafond devient la taille pour les non-admins au lieu
        de désactiver la pagination. Les admins ne sont pas bornés par ce setting.
        Un plafond non entier ou non positif lève ImproperlyConfigured.
        """
        page_size = super().get_page_size(request)
        if is_pagination_admin(request):
            return page_size
        maximum = settings.API_MAX_PAGE_SIZE
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
            raise ImproperlyConfigured("API_MAX_PAGE_SIZE must be a positive integer.")
        return min(page_size or maximum, maximum)

    def get_index_link(self, index):
        """Construit l'URL d'une page en conservant les autres paramètres GET."""
        if not index:
            return None
        url = self.request and self.request.build_absolute_uri() or ""
        return replace_query_param(url, "page", index)

    def get_paginated_response(self, data):
        """Retourne résultats, compteurs, navigation et additional_data.

        additional_data peut compléter ou remplacer les clés de la réponse ;
        l'appelant doit donc éviter les collisions involontaires avec results.
        Les données sont déjà sérialisées, aucune permission n'est vérifiée ici.
        """
        count = self.page.paginator.count
        page_count = self.page.paginator.num_pages
        next = self.page.next_page_number() if self.page.has_next() else None
        previous = (
            self.page.previous_page_number() if self.page.has_previous() else None
        )
        page_size = self.get_page_size(self.request)

        additional_data = OrderedDict(
            (key, value) for key, value in self.additional_data.items()
        )
        response = OrderedDict()
        response.update(
            OrderedDict(
                [
                    ("count", count),
                    ("page_size", page_size),
                    ("page", self.page.number),
                    ("pages", page_count),
                    ("previous_page", previous),
                    ("next_page", next),
                    ("previous", self.get_previous_link()),
                    ("next", self.get_next_link()),
                    ("first", self.get_index_link(1)),
                    ("last", self.get_index_link(page_count)),
                    ("results", data),
                ]
            )
        )
        response.update(additional_data)
        return Response(response)
