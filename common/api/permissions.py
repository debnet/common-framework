# coding: utf-8
from rest_framework import permissions


class CommonModelPermissions(permissions.DjangoModelPermissions):
    """
    Permissions spécifiques pour les API RESTful
    """
    perms_map = {
        'GET': ['%(app_label)s.view_%(model_name)s'],
        'OPTIONS': [],
        'HEAD': [],
        'POST': ['%(app_label)s.add_%(model_name)s'],
        'PUT': ['%(app_label)s.change_%(model_name)s'],
        'PATCH': ['%(app_label)s.change_%(model_name)s'],
        'DELETE': ['%(app_label)s.delete_%(model_name)s'],
    }

    def has_permission(self, request, view):
        """
        Surcharge de la gestion de permission par défaut pour autoriser la consultation en l'absence d'un QuerySet
        """
        try:
            return super().has_permission(request, view)
        except (AssertionError, AttributeError):
            return request.user and (request.user.is_authenticated or not self.authenticated_users_only)


class CurrentUserPermissions(CommonModelPermissions):
    """
    Permissions spécifiques permettant de consulter les données propres à l'utilisateur connecté
    """
    filters = {}

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        has_permission = super().has_permission(request, view)
        if has_permission:
            return True
        model = view.queryset.model
        if not request.user or view.action not in ['list', 'retrieve'] or model not in self.filters:
            return False
        view.queryset = view.get_queryset().filter(**self.filters.get(model)(request))
        return view.queryset.exists() if view.action == 'retrieve' else True
