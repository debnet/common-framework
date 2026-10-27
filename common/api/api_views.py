# coding: utf-8
"""Endpoints utilitaires : routage public, profil et métadonnées par UUID."""

from collections import OrderedDict

from django.contrib.auth import get_user_model
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from common.api.input_serializers import ResolveUrlInputSerializer
from common.api.permissions import ServiceUsagePermission
from common.api.serializers import UserInfosSerializer
from common.api.utils import api_view_with_serializer
from common.models import Global
from common.utils import recursive_get_urls


@api_view_with_serializer(["POST"], ResolveUrlInputSerializer)
@permission_classes([AllowAny])
@csrf_exempt
def resolve_url(request):
    """
    Permet de résoudre une URL à partir de son nom de vue
    Endpoint public : il ne vérifie pas que l'appelant puisse ensuite consulter
    la ressource. Renvoie une URL absolue, ou 404 si reverse ne trouve pas le nom.
    """
    try:
        data = getattr(request, "validated_data", request.data)
        url = reverse(
            data.get("viewname"),
            args=data.get("args", []),
            kwargs=data.get("kwargs", {}),
        )
        return Response(request.build_absolute_uri(url))
    except NoReverseMatch:
        return Response(status=status.HTTP_404_NOT_FOUND)


@api_view(["GET"])
@permission_classes([AllowAny, ServiceUsagePermission])
def get_urls(request):
    """
    Permet d'afficher l'ensemble des URLs connues
    Le paramètre GET 'namespaces' peut être répété pour filtrer plusieurs namespaces.
    Endpoint public : cette liste révèle la structure du routage, sans accorder
    de permission d'accès. namespaces est lu comme une liste de paramètres GET.
    """
    namespaces = request.query_params.getlist("namespaces", [])
    return Response(
        OrderedDict(
            (name, request.build_absolute_uri(url))
            for name, url in recursive_get_urls(namespaces=namespaces)
        )
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, ServiceUsagePermission])
def user_infos(request, user_id=None):
    """
    Renvoie le profil, les groupes et métadonnées d'un compte authentifié.

    Sans user_id, utilise le compte courant. La consultation d'un autre compte
    exige is_staff ou is_superuser ; les autres utilisateurs peuvent fournir
    uniquement leur propre identifiant. Le serializer dédié exclut password
    et ne traite pas les paramètres de projection des viewsets CRUD.
    """
    user_id = request.user.pk if user_id is None else user_id
    if user_id != request.user.pk and not (
        request.user.is_staff or request.user.is_superuser
    ):
        raise PermissionDenied()
    user = get_object_or_404(
        get_user_model().objects.prefetch_related(
            "metadata", "user_permissions", "groups__metadata", "groups__permissions"
        ),
        pk=user_id,
    )
    return Response(UserInfosSerializer(user, context=dict(request=request)).data)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, ServiceUsagePermission])
def metadata(request, uuid):
    """
    Lit ou modifie les métadonnées d'une entité retrouvée par son UUID global.

    Exige l'authentification et view_metadata, y compris pour POST qui retourne
    les données. POST reçoit {clé: valeur} : None supprime (delete_metadata),
    une clé existante est modifiée (change_metadata), une nouvelle est ajoutée
    (add_metadata). Toutes les permissions sont vérifiées avant les écritures.
    Ces droits sont des droits de modèle, pas des ACL sur l'entité cible.
    """
    # Le POST retourne aussi les métadonnées : la lecture reste donc nécessaire.
    if not request.user.has_perm("common.view_metadata"):
        raise PermissionDenied()
    if request.method == "POST":
        if not isinstance(request.data, dict) or any(
            not isinstance(key, str) or not key or len(key) > 100
            for key in request.data
        ):
            raise ValidationError(
                _(
                    "Un dictionnaire avec des clés non vides de 100 caractères maximum est requis."
                )
            )
    entity = Global.objects.from_uuid(uuid)
    if not entity:
        raise NotFound(_("Entité inconnue."))
    if request.method == "POST":
        existing_keys = set(
            entity.metadata.filter(key__in=request.data).values_list("key", flat=True)
        )
        permissions = set()
        for key, value in request.data.items():
            if value is None:
                permissions.add("common.delete_metadata")
            elif key in existing_keys:
                permissions.add("common.change_metadata")
            else:
                permissions.add("common.add_metadata")
        # Vérifie toutes les opérations avant d'effectuer la moindre écriture.
        if not request.user.has_perms(permissions):
            raise PermissionDenied()
        for key, value in request.data.items():
            if value is None:
                entity.del_metadata(key)
            else:
                entity.set_metadata(key, value)
    return Response(entity.get_metadata())
