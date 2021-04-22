# coding: utf-8
from collections import OrderedDict

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core import exceptions
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from common.api.input_serializers import (
    ResolveUrlInputSerializer, ResetPasswordSerializer, ConfirmPasswordSerializer)
from common.api.serializers import UserInfosSerializer
from common.api.utils import api_view_with_serializer
from common.models import Global
from common.settings import settings
from common.utils import base64_decode, base64_encode, recursive_get_urls


@api_view_with_serializer(['POST'], ResolveUrlInputSerializer)
@permission_classes([AllowAny])
@csrf_exempt
def resolve_url(request):
    """
    Permet de résoudre une URL à partir de son nom de vue
    """
    try:
        data = getattr(request, 'validated_data', request.data)
        url = reverse(data.get('viewname'), args=data.get('args', []), kwargs=data.get('kwargs', {}))
        return Response(request.build_absolute_uri(url))
    except NoReverseMatch:
        return Response(status=status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_urls(request):
    """
    Permet d'afficher l'ensemble des URLs connues
    L'argument optionnel 'namespaces' permet de définir un filtre sur un ou plusieurs namespaces séparés par des virgules
    """
    namespaces = request.query_params.getlist('namespaces', [])
    return Response(OrderedDict(
        (name, request.build_absolute_uri(url)) for name, url in recursive_get_urls(namespaces=namespaces)))


@api_view(['GET'])
def user_infos(request, user_id=None):
    """
    Permet d'afficher les données (user, groupes, métadonnées) de l'utilisateur en cours
    """
    user_id = user_id or request.user.pk
    user = get_object_or_404(
        get_user_model().objects.prefetch_related(
            'metadata',
            'user_permissions',
            'groups__metadata',
            'groups__permissions'
        ),
        pk=user_id)
    return Response(UserInfosSerializer(user, context=dict(request=request)).data)


@api_view_with_serializer(['POST'], ResetPasswordSerializer)
@permission_classes([AllowAny])
def reset_password(request):
    """
    Demande la réinitialisation du mot de passe d'un utilisateur
    """
    data = {k: v for k, v in request.validated_data.items() if v}
    if not data:
        raise ValidationError(_("Nom d'utilisateur ou e-mail requis pour la réinitialisation de mot de passe."))
    users = get_user_model().objects.filter(**data)
    if not users.exists():
        raise NotFound(_("Utilisateur non trouvé."))
    if users.count() > 1:
        raise ValidationError(_("Plusieurs utilisateurs correspondent à ces critères."))
    user = users.first()
    return Response(dict(
        username=user.username,
        email=user.email,
        uid=base64_encode(user.pk),
        token=default_token_generator.make_token(user)))


@api_view_with_serializer(['POST'], ConfirmPasswordSerializer)
@permission_classes([AllowAny])
def confirm_password(request):
    """
    Confirme la réinitialisation de mot de passe d'un utilisateur
    """
    data = request.validated_data
    secret_key = data.get('secret_key')
    token = data.get('token')
    uid = data.get('uid')
    password = data.get('password')

    if secret_key != settings.FRONTEND_SECRET_KEY:
        raise PermissionDenied(_("Clé secrète invalide."))
    try:
        user = get_user_model().objects.filter(pk=base64_decode(uid)).first()
    except ValueError:
        user = None
    if not user:
        raise NotFound(_("Utilisateur non trouvé."))
    if not default_token_generator.check_token(user, token):
        raise PermissionDenied(_("Jeton invalide."))
    try:
        validate_password(password, user=user)
    except exceptions.ValidationError as error:
        raise ValidationError({'password': error.messages})
    user.set_password(password)
    user.save()
    return Response(status=status.HTTP_200_OK)


@api_view(['GET', 'POST'])
def metadata(request, uuid):
    """
    Liste et/ou ajoute des métadonnées sur une entité spécifique
    """
    entity = Global.objects.from_uuid(uuid)
    if not entity:
        raise NotFound(_("Entité inconnue."))
    if request.method == 'POST':
        for key, value in request.data.items():
            if value is None:
                entity.del_metadata(key)
            else:
                entity.set_metadata(key, value)
    return Response(entity.get_metadata())
