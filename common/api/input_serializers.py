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


class MetaDataSerializer(BaseCustomSerializer):
    """
    Serializer pour l'ajout de métadonnées sur une entité
    """

    key = serializers.CharField(label=_("clé"))
    value = JsonField(required=False, label=_("valeur"))
    date = serializers.DateTimeField(required=False, label=_("date péremption"))
