# coding: utf-8
"""Champs DRF : JSON, choix dynamiques, projections et relations asymétriques."""

from django import VERSION as django_version
from django.db import models
from django.urls import NoReverseMatch
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.fields import ChoiceField, Field, ReadOnlyField
from rest_framework.relations import (
    HyperlinkedIdentityField,
    HyperlinkedRelatedField,
    PrimaryKeyRelatedField,
)

from common.settings import settings
from common.utils import (
    JsonDecoder,
    JsonEncoder,
    get_pk_field,
    json_encode,
    recursive_get_urls,
)

# JSON natif et adaptateur de compatibilité
# ----------------------------------------

# Substitue le champ JSON du common par la version générique introduite par Django 3.1
if django_version < (3, 1) or settings.COMMON_JSONFIELD:

    class JsonField(Field):
        """
        JsonField representation for Django REST Framework
        """

        def to_native(self, obj):
            return obj

        def from_native(self, data):
            return json_encode(data)

        def to_internal_value(self, data):
            return data

        def to_representation(self, value):
            return value

else:
    from rest_framework.fields import JSONField

    class JsonField(JSONField):
        """
        JsonField representation for Django REST Framework
        """

        def __init__(self, *args, **kwargs):
            """Conserve le champ DRF natif en ajoutant les codecs Common par défaut.

            Une surcharge explicite, y compris None, est respectée ; la validation
            de sérialisabilité et les options du champ restent celles de DRF.
            """
            if "encoder" not in kwargs:
                kwargs["encoder"] = JsonEncoder
            if "decoder" not in kwargs:
                kwargs["decoder"] = JsonDecoder
            super().__init__(*args, **kwargs)


# Choix et représentations en lecture seule
# ----------------------------------------


class QuerySetChoiceField(ChoiceField):
    """
    Surcharge d'un champ de choix se comportant comme une clé étrangère avec l'option de choisir la clé et le libellé
    """

    def __init__(
        self, model, value=None, label=None, filters=None, order_by=None, **kwargs
    ):
        """Charge les choix (colonne value, colonne label) du modèle à l'initialisation.

        filters est un mapping de lookups ORM et order_by un nom de champ signé.
        L'argument historique label nomme une colonne, pas le libellé du champ
        DRF ; elle est stockée séparément dans label_field pour éviter une collision.
        """
        self.model = model
        self.value = value
        self.label_field = label
        self.filters = filters
        self.order_by = order_by
        super().__init__(choices=self.values, **kwargs)

    @property
    def values(self):
        """Réévalue les choix en base et renvoie une liste de couples valeur/libellé.

        Toute erreur de résolution ou de base entraîne [] pour compatibilité.
        La propriété choices du parent est, elle, l'instantané créé au constructeur.
        """
        try:
            queryset = self.model.objects.filter(**self.filters or {})
            if self.order_by:
                queryset = queryset.order_by(self.order_by)
            return list(queryset.values_list(self.value, self.label_field))
        except Exception:
            return []


class ChoiceDisplayField(ReadOnlyField):
    """
    Champ pour récupérer la valeur d'une énumération à partir d'un modèle
    """

    def __init__(self, choices, **kwargs):
        self.choices = dict(choices)
        super().__init__(**kwargs)

    def to_representation(self, value):
        """Renvoie le libellé associé ou None si la valeur n'est pas connue."""
        return self.choices.get(value)


class ReadOnlyObjectField(ReadOnlyField):
    """
    Surcharge du champ "lecture seule" de DRF pour prendre en compte les objets complets
    """

    def to_representation(self, value):
        """Préserve les valeurs simples et adapte les fichiers/objets ORM.

        Une URL est rendue absolue si une request est dans le contexte. Un modèle
        sans URL donne to_dict() si disponible, sinon sa PK. Aucune permission
        sur l'objet n'est vérifiée ici : elle incombe à la vue appelante.
        """
        url = None
        if getattr(value, "url", None):
            url = value.url
        elif isinstance(value, dict) and "url" in value:
            url = value.get("url")
        if url:
            request = self.context.get("request", None)
            if request is not None:
                return request.build_absolute_uri(url)
            return url
        if not isinstance(value, models.Model):
            return value
        pk_field = get_pk_field(value).name
        return (
            value.to_dict()
            if hasattr(value, "to_dict")
            else getattr(value, pk_field, value)
        )


# Relations hyperliées et représentation asymétrique lecture/écriture
# ------------------------------------------------------------------


