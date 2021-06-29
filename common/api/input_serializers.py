# coding: utf-8
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.api.fields import JsonField
from common.api.serializers import BaseCustomSerializer


class ResolveUrlInputSerializer(BaseCustomSerializer):
    """
    Serializer pour la résolution d'URLs
    """

    viewname = serializers.CharField()
    args = serializers.ListField(required=False)
    kwargs = serializers.JSONField(required=False)


class ResetPasswordSerializer(BaseCustomSerializer):
    """
    Serializer pour la réinitialisation du mot de passe utilisateur
    """

    username = serializers.CharField(max_length=30, required=False, label=_("nom d'utilisateur"))
    email = serializers.EmailField(required=False, label=_("e-mail"))


class ConfirmPasswordSerializer(BaseCustomSerializer):
    """
    Serializer pour la confirmation de réinitialisation du mot de passe
    """

    secret_key = serializers.CharField(label=_("clé secrète"))
    uid = serializers.CharField(label=_("identifiant"))
    token = serializers.CharField(label=_("token"))
    password = serializers.CharField(label=_("mot de passe"), write_only=True, style=dict(input_type="password"))


class MetaDataSerializer(BaseCustomSerializer):
    """
    Serializer pour l'ajout de métadonnées sur une entité
    """

    key = serializers.CharField(label=_("clé"))
    value = JsonField(required=False, label=_("valeur"))
    date = serializers.DateTimeField(required=False, label=_("date péremption"))
