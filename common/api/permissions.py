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


class CurrentUserPermissions(CommonModelPermissions):
    """
    Permissions spécifiques permettant de consulter les données propres à l'utilisateur connecté
    """
    filters = {}

    def has_permission(self, request, view):
        has_permission = super().has_permission(request, view)
        if has_permission:
            return True
        model = view.queryset.model
        if not request.user or view.action not in ['list', 'retrieve'] or model not in self.filters:
            return False
        filters = view.kwargs or {}
        filters.update(self.filters.get(model)(request))
        view.queryset = view.get_queryset().filter(**filters)
        return view.model.objects.filter(**filters).exists()