class CustomHyperlinkedField:
    """
    Surcharge des méthodes pour les champs identifiants par URL
    """

    views_for_model = {}
    pk_field = None

    def __init__(self, *args, **kwargs):
        """Résout le namespace du view_name à partir des routes du modèle lié.

        La découverte conserve un cache par classe/modèle. Un queryset fourni
        peut être évalué par le test de présence historique du constructeur.
        """
        super().__init__(*args, **kwargs)
        # Tente de récupérer l'URL dans les APIs qui correspondent exactement au modèle ciblé
        if self.view_name and self.queryset:
            model = self.queryset.model
            views = self.views_for_model.setdefault(
                model, list(recursive_get_urls(model=model))
            )
            for view_name, url in views:
                if view_name.endswith(self.view_name):
                    self.view_name = view_name

    def get_name(self, obj):
        # Retourne juste la clé primaire pour éviter de multiplier les requêtes
        return str(obj.pk)

    def get_url(self, obj, view_name, request, format):
        """Résout le détail d'un objet, en recherchant les namespaces compatibles.

        Essaie le modèle du champ parent puis celui du queryset/de l'objet.
        Une PK absente ou un reverse impossible donne None au lieu de faire
        échouer toute la représentation ; les autres erreurs sont propagées.
        """
        if not hasattr(obj, "pk") or obj.pk in (None, ""):
            return None

        try:
            # Récupération du modèle de la clé étrangère via le modèle du Serializer parent
            model = self.parent.Meta.model._meta.get_field(
                self.field_name
            ).related_model
        except Exception:
            # Récupération du modèle lié au QuerySet
            model = getattr(getattr(self, "queryset", None), "model", None) or type(obj)

        # Tente de récupérer l'URL dans les APIs qui correspondent exactement au modèle ciblé
        views = self.views_for_model.setdefault(
            model, list(recursive_get_urls(model=model))
        )
        for try_view_name, url in views:
            if try_view_name.endswith(view_name):
                view_name = try_view_name

        lookup_value = getattr(obj, self.lookup_field)
        kwargs = {self.lookup_url_kwarg: lookup_value}
        try:
            return self.reverse(
                view_name, kwargs=kwargs, request=request, format=format
            )
        except NoReverseMatch:
            return None

    def to_internal_value(self, value):
        """Accepte une URL ou, après refus de celle-ci, une PK de la relation.

        Le fallback conserve le queryset du champ, donc ses éventuels filtres
        d'accès ; une PK inexistante reste une erreur de validation DRF.
        """
        try:
            return super().to_internal_value(value)
        except ValidationError:
            return PrimaryKeyRelatedField.to_internal_value(self, value)


class CustomHyperlinkedIdentityField(CustomHyperlinkedField, HyperlinkedIdentityField):
    """
    Surcharge du champ identifiant par URL pour les clés primaires
    """


class CustomHyperlinkedRelatedField(CustomHyperlinkedField, HyperlinkedRelatedField):
    """
    Surcharge du champ identifiant par URL pour les clés étrangères
    """


class AsymetricRelatedField(serializers.PrimaryKeyRelatedField):
    """
    Surcharge du PrimaryKeyRelatedField permettant la lecture (GET) de d'objet serialisé complet
    et l'écriture (POST/PUT) uniquement avec l'identifiant
    """

    # Constructeur permettant de générer le field depuis un serializer
    @classmethod
    def from_serializer(cls, serializer, name=None):
        """Crée une classe de champ utilisant serializer en lecture et sa PK en écriture.

        Le serializer doit exposer Meta.model pour fournir le queryset par
        défaut. name permet de fixer le nom de classe générée pour les schémas.
        """
        if name is None:
            item = (
                serializer.Meta.model
                if isinstance(serializer, serializers.ModelSerializer)
                else serializer.__class__
            )
            name = "{}AsymetricAutoField".format(item.__name__)
        return type(name, (cls,), {"serializer_class": serializer})

    # Surcharge permettant de récupérer l'objet serialisé (et non juste l'identifiant)
    def to_representation(self, value):
        return self.serializer_class(value, context=self.context).data

    # Permet de prendre le queryset du modèle du serializer
    def get_queryset(self):
        if self.queryset:
            return self.queryset
        return self.serializer_class.Meta.model.objects.all()

    # Surcharge retournant directement l'identifiant de chaque item au lieu de faire appel à 'to_representation'
    # qui ne retourne plus uniquement l'identifiant mais un objet serialisé
    def get_choices(self, cutoff=None):
        """Construit les choix PK/libellé sans sérialiser chaque objet en entier.

        cutoff borne le queryset avant itération, pour les listes de choix des
        formulaires DRF. Le queryset propre au champ est prioritaire s'il existe.
        """
        queryset = self.get_queryset()
        if queryset is None:
            return {}
        if cutoff is not None:
            queryset = queryset[:cutoff]
        return {item.pk: self.display_value(item) for item in queryset}
