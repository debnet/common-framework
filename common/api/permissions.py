# coding: utf-8
"""Permissions de modèles, lecture de ses propres données et réservation de quota."""

from rest_framework import permissions


class ServiceUsagePermission(permissions.BasePermission):
    """Réserve le quota après l'authentification DRF, avant l'exécution de la vue."""

    def has_permission(self, request, view):
        """Réserve le quota de l'acteur déjà authentifié, puis laisse continuer.

        À placer après les permissions d'accès dans les vues DRF externes ;
        un refus de quota est converti en 403 par la gestion d'erreurs DRF.
        """
        from common.utils import reserve_service_usage

        reserve_service_usage(request)
        return True


class CommonModelPermissions(permissions.DjangoModelPermissions):
    """
    Permissions spécifiques pour les API RESTful
    """

    perms_map = {
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "OPTIONS": [],
        "HEAD": [],
        "POST": ["%(app_label)s.add_%(model_name)s"],
        "PUT": ["%(app_label)s.change_%(model_name)s"],
        "PATCH": ["%(app_label)s.change_%(model_name)s"],
        "DELETE": ["%(app_label)s.delete_%(model_name)s"],
    }

    def has_permission(self, request, view):
        """
        Surcharge de la gestion de permission par défaut pour autoriser la consultation en l'absence d'un QuerySet
        """
        try:
            return super().has_permission(request, view)
        except (AssertionError, AttributeError):
            return request.user and (
                request.user.is_authenticated or not self.authenticated_users_only
            )


class CurrentUserPermissions(CommonModelPermissions):
    """
    Permissions spécifiques permettant de consulter les données propres à l'utilisateur connecté
    """

    filters = {}

    def has_permission(self, request, view):
        """Autorise le modèle ou restreint list/retrieve aux données de l'acteur.

        filters associe les modèles à des fabriques de filtres recevant request.
        Le fallback modifie view.queryset pour le reste de la requête ; il ne
        donne aucun droit d'écriture et ne constitue pas une ACL générique.
        """
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        has_permission = super().has_permission(request, view)
        if has_permission:
            return True
        model = view.queryset.model
        if (
            not request.user
            or view.action not in ["list", "retrieve"]
            or model not in self.filters
        ):
            return False
        view.queryset = view.get_queryset().filter(**self.filters.get(model)(request))
        return view.queryset.exists() if view.action == "retrieve" else True
