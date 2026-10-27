# coding: utf-8
"""Réservation des quotas des vues Django, avant leurs effets de bord."""

from rest_framework.views import APIView

from common.utils import reserve_service_usage


class ServiceUsageMiddleware:
    """Réserve les quotas des vues Django avant leur exécution.

    Les vues DRF utilisent ServiceUsagePermission, après leur authentification.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        """Délègue DRF à sa permission ; réserve les autres vues après le middleware auth.

        Un refus lève PermissionDenied avant l'exécution de la vue. Les vues
        DRF externes doivent déclarer ServiceUsagePermission pour ne pas être
        exemptées par ce chemin de délégation.
        """
        view_class = getattr(view_func, "cls", None)
        if isinstance(view_class, type) and issubclass(view_class, APIView):
            return None
        reserve_service_usage(request)
        return None
