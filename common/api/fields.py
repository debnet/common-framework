# coding: utf-8
from rest_framework.fields import Field
from rest_framework.relations import HyperlinkedIdentityField

from common.utils import json_encode, recursive_get_urls


class JsonField(Field):
    """
    JsonField representation for Django REST Framework
    """

    def to_native(self, obj):
        return obj

    def from_native(self, data):
        return json_encode(data, sort_keys=True)

    def to_internal_value(self, data):
        return data

    def to_representation(self, value):
        return value


class CustomHyperlinkedIdentityField(HyperlinkedIdentityField):
    """
    Surcharge du champ identifiant des hyperliens pour éviter l'évaluation du __str__ des modèles
    """
    urls_for_model = {}

    def get_name(self, obj):
        return str(obj.pk)

    def get_url(self, obj, view_name, request, format):
        if hasattr(obj, 'pk') and obj.pk in (None, ''):
            return None

        # Tente de récupérer l'URL dans les APIs qui correspond exactement au modèle ciblé
        model = type(obj)
        urls = self.urls_for_model[model] = self.urls_for_model.get(model) or recursive_get_urls(model=model)
        for urlname, url in urls:
            if urlname.endswith(view_name):
                view_name = urlname

        lookup_value = getattr(obj, self.lookup_field)
        kwargs = {self.lookup_url_kwarg: lookup_value}
        return self.reverse(view_name, kwargs=kwargs, request=request, format=format)
