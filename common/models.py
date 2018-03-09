# coding: utf-8
import logging
import pickle
import time
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser, Group
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core import serializers
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, query
from django.db.models.deletion import Collector
from django.db.models.signals import m2m_changed, post_delete, post_init, post_save, pre_save
from django.dispatch import receiver
from django.forms.models import model_to_dict
from django.utils.timezone import now
from django.utils.translation import ugettext_lazy as _
from rest_framework.renderers import JSONRenderer

try:
    from rest_framework_xml.renderers import XMLRenderer
except ImportError:
    XMLRenderer = None

try:
    from rest_framework_yaml.renderers import YAMLRenderer
except ImportError:
    YAMLRenderer = None

from common.fields import JsonField, PickleField, json_encode
from common.settings import settings
from common.utils import get_current_app, get_current_user, get_pk_field, merge_dict


# Logging
logger = logging.getLogger(__name__)

# Celery
app = get_current_app()

ENTITY_FIELDS = ('uuid', 'creation_date', 'modification_date', )
PERISHABLE_FIELDS = ENTITY_FIELDS + ('start_date', 'end_date', )


def to_boolean(label_field, sort_order=None):
    """
    Transforme une méthode en attribut booléen
    :param label_field: Libellé du champ affiché par l'administration
    :param sort_order: Champ utilisé pour trier cette donnée (facultatif)
    :return: Wrapper
    """
    def wrapper(boolean_field):
        boolean_field.boolean = True
        boolean_field.short_description = label_field
        if sort_order:
            boolean_field.admin_order_field = sort_order
        return boolean_field
    return wrapper


def get_content_type(model):
    """
    Récupère le content type d'un modèle
    :param model: Instance de modèle
    :return: Content type
    """
    # Récupération du content type en cache si possible
    content_type = getattr(model, '_content_type', None)
    if not content_type or content_type.model_class is not model:
        content_type = ContentType.objects.get_for_model(model)
        model._content_type = content_type
    return content_type


class Serialized(object):
    """
    Resultat de serialisation
    """

    def __init__(self, value, format='json'):
        self.format = format
        if isinstance(value, query.QuerySet):
            self.meta = value.model._meta
            self.count = value.count()
            self.query = str(value.query or '') or None
            self.single = False
        elif isinstance(value, models.Model):
            self.meta = value._meta
            self.count = 1
            self.query = None
            self.single = True
            value = [value, ]
        self.data = serializers.serialize(format, value)

    def deserialize(self):
        data = serializers.deserialize(self.format, self.data)
        objects = [item.object for item in data]
        return objects[0] if self.single else objects

    def __str__(self):
        return self.data

    def __repr__(self):
        return '[{format}] {object} ({count})'.format(
            format=self.format, count=self.count, object=self.meta.object_name)


class MetaDataQuerySet(models.QuerySet):
    """
    QuerySet des métadonnées
    """

    def search(self, *, id=None, type=None, key=None, value=None, date=None, valid=True):
        """
        Effectue une recherche multi-critères dans les métadonnées
        :param id: instance de l'entité
        :param type: Type de l'entité concernée
        :param key: Clé de recherche
        :param value: Valeur après déserialisation
        :param date: Date de vérification (facultatif)
        :param valid: Uniquement les métadonnées valides ?
        :return: QuerySet
        """
        queryset = self
        if id:
            queryset = queryset.filter(object_id=id)
        if type:
            if isinstance(type, int):
                queryset = queryset.filter(content_type_id=type)
            else:
                content_type = get_content_type(type)
                queryset = queryset.filter(content_type=content_type)
        if key:
            queryset = queryset.filter(key=key)
        if value:
            from common.utils import json_encode
            queryset = queryset.filter(value=json_encode(value, sort_keys=True))
        return queryset.select_valid(date=date, valid=valid)

    def select_valid(self, date=None, valid=True):
        """
        Sélectionne les éléments valides du QuerySet
        :param date: Date de vérification (facultatif)
        :param valid: Retourne les éléments valides ou invalides (valides par défaut)
        :return: QuerySet
        """
        if valid is None:
            return self
        function = self.filter if valid else self.exclude
        return function(Q(deletion_date__isnull=True) | Q(deletion_date__gte=date or now()))

    valid = property(select_valid)


class MetaData(models.Model):
    """
    Modèle de métadonnées associées aux entités
    """
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        verbose_name=_("type d'entité"))
    object_id = models.TextField(verbose_name=_("identifiant"))
    entity = GenericForeignKey()

    key = models.CharField(max_length=100, verbose_name=_("clé"))
    value = JsonField(blank=True, null=True, verbose_name=_("valeur"))
    creation_date = models.DateTimeField(auto_now_add=True, verbose_name=_("date de création"))
    modification_date = models.DateTimeField(auto_now=True, verbose_name=_("date de modification"))
    deletion_date = models.DateTimeField(blank=True, null=True, verbose_name=_("date de suppression"))
    objects = MetaDataQuerySet.as_manager()

    def __str__(self):  # pragma: no cover
        return _("{key}: {value}").format(key=self.key, value=self.value)

    @property
    def valid(self):
        """
        Validité dans le temps de la métadonnée
        """
        return not self.deletion_date or now() < self.deletion_date

    @staticmethod
    def get(instance, key=None, valid=True, raw=False, queryset=None):
        """
        Permet de récupérer une valeur de métadonnée à partir de sa clé depuis une instance
        :param instance: Instance du modèle
        :param key: Clé de recherche
        :param valid: Uniquement les données valides ?
        :param raw: Retourner les entités à la place des valeurs ?
        :param queryset: QuerySet de récupération des métadonnées
        :return: Valeur ou entité
        """
        assert getattr(instance, 'pk', None), _("Unable to get metadata from an unsaved model instance.")
        content_type = get_content_type(instance.__class__)
        queryset = queryset or MetaData.objects.filter(content_type=content_type, object_id=instance.pk)
        if valid:
            queryset = queryset.filter(Q(deletion_date=None) | Q(deletion_date__gte=now()))
        if key:
            only = ('key', 'value', 'deletion_date') if raw else ('value', )
            metadata = queryset.filter(key=key).only(*only).first()
            return metadata if raw or not metadata else metadata.value
        queryset = queryset.only('key', 'value').order_by('key')
        return queryset if raw else {m.key: m.value for m in queryset}

    @staticmethod
    def set(instance, key, value, date=None, queryset=None):
        """
        Permet d'ajouter ou modifier une métadonnée
        :param instance: Instance du modèle
        :param key: Clé
        :param value: Valeur
        :param date: Date de péremption de la métadonnée
        :param queryset: QuerySet de récupération des métadonnées
        :return: Vrai en cas de succès, faux sinon
        """
        assert getattr(instance, 'pk', None), _("Unable to set metadata for an unsaved model instance.")
        content_type = get_content_type(instance.__class__)
        try:
            queryset = queryset or MetaData.objects.filter(content_type=content_type, object_id=instance.pk)
            metadata = queryset.only('value', 'deletion_date').get(key=key)
            metadata.deletion_date = date
            metadata.value = value
            metadata.save(update_fields=('value', 'deletion_date'))
        except MetaData.DoesNotExist:
            metadata = MetaData(
                content_type=content_type,
                object_id=instance.pk,
                key=key,
                value=value,
                deletion_date=date)
            metadata.save()
        return metadata

    @staticmethod
    def add(instance, key, value, allow_duplicate=True, queryset=None):
        """
        Permet d'ajouter une valeur à une métadonnée existante
        :param instance: Instance du modèle
        :param key: Clé
        :param value: Valeur
        :param allow_duplicate: Autorise l'ajout de doublons dans les listes de valeur (par défaut)
        :param queryset: QuerySet de récupération des métadonnées
        :return: Métadonnée
        """
        assert getattr(instance, 'pk', None), _("Unable to set metadata for an unsaved model instance.")
        metadata = MetaData.get(instance, key=key, queryset=queryset, raw=True)
        if metadata:
            if isinstance(value, dict) and isinstance(metadata.value, dict):
                metadata.value.update(value)
            elif isinstance(metadata.value, list):
                values = metadata.value
                if isinstance(value, (list, tuple, set, frozenset)):
                    values.extend(list(value))
                else:
                    values.append(value)
                metadata.value = values if allow_duplicate else set(values)
            else:
                metadata.value += value
            metadata.save(update_fields=('value', ))
            return metadata
        value = value if isinstance(value, (list, tuple, set, frozenset, dict)) else [value]
        return MetaData.set(instance, key=key, value=value, queryset=queryset)

    @staticmethod
    def remove(instance, key=None, logic=False, date=None, queryset=None):
        """
        Permet de supprimer une ou toutes les métadonnées
        :param instance: Instance du modèle
        :param key: Clé
        :param logic: Suppression logique ?
        :param date: Date de péremption de la métadonnée
        :param queryset: QuerySet de récupération des métadonnées
        :return: Vrai en cas de succès, faux sinon
        """
        assert getattr(instance, 'pk', None), _("Unable to delete metadata from an unsaved model instance.")
        content_type = get_content_type(instance.__class__)
        queryset = queryset or MetaData.objects.filter(content_type=content_type, object_id=instance.pk)
        if key:
            queryset = queryset.filter(key=key)
        if logic:
            date = date or now()
            for metadata in queryset.only('deletion_date').all():
                metadata.deletion_date = date
                metadata.save(update_fields=('deletion_date', ))
        else:
            queryset.all().delete()

    class Meta:
        verbose_name = _("métadonnée")
        verbose_name_plural = _("métadonnées")
        unique_together = ('content_type', 'object_id', 'key')
        index_together = (
            ('content_type', 'object_id'),
            ('content_type', 'object_id', 'deletion_date'),
            ('content_type', 'object_id', 'deletion_date', 'key'))


class CommonQuerySet(models.QuerySet):
    """
    QuerySet des modèles communs
    """

    def serialize(self, format='json'):
        """
        Permet de serialiser le QuerySet
        :param format: Format de sérialisation
        :return: QuerySet serialisée
        """
        return Serialized(self, format=format)

    def to_dict(self, *args, **kwargs):
        """
        Retourne l'ensemble des entités du QuerySet sous forme de dictionnaire
        :return: Liste de dictionnaires
        """
        return [e.to_dict(*args, **kwargs) if isinstance(e, CommonModel) else e for e in self]

    def __json__(self):
        """
        Représentation de l'instance sous forme de dictionnaire pour sérialisation JSON
        :return: dict
        """
        return [item.__json__() for item in self]


class CommonModel(models.Model):
    """
    Modèle commun
    """
    metadata = GenericRelation(MetaData)
    objects = CommonQuerySet.as_manager()

    # Propriétés liées à l'historisation et au type de modèle
    _copy = {}
    _copy_m2m = {}
    _content_type = None

    def validate_unique(self, exclude=None):
        """
        Surcharge de la validation de l'unicité pour les index uniques composés de champs nuls
        :param exclude: Champs à exclure de la validation
        """
        model = type(self)
        for unique_together in model._meta.unique_together:
            queryset = model.objects.exclude(pk=self.pk) if self.pk else model.objects.all()
            fields = []
            has_null = False
            for field_name in unique_together:
                fields.append(field_name)
                value = getattr(self, field_name, None)
                field = model._meta.get_field(field_name)
                if field.null and value is None:
                    queryset = queryset.filter(**{field_name + '__isnull': True})
                    has_null = True
                else:
                    queryset = queryset.filter(**{field_name: value})
            if has_null and queryset.count() > 0:
                raise ValidationError(self.unique_error_message(model, fields))
        super().validate_unique(exclude)

    def update(self, exclude=None):
        """
        Permet de mettre à jour un enregistrement existant à partir des données d'une instance
        :param exclude: Champs à exclure de la modification
        :return: Nombre d'enregistrements modifiés
        """
        exclude = exclude or []
        non_unique_fields = set()
        unique_fields = set()
        model = type(self)
        for unique_together in model._meta.unique_together:
            unique_fields.update(unique_together)
        for field in model._meta.fields:
            if field.auto_created or not field.editable or field.name in exclude:
                continue
            if field.unique:
                unique_fields.add(field.name)
            else:
                non_unique_fields.add(field.name)
        assert unique_fields, _("Unable to update an instance which have no unique fields.")
        queryset = model.objects.filter(**{field: getattr(self, field, None) for field in unique_fields})
        count = queryset.update(**{field: getattr(self, field, None) for field in non_unique_fields})
        if count:
            self.pk = queryset.first().pk
            self.refresh_from_db()
        return count

    def save(self, *args, force_insert=True, _full_update=False, **kwargs):
        """
        Sauvegarde l'instance du modèle
        """
        if self.pk and not _full_update and not force_insert:
            kwargs['update_fields'] = update_fields = set(kwargs.pop('update_fields', self.modified.keys()))
            # Les champs de date avec auto_now=True ne sont modifiés que pendant la sauvegarde
            update_fields.update([field.name for field in self._meta.fields if getattr(field, 'auto_now', None)])
        return super().save(*args, **kwargs)

    def get_metadata(self, key=None, valid=True, raw=False):
        """
        Permet de récupérer une valeur de métadonnée à partir de sa clé
        :param key: Clé de recherche
        :param valid: Uniquement les données valides ?
        :param raw: Retourner les entités à la place des valeurs ?
        :return: Valeur ou entité
        """
        return MetaData.get(self, key=key, valid=valid, raw=raw, queryset=self.metadata)

    def set_metadata(self, key, value, date=None):
        """
        Permet d'ajouter ou modifier une métadonnée
        :param key: Clé
        :param value: Valeur
        :param date: Date de péremption de la métadonnée
        :return: Vrai en cas de succès, faux sinon
        """
        return MetaData.set(self, key=key, value=value, date=date, queryset=self.metadata)

    def add_metadata(self, key, value, allow_duplicate=True):
        """
        Permet d'ajouter une valeur à une métadonnée existante
        :param key: Clé
        :param value: Valeur
        :param default: Valeur par défaut si la métadonnée n'existe pas (facultatif)
        :param allow_duplicate: Autorise l'ajout de doublons dans les listes de valeur (par défaut)
        :return: Métadonnée
        """
        return MetaData.add(self, key=key, value=value, allow_duplicate=allow_duplicate, queryset=self.metadata)

    def del_metadata(self, key=None, logic=False, date=None):
        """
        Permet de supprimer une métadonnée
        :param key: Clé
        :param logic: Suppression logique ?
        :param date: Date de péremption de la métadonnée
        :return: Vrai en cas de succès, faux sinon
        """
        return MetaData.remove(self, key=key, logic=logic, date=date, queryset=self.metadata)

    def to_dict(self, includes=None, excludes=None,
                editables=False, uids=False, metadata=False, names=False, types=False,
                display=False, fks=False, m2m=False, no_ids=False, functions=None, extra=None, raw=False, **kwargs):
        """
        Retourne la représentation d'une entité sous forme de dictionnaire
        :param includes: Attributs à inclure
        :param excludes: Attributs à exclure
        :param editables: Inclure les valeurs des attributs non éditables ?
        :param uids: Inclure les identifiants uniques de toutes les entités liées ?
        :param metadata: Inclure les métadonnées ?
        :param names: Inclure les informations textuelles du modèle ?
        :param types: Inclure le type d'entité ?
        :param display: Inclure le libellé de l'attribut s'il existe ?
        :param fks: Inclure les éléments liés via les clés étrangères ?
        :param m2m: Inclure les identifiants des relations ManyToMany liées ?
        :param no_ids: Inclure les identifiants des clés primaires et les identifiants des clés étrangères ?
        :param functions: Exécuter et inclure le résultat d'une ou plusieurs fonctions ?
        Les fonctions doivent être de la forme suivante :
        [ (nom_champ, nom_fonction, [arg1, arg2, ...], {kwarg1: valeur, kwargs2: valeur, ...} ]
        :param extra: Liste d'attributs supplémentaires à récupérer (ou vrai pour tous les attributs hors modèle)
        :param raw: Ne pas chercher à retourner des valeurs serialisables ?
        :return: Dictionnaire
        """
        keywords = dict(editables=editables, uids=uids, metadata=metadata,
                        names=names, types=types, display=display, fks=fks, m2m=m2m, no_ids=no_ids)
        keywords.update(kwargs)
        from django.db.models.fields.related import ManyToManyField
        meta = self._meta
        data = dict()
        # Données textuelles de l'entité (nom, modèle, représentation, etc...)
        if names:
            data.update(
                _label=str(self),
                _model=dict(
                    object_name=meta.object_name,
                    model_name=meta.model_name,
                    app_label=meta.app_label,
                    verbose_name=str(meta.verbose_name) if meta.verbose_name else None,
                    verbose_name_plural=str(meta.verbose_name_plural) if meta.verbose_name_plural else None))
        # Type de l'entité
        if types:
            data_type = model_to_dict(get_content_type(self))
            data_type.pop('_state', None)  # Non serialisable
            data.update(_content_type=data_type)
        deferred_fields = self.get_deferred_fields()
        for field in meta.concrete_fields + meta.many_to_many:
            # Ignore les champs chargés en différé pour éviter une boucle de récursion dans to_dict()
            if field.attname in deferred_fields:
                continue
            # Champs éditables
            if not editables and not getattr(field, 'editable', editables):
                continue
            # Champs inclus
            if includes and field.name not in includes:
                continue
            # Champs exclus
            if excludes and field.name in excludes:
                continue
            # Relations de type many-to-many
            if isinstance(field, ManyToManyField):
                if not m2m:
                    continue
                if self.pk is None:
                    data[field.name] = []
                else:
                    value = field.value_from_object(self)
                    related = field.related_model
                    # Identifiants
                    if not no_ids:
                        data[field.name + '_ids'] = [v.pk for v in value]
                    # Données
                    if fks:
                        if isinstance(related, CommonModel):
                            data[field.name] = [v.to_dict(**keywords) for v in value]
                        else:
                            data[field.name] = [model_to_dict(v) for v in value]
                            for item in data[field.name]:
                                item.pop('_state', None)  # Non serialisable
                    # GUIDs (uniquement entités)
                    if uids and isinstance(related, Entity):
                        data[field.name + '_uids'] = [v.uuid for v in value]
            # Autres champs
            else:
                # Valeur du champ
                value = field.value_from_object(self)
                if field.primary_key and no_ids:
                    continue
                # Gestion des clés étrangères
                if isinstance(field, (models.ForeignKey, models.OneToOneField)):
                    # Identifiant
                    if not no_ids:
                        data[field.attname] = value
                    if fks or uids:
                        fk = getattr(self, field.name, None)
                        # Données
                        if fks and fk:
                            if isinstance(fk, CommonModel):
                                data[field.name] = fk.to_dict(**keywords)
                            else:
                                data[field.name] = model_to_dict(fk)
                                data[field.name].pop('_state', None)  # Non serialisable
                        # GUID (uniquement entité)
                        if uids and isinstance(fk, Entity):
                            data[field.name + '_uid'] = fk.uuid
                # Gestion des valeurs nulles (hors clés étrangères)
                elif value is None:
                    data[field.name] = None
                # Cas spécifique du champ JSON
                elif isinstance(field, JsonField):
                    data[field.name] = value if raw else json_encode(value, sort_keys=True)
                # Cas spécifique du champ binaire (pickle)
                elif isinstance(field, PickleField):
                    data[field.name] = value if raw else pickle.dumps(value)
                # Cas spécifique des champs fichier & image
                elif isinstance(field, (models.FileField, models.ImageField)):
                    data[field.name] = (value if raw else getattr(value, 'url', None)) if value else None
                # Cas spécifique pour les listes
                elif isinstance(value, (list, set, tuple)):
                    data[field.name] = value if raw else ','.join(value)
                elif display and hasattr(self, 'get_{}_display'.format(field.name)):
                    data[field.name + '_display'] = getattr(self, 'get_{}_display'.format(field.name))()
                    data[field.name] = value
                else:
                    data[field.name] = value
        # Gestion des métadonnées
        if metadata:
            data['metadata'] = self.get_metadata()
        # Appel de fonctions internes à l'entité
        if functions:
            for key, func_name, func_args, func_kwargs in functions:
                data[key] = getattr(self, func_name)(*func_args, **func_kwargs)
        # Champs additionnels
        if extra:
            # Récupération automatique des prefetchs
            if extra is True:
                extra = set(self.__dict__) - set(meta.model().__dict__)
            for field in extra:
                if field in data:
                    continue
                item = getattr(self, field, None)
                # Liste d'entités
                if item and isinstance(item, list) and item[0] and isinstance(item[0], Entity):
                    data[field] = [i.to_dict(**keywords) for i in item]
                # QuerySet d'entités
                elif isinstance(item, (CommonModel, CommonQuerySet)):
                    data[field] = item.to_dict(**keywords)
                # Dictionnaire
                elif isinstance(item, dict):
                    for key, value in item.items():
                        if key in data:
                            continue
                        # Entités ou QuerySet d'entités
                        if isinstance(value, (CommonModel, CommonQuerySet)):
                            data[key] = value.to_dict(**keywords)
                        # Autres données
                        else:
                            data[key] = value
                # Valeur quelconque
                else:
                    data[field] = item
        return data

    def m2m_to_dict(self, raw=False):
        """
        Retourne toutes les relations de type ManyToMany classées par attribut
        :param raw:
        :return: Dictionnaire
        """
        data = {}
        if self.pk is None:
            return data
        meta = self._meta
        for field in meta.many_to_many:
            if raw:
                value = field.value_from_object(self)
                data[field.name] = value
            else:
                data[field.name] = list(getattr(self, field.name).values_list('pk', flat=True))
        return data

    def related_to_dict(self, includes=None, excludes=None, valid=True, date=None, **kwargs):
        """
        Retourne toutes les relations de type related set classées par attribut
        :param includes: Relations à inclure
        :param excludes: Relations à exclure
        :param valid: Récupérer les éléments valides ?
        (entités périssables uniquement, la valeur nulle pour tous)
        :param date: Date de référence pour la validation des éléments
        (entités périssables uniquement, la valeur nulle pour la date et l'heure du jour)
        :param kwargs: Arguments complémentaires, principalement pour l'appel interne à 'to_dict()'
        :return: Dictionnaire
        """
        data = {}
        if self.pk is None:
            return data
        meta = self._meta
        for field in meta.get_fields():
            if not (field.one_to_many or field.one_to_one) or not field.auto_created:
                continue
            field_name = field.get_accessor_name()
            model = field.model
            if includes and field_name not in includes:
                continue
            if excludes and field_name in excludes:
                continue
            queryset = getattr(self, field_name)
            if issubclass(model, PerishableEntity):
                queryset = queryset.select_valid(valid=valid, date=date)
            data[field_name] = queryset.all().to_dict(**kwargs)
        return data

    def serialize(self, format='json'):
        """
        Permet de serialiser l'entité
        :param format: Format de sérialisation
        :return: Entité serialisée
        """
        return Serialized(self, format=format)

    def get_modified(self, **options):
        """
        Retourne l'ensemble des modifications effectuées sur l'entité
        :param options: Paramètres de la fonction .to_dict()
        :return: Set structuré par (champ, (valeur avant, valeur après))
        """
        old_data = self._copy
        if options:
            old_data = type(self)(**self._copy).to_dict(**options)
        new_data = self.to_dict(**options)
        keys = set(old_data.keys()) | set(new_data.keys())
        return {k: (old_data.get(k), new_data.get(k)) for k in keys if old_data.get(k) != new_data.get(k)}

    @property
    def modified(self):
        """
        Retourne l'ensemble des modifications effectuées sur l'entité
        """
        return self.get_modified(editables=True)

    @property
    def m2m_modified(self):
        """
        Retourne les identifiants modifiés sur les relations de type many-to-many de l'entité
        """
        old_data, new_data = self._copy_m2m, self.m2m_to_dict()
        keys = set(old_data.keys()) | set(new_data.keys())
        return {k: (old_data.get(k), new_data.get(k)) for k in keys if old_data.get(k) != new_data.get(k)}

    @staticmethod
    def _model_type(obj):
        obj._content_type = obj._content_type or get_content_type(obj)
        return obj._content_type

    @property
    def model_type(self):
        return self._model_type(self.__class__)

    @classmethod
    def get_model_type(cls):
        return cls._model_type(cls)

    def has_webhook(self, status=None):
        """
        Permet de déterminer si ce type d'entité est couvert par un ou plusieurs webhooks
        :param status: (Facultatif) Statut
        :return: Vrai ou faux
        """
        key = 'WEBHOOK_{}_{}_{}'.format(self._meta.app_label, self._meta.object_name, status or '@')
        result = cache.get(key)
        if not result:
            filters = dict(types__in=[self.model_type])
            if status:
                filters.update({Webhook.STATUS_FILTERS.get(status): True})
            result = Webhook.objects.filter(**filters).exists()
            cache.set(key, result, timeout=3600)
        return result

    def __json__(self):
        """
        Représentation de l'instance sous forme de dictionnaire pour sérialisation JSON
        :return: dict
        """
        data = self.to_dict(editables=True, types=True)
        data.update(_copy=self._copy, _copy_m2m=self._copy_m2m)
        return data

    class Meta:
        abstract = True


class HistoryCommon(CommonModel):
    """
    Abstraction commune aux historiques et champs modifiés
    """
    creation_date = models.DateTimeField(auto_now_add=True, editable=False, verbose_name=_("date"))
    restoration_date = models.DateTimeField(
        blank=True, null=True, editable=False, verbose_name=_("dernière restauration"))
    restored = models.NullBooleanField(editable=False, verbose_name=_("restauré"))
    data = JsonField(blank=True, null=True, editable=False, verbose_name=_("données"))
    data_size = models.PositiveIntegerField(editable=False, verbose_name=_("taille données"))

    def save(self, *args, **kwargs):
        self.data_size = 0
        if self.data is not None:
            self.data_size = len(str(self.data))
        return super().save(*args, **kwargs)

    class Meta:
        abstract = True


class CustomGenericForeignKey(GenericForeignKey):
    """
    Surcharge de la GenericForeignKey qui ne vide pas les propriétés de la clé si l'instance n'existe pas
    """

    def __set__(self, instance, value):
        if value is None:
            ct = getattr(instance, self.ct_field, None)
            fk = getattr(instance, self.fk_field, None)
        else:
            ct = self.get_content_type(obj=value)
            fk = value._get_pk_val()
        setattr(instance, self.ct_field, ct)
        setattr(instance, self.fk_field, fk)
        self.set_cached_value(instance, value)


class History(HistoryCommon):
    """
    Entité d'historique
    """
    CREATE = 'C'
    UPDATE = 'U'
    DELETE = 'D'
    RESTORE = 'R'
    M2M = 'M'
    LOG_STATUS = (
        (CREATE, _("Création")),
        (UPDATE, _("Modification")),
        (DELETE, _("Suppression")),
        (RESTORE, _("Restauration")),
        (M2M, _("Many-to-many")),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        editable=False,
        verbose_name=_("utilisateur"))
    status = models.CharField(max_length=1, choices=LOG_STATUS, editable=False, verbose_name=_("statut"))
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        editable=False,
        verbose_name=_("type d'entité"))
    object_id = models.TextField(editable=False, verbose_name=_("identifiant"))
    object_uid = models.UUIDField(editable=False, verbose_name=_("UUID"))
    object_str = models.TextField(editable=False, verbose_name=_("entité"))
    reason = models.TextField(blank=True, null=True, editable=False, verbose_name=_("motif"))
    admin = models.BooleanField(default=False, editable=False, verbose_name=_("admin"))
    entity = CustomGenericForeignKey()

    def __str__(self):  # pragma: no cover
        return _("[{status}] {content_type} #{object_id}").format(
            status=self.get_status_display(),
            content_type=self.content_type, object_id=self.object_id)

    def restore(self, *, ignore_log=None, current_user=None, reason=None,
                force_default=None, from_admin=None, rollback=False):
        """
        Permet de restaurer complètement une entité
        :param ignore_log: Ignorer l'historisation ?
        :param current_user: Utilisateur à l'origine de la restauration
        :param reason: Message d'information associé à l'historique de restauration
        :param force_default: Force le comportement par défaut de la sauvegarde ?
        :param from_admin: Indique que la restauration a été demandée via l'interface d'administration ?
        :param rollback: Recrée l'entité si elle a été supprimée ?
        """
        try:
            entity = self.entity
            if rollback:
                if not entity:
                    entity = self.content_type.model_class()()
                if not self.data:
                    self.restored = False
                    return self.restored
                for key, value in self.data.items():
                    setattr(entity, key, value)
            else:
                if not entity:
                    self.restored = False
                    return self.restored
                for field in self.historyfield_set.filter(status_m2m__isnull=True):
                    setattr(entity, field.name, field.data)
            entity._from_admin = from_admin
            entity._restore = True
            entity.save(
                _current_user=current_user or get_current_user(),
                _ignore_log=ignore_log,
                _reason=reason,
                _force_default=force_default)
            if not rollback:
                for field in self.historyfield_set.filter(status_m2m__isnull=False):
                    getattr(entity, field.name).set(field.data)
            self.restored = True
        except:
            self.restored = False
            raise
        finally:
            self.restoration_date = now()
            self.save()
        return self.restored

    class Meta:
        verbose_name = _("historique")
        verbose_name_plural = _("historiques")
        ordering = ['-creation_date']
        index_together = ('content_type', 'object_id')


class HistoryField(HistoryCommon):
    """
    Entité d'historique des modifications de champs
    """
    CLEAR_M2M = 'C'
    ADD_M2M = 'A'
    REMOVE_M2M = 'R'
    LOG_STATUS_M2M = (
        (CLEAR_M2M, _("Purge")),
        (ADD_M2M, _("Ajout")),
        (REMOVE_M2M, _("Suppression")),
    )

    history = models.ForeignKey(
        'History',
        on_delete=models.CASCADE,
        editable=False,
        verbose_name=_("historique"))
    field_name = models.CharField(max_length=100, editable=False, verbose_name=_("nom du champ"))
    old_value = models.TextField(blank=True, null=True, editable=False, verbose_name=_("ancienne valeur"))
    new_value = models.TextField(blank=True, null=True, editable=False, verbose_name=_("nouvelle valeur"))
    status_m2m = models.CharField(
        max_length=1,
        choices=LOG_STATUS_M2M,
        blank=True,
        null=True,
        editable=False,
        verbose_name=_("statut M2M"))

    def __str__(self):  # pragma: no cover
        return _("[{entity}] ({field}) {old} ~ {new}").format(
            entity=self.history.content_type, field=self.field_name, old=self.old_value, new=self.new_value)

    def restore(self, *, ignore_log=None, current_user=None, reason=None,
                force_default=None, from_admin=None, rollback=False):
        """
        Permet de restaurer un champ d'une entité
        :param ignore_log: Ignorer l'historisation ?
        :param current_user: Utilisateur à l'origine de la restauration
        :param reason: Message d'information associé à l'historique de restauration
        :param force_default: Force le comportement par défaut de la sauvegarde ?
        :param from_admin: Indique que la restauration a été demandée via l'interface d'administration ?
        :param rollback: (Utilisé uniquement sur un historique complet)
        """
        try:
            entity = self.history.entity
            if not entity:
                self.restored = False
                return self.restored
            if self.status_m2m:
                getattr(entity, self.field_name).set(self.data)
            else:
                setattr(entity, self.field_name, self.data)
            entity._from_admin = from_admin
            entity._restore = True
            entity.save(
                _current_user=current_user or get_current_user(),
                _ignore_log=ignore_log,
                _reason=reason,
                _force_default=force_default)
            self.restored = True
        except:
            self.restored = False
            raise
        finally:
            self.restoration_date = now()
            self.save()
        return self.restored

    class Meta:
        verbose_name = _("champ modifié")
        verbose_name_plural = _("champs modifiés")
        ordering = ['-creation_date']


class GlobalManager(models.Manager):
    """
    Manager global
    """

    def entity(self, uuid):
        """
        Récupération directe d'une entité à partir de son identifiant unique
        """
        try:
            return self.get(object_uid=uuid).entity
        except:
            return None


class Global(models.Model):
    """
    Entité globale
    """
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        editable=False,
        verbose_name=_("type d'entité"))
    object_id = models.TextField(editable=False, verbose_name=_("identifiant"))
    object_uid = models.UUIDField(unique=True, editable=False, verbose_name=_("UUID"))
    entity = GenericForeignKey()
    objects = GlobalManager()

    def __str__(self):
        return _("({object_uid}) {content_type} #{object_id}").format(
            object_uid=self.object_uid, content_type=self.content_type, object_id=self.object_id)

    class Meta:
        verbose_name = _("globale")
        verbose_name_plural = _("globales")
        unique_together = ('content_type', 'object_id')


class EntityQuerySet(CommonQuerySet):
    """
    QuerySet des entités
    """

    # Propriétés liées à l'historisation
    _ignore_log = False
    _current_user = None
    _reason = None
    _from_admin = False
    _force_default = False

    def delete(self, _ignore_log=None, _current_user=None, _reason=None, _force_default=None):
        """
        Surcharge de la suppression des entités du QuerySet
        :param _ignore_log: Ignorer l'historique de suppression ?
        :param _current_user: Utilisateur à l'origine de la suppression
        :param _reason: Raison de la suppression
        :param _force_default: Force la suppression directe ?
        """
        if _force_default or self._force_default:
            return super().delete()

        assert self.query.can_filter(), _("Cannot use 'limit' or 'offset' with delete.")
        if self._fields is not None:
            raise TypeError(_("Cannot call delete() after .values() or .values_list()"))

        del_query = self._clone()
        for element in del_query:
            element._ignore_log = _ignore_log or self._ignore_log
            element._current_user = _current_user or self._current_user or get_current_user()
            element._reason = _reason or self._reason
            element._from_admin = self._from_admin

        del_query._for_write = True
        del_query.query.select_for_update = False
        del_query.query.select_related = False
        del_query.query.clear_ordering(force_empty=True)

        collector = Collector(using=del_query.db)
        collector.collect(del_query)
        deleted, _rows_count = collector.delete()

        self._result_cache = None
        return deleted, _rows_count

    def create(self, _ignore_log=None, _current_user=None, _reason=None, _force_default=None, **kwargs):
        """
        Surcharge de la création d'entités
        :param _ignore_log: Ignorer l'historique de création ?
        :param _current_user: Utilisateur à l'origine de la création
        :param _reason: Raison de la création
        :param _force_default: Force la suppression directe ?
        """
        if _force_default:
            return super().create(**kwargs)
        obj = self.model(**kwargs)
        obj.save(
            force_insert=True,
            using=self.db,
            _ignore_log=_ignore_log,
            _current_user=_current_user or get_current_user(),
            _reason=_reason)
        return obj

    def distinct_on_fields(self, *fields, order_by=False):
        """
        Permet de faire un distinct sur un/des champs précis du modèle sur tous les backends
        (pgsql est le seul backend à supporter le distinct on fields, cependant, il ne permet pas l'order_by ensuite)
        :param fields: Liste des champs sur lequel appliquer le distinct
        :param order_by: Indique si le QuerySet sera suivi ou non d'un order_by sur des champs différents
        :return: QuerySet
        """
        from django.db import connection
        if not order_by and connection.vendor == 'postgresql':
            return self.distinct(*fields)

        from django.db.models import Max, Q
        groups = self.values(*fields).annotate(max_modification_date=Max('modification_date'))
        filters = Q()
        for item in groups:
            field_filter = {field: item[field] for field in fields if field != 'modification_date'}
            filters |= Q(modification_date=item['max_modification_date'], **field_filter)
        return self.filter(filters)

    def get_by_natural_key(self, *args, **kwargs):
        """
        Recherche une instance du modèle par sa clé naturelle
        :param natural_key: Clé naturelle (par défaut l'UUID de l'instance)
        :return: Instance du modèle
        """
        return self.get(**dict(zip(self.model._natural_key, args)))


class Entity(CommonModel):
    """
    Entité de base
    """
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name=_("UUID"))
    creation_date = models.DateTimeField(auto_now_add=True, verbose_name=_("date de création"))
    modification_date = models.DateTimeField(auto_now=True, verbose_name=_("date de modification"))
    globals = GenericRelation(Global)
    objects = EntityQuerySet.as_manager()

    # Propriétés liées à l'historisation
    _ignore_log = False
    _ignore_global = False
    _current_user = None
    _reason = None
    _from_admin = False
    _restore = False
    _force_default = False
    _history = None
    _init = False
    _natural_key = ('uuid', )

    def save(self, *args, _ignore_log=None, _current_user=None, _reason=None,
             _force_default=None, force_insert=False, **kwargs):
        """
        Surcharge de la sauvegarde de l'entité
        :param _ignore_log: Ignorer l'historique de modification ?
        :param _current_user: Utilisateur à l'origine de la modification
        :param _reason: Raison de la modification
        :param _force_default: Force le comportement par défaut ?
        """
        if _force_default:
            return super().save(*args, **kwargs)
        self._ignore_log = _ignore_log or self._ignore_log
        self._current_user = _current_user or self._current_user or get_current_user()
        self._reason = _reason or self._reason
        self._force_default = _force_default or self._force_default
        if force_insert:
            self.pk = self.id = None
            self.uuid = uuid.uuid4()
        return super().save(*args, **kwargs)

    def delete(self, *args, _ignore_log=None, _current_user=None, _reason=None,
               _force_default=None, keep_parents=False, **kwargs):
        """
        Surcharge de la suppression de l'entité
        :param _ignore_log: Ignorer l'historique de suppression ?
        :param _current_user: Utilisateur à l'origine de la suppression
        :param _reason: Raison de la suppression
        :param _force_default: Force le comportement par défaut ?
        """
        if _force_default:
            return super().delete(*args, **kwargs)

        assert self.pk is not None, _(
            "{} can't be deleted because it doesn't exists in database.").format(self._meta.object_name)
        self._ignore_log = _ignore_log or self._ignore_log
        self._current_user = _current_user or self._current_user or get_current_user()
        self._reason = _reason or self._reason
        self._force_default = _force_default or self._force_default

        from django.db import router
        using = kwargs.get('using', False) or router.db_for_write(self.__class__, instance=self)
        collector = Collector(using=using)
        collector.collect([self], keep_parents=keep_parents)
        for instances in collector.data.values():
            for instance in instances:
                instance._ignore_log = self._ignore_log
                instance._current_user = self._current_user
                instance._reason = self._reason
                instance._force_default = self._force_default
                instance._from_admin = self._from_admin
        return collector.delete()

    def __init__(self, *args, **kwargs):
        if not self.__class__._init:
            for field in self._meta.concrete_fields + self._meta.many_to_many:
                if not field.remote_field or field.related_model is Global:
                    continue
                if isinstance(field, models.ForeignKey):
                    suffix = '_uid'
                    fget = lambda self, field_name=field.name: self._get_uid(field_name)
                    fset = lambda self, value, field_name=field.name: self._set_uid(field_name, value)
                elif isinstance(field, models.ManyToManyField):
                    suffix = '_uids'
                    fget = lambda self, field_name=field.name: self._get_uids(field_name)
                    fset = lambda self, values, field_name=field.name: self._set_uids(field_name, values)
                else:
                    continue
                setattr(self.__class__, field.name + suffix, property(fget, fset))
            self.__class__._init = True
        super().__init__(*args, **kwargs)

    def _get_uid(self, fk_field):
        return getattr(self, fk_field).uuid

    def _set_uid(self, fk_field, value):
        field = self._meta.get_field(fk_field)
        unique = Global.objects.select_related().get(object_uid=value)
        model_from = unique.content_type.model_class()
        model_to = field.related_model
        assert model_from == model_to, _("Unexpected model '{}' used instead of expected model '{}'.").format(
            model_from._meta.verbose_name_raw, model_to._meta.verbose_name_raw
        )
        setattr(self, fk_field + '_id', unique.object_id)

    def _get_uids(self, m2m_field):
        return getattr(self, m2m_field).values_list('uuid', flat=True)

    def _set_uids(self, m2m_field, values):
        if not values:
            getattr(self, m2m_field).clear()
            return
        field = self._meta.get_field(m2m_field)
        uniques = Global.objects.select_related().filter(object_uid__in=values)
        assert uniques.values_list('content_type', flat=True).distinct(
        ).count() == 1, _("Multiple model types are found in values.")
        model_from = uniques.first().content_type.model_class()
        model_to = field.related_model
        assert model_from == model_to, _("Unexpected model '{}' used instead of expected model '{}'.").format(
            model_from._meta.verbose_name_raw, model_to._meta.verbose_name_raw
        )
        ids = uniques.values_list('object_id', flat=True)
        getattr(self, m2m_field).set(ids)

    def __json__(self):
        """
        Représentation de l'instance sous forme de dictionnaire pour sérialisation JSON
        :return: dict
        """
        data = super().__json__()
        data.update(_current_user=get_data_from_object(self._current_user),
                    _reason=self._reason, _from_admin=self._from_admin, _restore=self._restore,
                    _ignore_log=self._ignore_log, _force_default=self._force_default)
        return data

    @staticmethod
    def from_uuid(uuid):
        """
        Permet de récupérer une instance d'entité à partir de son UUID
        :param uuid: UUID
        :return: Instance
        """
        reference = Global.objects.filter(object_uid=uuid).first()
        if reference:
            return reference.entity
        return None

    class Meta:
        abstract = True


class PerishableEntityQuerySet(EntityQuerySet):
    """
    QuerySet des entités périssables
    """

    def select_valid(self, date=None, valid=True):
        """
        Sélectionne les éléments valides du QuerySet
        :param date: Date de référence (facultatif)
        :param valid: Retourne les éléments valides ou invalides (valides par défaut)
        :return: QuerySet
        """
        if valid is None:
            return self
        date = date or now()
        query = Q(start_date__lte=date, end_date__gte=date)
        query |= Q(start_date__lte=date, end_date__isnull=True)
        if not valid:
            return self.exclude(query)
        return self.filter(query)

    valid = property(select_valid)


class PerishableEntity(Entity):
    """
    Entité périssable
    """
    start_date = models.DateTimeField(blank=True, null=True, verbose_name=_("date d'effet"))
    end_date = models.DateTimeField(blank=True, null=True, verbose_name=_("date de fin"))
    objects = PerishableEntityQuerySet.as_manager()

    def save(self, *args, _ignore_log=None, _current_user=None, _reason=None, _force_default=None,
             force_insert=False, force_update=False, **kwargs):
        """
        Surcharge de la sauvegarde de l'entité périssable
        :param _ignore_log: Ignorer l'historique de modification ?
        :param _current_user: Utilisateur à l'origine de la modification
        :param _reason: Raison de la modification
        :param _force_default: Force le comportement par défaut ?
        :param force_insert: Force l'insertion des données ?
        :param force_update: Force la mise à jour des données ?
        """
        current_date = now()
        self._ignore_log = _ignore_log or self._ignore_log
        self._current_user = _current_user or self._current_user or get_current_user()
        self._reason = _reason or self._reason
        self._force_default = force_insert or force_update or _force_default or self._force_default
        self.start_date = self.start_date or current_date
        previous = None
        if self.pk and not self._force_default:
            previous = self.__class__.objects.get(pk=self.pk)
            previous.end_date = self.end_date or current_date
            previous.save(_ignore_log=self._ignore_log, _current_user=self._current_user, _reason=self._reason,
                          _force_default=True, force_insert=force_insert, force_update=force_update, **kwargs)
            self.pk = self.id = self.end_date = None
            self.uuid = uuid.uuid4()
            self.start_date = previous.end_date or current_date
        super().save(_ignore_log=self._ignore_log, _current_user=self._current_user, _reason=self._reason,
                     _force_default=self._force_default, force_insert=force_insert, force_update=force_update, **kwargs)
        if not self._force_default and previous:
            for metadata in previous.metadata.all():
                metadata.pk = metadata.id = None
                metadata.entity = self
                metadata.save(force_insert=True)
        self._force_default = False

    def delete(self, *args, _ignore_log=None, _current_user=None, _reason=None, _force_default=None, **kwargs):
        """
        Surcharge de la suppression de l'entité périssable
        :param _ignore_log: Ignorer l'historique de suppression ?
        :param _current_user: Utilisateur à l'origine de la suppression
        :param _reason: Raison de la suppression
        :param _force_default: Force le comportement par défaut ?
        """
        self._ignore_log = _ignore_log or self._ignore_log
        self._current_user = _current_user or self._current_user or get_current_user()
        self._reason = _reason or self._reason
        self._force_default = _force_default or self._force_default
        if self.pk and not self._force_default:
            self.end_date = self.end_date or now()
            return self.save(_ignore_log=_ignore_log, _current_user=_current_user, _reason=_reason, _force_default=True)
        else:
            return super().delete(_ignore_log=_ignore_log, _current_user=_current_user, _reason=_reason, **kwargs)

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError(_("La date de fin doit être ultérieure à la date d'effet."), code='incorrect_dates')

    @to_boolean(_("Valide"))
    def valid(self):
        return self.start_date <= now() and (self.end_date is None or self.end_date >= now())

    class Meta:
        abstract = True
        index_together = ['start_date', 'end_date']


class BaseWebhook(CommonModel):
    """
    Implémentation de base des WebHooks
    """
    FORMAT_JSON = 'json'
    FORMAT_XML = 'xml'
    FORMAT_YAML = 'yaml'
    FORMATS = (
        (FORMAT_JSON, _("JSON")),
        (FORMAT_XML, _("XML")),
        (FORMAT_YAML, _("YAML")),
    )

    METHOD_POST = 'post'
    METHOD_PUT = 'put'
    METHOD_PATCH = 'patch'
    METHODS = (
        (METHOD_POST, _("POST")),
        (METHOD_PUT, _("PUT")),
        (METHOD_PATCH, _("PATCH")),
    )

    AUTHORIZATION_BASIC = 'Basic'
    AUTHORIZATION_DIGEST = 'Digest'
    AUTHORIZATION_TOKEN = 'Token'
    AUTHORIZATION_BEARER = 'Bearer'
    AUTHORIZATION_JWT = 'JWT'
    AUTHORIZATIONS = (
        (AUTHORIZATION_BASIC, _("Basic")),
        (AUTHORIZATION_DIGEST, _("Digest")),
        (AUTHORIZATION_TOKEN, _("Token")),
        (AUTHORIZATION_BEARER, _("Bearer")),
        (AUTHORIZATION_JWT, _("JWT")),
    )

    SERIALIZERS = {
        FORMAT_JSON: JSONRenderer(),
        FORMAT_XML: XMLRenderer() if XMLRenderer else None,
        FORMAT_YAML: YAMLRenderer() if YAMLRenderer else None,
    }

    CONTENT_TYPES = {
        FORMAT_JSON: 'application/json',
        FORMAT_XML: 'application/xml',
        FORMAT_YAML: 'application/x-yaml',
    }

    STATUS_FILTERS = {
        History.CREATE: 'is_create',
        History.UPDATE: 'is_update',
        History.DELETE: 'is_delete',
        History.RESTORE: 'is_restore',
        History.M2M: 'is_m2m',
    }

    name = models.CharField(max_length=100, blank=True, null=True, verbose_name=_("nom"))
    url = models.URLField(verbose_name=_("url"))
    method = models.CharField(max_length=5, default=METHOD_POST, choices=METHODS, verbose_name=_("method"))
    format = models.CharField(max_length=4, default=FORMAT_JSON, choices=FORMATS, verbose_name=_("format"))
    authorization = models.CharField(
        max_length=6, choices=AUTHORIZATIONS, blank=True, null=True, verbose_name=_("authentification"))
    token = models.TextField(blank=True, null=True, verbose_name=_("token"))
    timeout = models.PositiveSmallIntegerField(default=30, verbose_name=_("délai d'attente"))
    retries = models.PositiveSmallIntegerField(default=0, verbose_name=_("tentatives"))
    delay = models.PositiveSmallIntegerField(default=0, verbose_name=_("délai entre tentatives"))

    def serialize_data(self, data):
        """
        Serialize les données fournies en fonction du type attendu
        :param data: Données brutes à sérializer
        :return: Données sérializées
        """
        if self.format in self.SERIALIZERS:
            serializer = self.SERIALIZERS.get(self.format)
            if not serializer:
                return None
            return serializer.render(data)
        return data

    @staticmethod
    def send_websocket(data):
        """
        Broadcast du message par websocket (si activé)
        :param data: Données à transmettre
        :return: Rien
        """
        try:
            from common.websocket import send_message
            from common.utils import json_encode
            send_message(json_encode(data))
        except Exception as error:
            logger.error(error, exc_info=True)

    def send_http(self, data):
        """
        Transmission du message par requête HTTP aux différentes APIs référencées
        :param data: Données à transmettre
        :return: Rien
        """
        try:
            from requests import request, RequestException
        except ImportError:
            return

        # Fabrication de l'entête de la requête HTTP
        headers = {}
        if self.authorization and self.token:
            headers['Authorization'] = '{type} {token}'.format(type=self.authorization, token=self.token)
        headers['Content-Type'] = self.CONTENT_TYPES.get(self.format, 'application/x-www-form-urlencoded')

        # Envoi de la requête (en plusieurs tentatives si configuré)
        for retries in range(self.retries + 1):
            try:
                serialized_data = self.serialize_data(data)
                request(self.method, self.url, data=serialized_data, headers=headers, timeout=self.timeout)
                break
            except RequestException as error:
                logger.warning(error)
                time.sleep(self.delay)
                continue
            except Exception as error:
                logger.error(error, exc_info=True)
                break

    def __str__(self):
        return self.name

    class Meta:
        abstract = True


class Webhook(BaseWebhook):
    """
    Webhook
    """
    types = models.ManyToManyField(ContentType, blank=True, verbose_name=_("types"))
    is_create = models.BooleanField(default=True, verbose_name=_("création"))
    is_update = models.BooleanField(default=True, verbose_name=_("modification"))
    is_delete = models.BooleanField(default=True, verbose_name=_("suppression"))
    is_restore = models.BooleanField(default=True, verbose_name=_("restauration"))
    is_m2m = models.BooleanField(default=True, verbose_name=_("many-to-many"))

    class Meta(BaseWebhook.Meta):
        verbose_name = _("webhook")
        verbose_name_plural = _("webhooks")


@receiver(post_init)
def post_init_receiver(sender, instance, *args, **kwargs):
    """
    Exécuté après chaque initialisation d'entité
    :param sender: Type d'entité
    :param instance: Instance de l'entité
    :return: Rien
    """
    if isinstance(instance, CommonModel):
        # Copie des données de l'entité
        instance._copy = instance.to_dict(editables=True)


@receiver(pre_save)
def pre_save_receiver(sender, instance, raw, *args, **kwargs):
    """
    Exécuté avant chaque sauvegarde d'entité
    :param sender: Type d'entité
    :param instance: Instance de l'entité
    :param raw: Entité créée depuis les fixtures ?
    :return: Rien
    """
    # Désactive la sauvegarde de l'historique pour cette entité
    if raw and isinstance(instance, Entity):
        instance._ignore_log = True
        instance._force_default = True
        return


@receiver(post_save)
def post_save_receiver(sender, instance, created, raw, *args, **kwargs):
    """
    Exécuté après chaque sauvegarde d'entité
    :param sender: Type d'entité
    :param instance: Instance de l'entité
    :param created: Entité nouvellement créée ?
    :param raw: Entité créée depuis les fixtures ?
    :return: Rien
    """
    if isinstance(instance, Entity):
        # Ajoute le point d'entrée global de l'entité
        if created and not instance._meta.pk.remote_field:
            if not settings.IGNORE_GLOBAL and not instance._ignore_global:
                Global.objects.create(content_type=instance.model_type, object_id=instance.pk, object_uid=instance.uuid)
        # Sauvegarde l'historique de modification
        if raw:
            instance._ignore_log = True
            instance._force_default = True
            return
        if not settings.IGNORE_LOG and not instance._ignore_log:
            log_save.apply_async(args=(instance, created, ), retry=False)
    if isinstance(instance, CommonModel):
        # Alerte des changements potentiels
        status = History.CREATE if created else History.UPDATE
        run_notify_changes(instance, status)
        # Copie des données de l'entité
        instance._copy = instance.to_dict(editables=True)


@app.task(ignore_result=True, name='common.log_save')
def log_save(instance, created):
    """
    Enregistre un historique de création/modification de l'entité
    :param instance: Instance de l'entité
    :param created: Entité nouvellement créée ?
    :return: Rien
    """
    # Sauvegarde la création/modification de l'entité
    if settings.IGNORE_LOG or instance._ignore_log:
        return
    user = instance._current_user
    if user and not user.pk:
        user = None
    # Vérification des changements entre les anciennes et nouvelles données
    old_data = instance._copy
    new_data = instance.to_dict(editables=True)
    diff = set(new_data.items()) ^ set(old_data.items())
    if not diff:
        return
    # Sauvegarde l'historique de création ou de modification
    history = History.objects.create(
        user=user,
        status=History.RESTORE if instance._restore else [History.UPDATE, History.CREATE][created],
        content_type=instance.model_type,
        object_id=instance.pk,
        object_uid=instance.uuid,
        object_str=str(instance),
        reason=instance._reason,
        data=old_data,
        admin=instance._from_admin)
    instance._history = history
    # Sauvegarde les champs modifiés
    if history.status != History.UPDATE:
        return
    for key in new_data:
        old_value = old_data.get(key, None)
        new_value = new_data.get(key, None)
        if old_value == new_value:
            continue
        HistoryField.objects.create(
            history=history,
            field_name=key,
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            data=old_value)
    logger.debug("Create/update log saved for entity {} #{} ({})".format(
        instance._meta.object_name, instance.pk, instance.uuid))


COPY_M2M_ACTIONS = ['pre_clear', 'pre_add', 'pre_remove']
LOG_M2M_ACTIONS = {
    'post_clear': HistoryField.CLEAR_M2M,
    'post_add': HistoryField.ADD_M2M,
    'post_remove': HistoryField.REMOVE_M2M,
}


@receiver(m2m_changed)
def m2m_changed_receiver(sender, instance, model, action, *args, **kwargs):
    """
    Exécuté après chaque sauvegarde d'entité contenant des champs many-to-many
    :param sender: Type de l'entité de liaison
    :param instance: Instance de l'entité porteuse de la relation
    :param model: Modèle lié à la relation many-to-many
    :param action: Action exécutée
    :return: Rien
    """
    status_m2m = LOG_M2M_ACTIONS.get(action)
    if isinstance(instance, Entity):
        if status_m2m and not settings.IGNORE_LOG and not instance._ignore_log:
            # Sauvegarde l'historique des changements de champs many-to-many
            log_m2m.apply_async(args=(instance, model, status_m2m, ), retry=False)
    if isinstance(instance, CommonModel):
        if action in COPY_M2M_ACTIONS:
            # Copie les anciennes données des champs many-to-many
            instance._copy_m2m = instance.m2m_to_dict()
        if status_m2m:
            # Alerte d'un changement dans les many-to-many
            run_notify_changes(instance, History.M2M, status_m2m)


@app.task(ignore_result=True, name='common.log_m2m')
def log_m2m(instance, model, status_m2m):
    """
    Enregistre un historique de modification des relations de type ManyToMany de l'entité
    :param instance: Instance de l'entité
    :param model: Modèle lié à la relation ManyToMany
    :param status: Statut de modification de la relation
    :return: Rien
    """
    # Sauvegarde la mise à jour de relations M2M de l'entité
    if settings.IGNORE_LOG or instance._ignore_log:
        return
    user = instance._current_user
    if user and not user.pk:
        user = None
    old_m2m = instance._copy_m2m
    new_m2m = instance.m2m_to_dict()
    for field in set(old_m2m) | set(new_m2m):
        # S'il n'y a aucun changement entre les anciennes et nouvelles données
        old_value = old_m2m.get(field, [])
        new_value = new_m2m.get(field, [])
        diff = set(old_value) ^ set(new_value)
        if not diff:
            continue
        # Sauvegarde de l'historique si ce n'est pas déjà fait
        history = instance._history
        if not history:
            history = History.objects.create(
                user=user,
                status=History.M2M,
                content_type=instance.model_type,
                object_id=instance.pk,
                object_uid=instance.uuid,
                object_str=str(instance),
                reason=instance._reason,
                data=None,
                admin=instance._from_admin)
            instance._history = history
        # Sauvegarde la relation modifiée
        labels = {e.pk: str(e) for e in model.objects.filter(id__in=set(old_value + new_value))}
        field = HistoryField.objects.create(
            history=history,
            field_name=field,
            old_value=' | '.join(labels[id] for id in old_value if id in labels) if old_value is not None else None,
            new_value=' | '.join(labels[id] for id in new_value if id in labels) if new_value is not None else None,
            data=old_value,
            status_m2m=status_m2m)
        logger.debug("Many-to-many log saved for field '{}' in entity {} #{} ({})".format(
            field, instance._meta.object_name, instance.pk, instance.uuid))


@receiver(post_delete)
def post_delete_receiver(sender, instance, *args, **kwargs):
    """
    Exécuté après chaque suppression d'entité
    :param sender: Type de l'entité
    :param instance: Instance de l'entité
    :return: Rien
    """
    if isinstance(instance, Entity):
        # Supprime le point d'entrée global de l'entité
        # Global.objects.filter(object_uid=instance.uuid).delete()
        # Sauvegarde l'historique de suppression
        if not settings.IGNORE_LOG and not instance._ignore_log:
            log_delete.apply_async(args=(instance, ), retry=False)
    if isinstance(instance, CommonModel):
        # Alerte de la suppression
        run_notify_changes(instance, History.DELETE)


@app.task(ignore_result=True, name='common.log_delete')
def log_delete(instance):
    """
    Enregistre un historique de suppression de l'entité
    :param instance: Instance de l'entité
    :return: Rien
    """
    # Sauvegarde la suppression de l'entité
    if settings.IGNORE_LOG or instance._ignore_log:
        return
    user = instance._current_user
    if user and not user.pk:
        user = None
    data = instance.to_dict(editables=True)
    # Sauvegarde de l'historique de suppression
    history = History.objects.create(
        user=user,
        status=History.DELETE,
        content_type=instance.model_type,
        object_id=instance.pk,
        object_uid=instance.uuid,
        object_str=str(instance),
        reason=instance._reason,
        data=data,
        admin=instance._from_admin)
    instance._history = history
    logger.debug("Delete log saved for entity {} #{} ({})".format(
        instance._meta.object_name, instance.pk, instance.uuid))


def run_notify_changes(instance, status, status_m2m=None):
    """
    Notification des changements sur une entité (par broadcast websocket et/ou API callback)
    :param instance: Instance de l'entité
    :param status: Statut général du changement
    :param status_m2m: Sous-statut concernant un changement sur les champs many-to-many
    :return: Rien
    """
    filters = {Webhook.STATUS_FILTERS.get(status): True}
    filters.update(dict(types__in=[instance.model_type]))
    if settings.NOTIFY_CHANGES and (settings.WEBSOCKET_ENABLED or instance.has_webhook(status)):
        return notify_changes.apply_async(args=(instance, status, status_m2m, ), retry=False)


@app.task(ignore_result=True, name='common.notify_changes')
def notify_changes(instance, status, status_m2m=None):
    """
    Notification des changements sur une entité (par broadcast websocket et/ou API callback)
    :param instance: Instance de l'entité
    :param status: Statut général du changement
    :param status_m2m: Sous-statut concernant un changement sur les champs many-to-many
    :return: Rien
    """
    # Différences de données entre la version précédente et la version actuelle
    diff_data_prev, diff_data_next = None, None
    if status in [History.UPDATE, History.RESTORE]:
        old_data = instance._copy.items()
        new_data = instance.to_dict(editables=True).items()
        if set(new_data) ^ set(old_data):
            diff_data_prev = dict(set(old_data) - set(new_data))
            diff_data_next = dict(set(new_data) - set(old_data))
    has_diff_data = diff_data_prev and diff_data_next
    # Différences de many-to-many entre la version précédente et la version actuelle
    diff_m2m_prev, diff_m2m_next = {}, {}
    if status == History.M2M:
        old_m2m = instance._copy_m2m
        new_m2m = instance.m2m_to_dict()
        for field in set(old_m2m) | set(new_m2m):
            old_value = old_m2m.get(field, ())
            new_value = new_m2m.get(field, ())
            if set(old_value) ^ set(new_value):
                diff_m2m_prev[field] = list(set(old_value) - set(new_value))
                diff_m2m_next[field] = list(set(new_value) - set(old_value))
    has_diff_m2m = diff_m2m_prev and diff_m2m_next

    # Création du message à transmettre
    get_data = getattr(instance, 'get_webhook_data', lambda *a, **k: instance.to_dict(**settings.NOTIFY_OPTIONS))
    data = {
        'id': str(uuid.uuid4()),
        'date': now(),
        'meta': {
            'id': instance.pk,
            'uuid': getattr(instance, 'uuid', None),
            'type': model_to_dict(get_content_type(instance)),
            'status': status,
            'status_display': str(dict(History.LOG_STATUS).get(status, '')) or None,
            'status_m2m': status_m2m,
            'status_m2m_display': str(dict(HistoryField.LOG_STATUS_M2M).get(status_m2m, '')) or None,
        },
        'changes': {
            'data': {
                'previous': diff_data_prev,
                'current': diff_data_next,
            } if has_diff_data else None,
            'm2m': {
                'previous': diff_m2m_prev,
                'current': diff_m2m_next,
            } if has_diff_m2m else None,
        } if has_diff_data or has_diff_m2m else None,
        'data': get_data(status=status, status_m2m=status_m2m),
    }

    # Envoi des données par websocket si sctivé
    if settings.WEBSOCKET_ENABLED:
        Webhook.send_websocket(data)

    # Envoi des données par requête HTTP
    filters = {Webhook.STATUS_FILTERS.get(status): True}
    filters.update(dict(types__in=[instance.model_type]))
    for webhook in Webhook.objects.filter(**filters):
        webhook.send_http(data)

    return data


@receiver(post_save)
def create_token_and_metadata(sender, instance=None, created=False, **kwargs):
    """
    Génération du jeton d'authentification pour Django REST Framework
    """
    if created:
        if issubclass(sender, get_user_model()):
            try:
                from rest_framework.authtoken.models import Token
                Token.objects.create(user=instance)
            except (AttributeError, ImportError):
                logger.warning("Unable to create API Token, are django-rest-framework with authtoken installed?")
            UserMetaData.objects.create(user=instance, data={})
        elif issubclass(sender, Group):
            GroupMetaData.objects.create(group=instance, data={})


class UserMetaData(CommonModel):
    """
    Métadonnées pour un utilisateur
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name=_("utilisateur"),
        related_name='metadata')
    data = JsonField(blank=True, null=True, verbose_name=_("données"))

    @staticmethod
    def set(user, **data):
        meta = user.metadata
        meta.data.update(**data)
        meta.save()
        return meta.data

    @staticmethod
    def get(user, key=None, groups=True):
        if groups:
            data = {}
            for group in user.groups.select_related('metadata').all():
                merge_dict(data, group.metadata.data or {})
            merge_dict(data, user.metadata.data or {})
        else:
            data = user.metadata.data
        return data.get(key) if key else data

    @staticmethod
    def remove(user, key):
        meta = user.metadata
        meta.data.pop(key, None)
        meta.save()
        return meta.data

    @staticmethod
    def merge(user, *idict, **data):
        meta = user.metadata
        merge_dict(meta.data, *idict, **data)
        meta.save()
        return meta.data

    def __str__(self):
        return str(self.user)

    class Meta:
        verbose_name = _("métadonnées d'utilisateur")
        verbose_name_plural = _("métadonnées d'utilisateurs")


class GroupMetaData(CommonModel):
    """
    Métadonnées pour un groupe
    """
    group = models.OneToOneField(
        'auth.Group',
        on_delete=models.CASCADE,
        verbose_name=_("groupe"),
        related_name='metadata')
    data = JsonField(blank=True, null=True, verbose_name=_("données"))

    @staticmethod
    def set(group, **data):
        meta = group.metadata
        meta.data.update(**data)
        meta.save()
        return meta.data

    @staticmethod
    def get(group, key=None):
        meta = group.metadata
        return meta.data.get(key) if key else meta.data

    @staticmethod
    def remove(group, key):
        meta = group.metadata
        meta.data.pop(key, None)
        meta.save()
        return meta.data

    @staticmethod
    def merge(group, *idict, **data):
        meta = group.metadata
        merge_dict(meta.data, *idict, **data)
        meta.save()
        return meta.data

    def __str__(self):
        return str(self.group)

    class Meta:
        verbose_name = _("métadonnées de groupe")
        verbose_name_plural = _("métadonnées de groupes")


class ServiceUsage(CommonModel):
    """
    Utilisation et/ou restriction des APIs
    """
    name = models.CharField(max_length=200, verbose_name=_("nom"))
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name=_("utilisateur"))
    count = models.PositiveIntegerField(default=0, verbose_name=_("nombre"))
    limit = models.PositiveIntegerField(blank=True, null=True, verbose_name=_("limite"))
    address = models.CharField(max_length=40, verbose_name=_("adresse"))
    date = models.DateTimeField(auto_now=True, verbose_name=_("date"))

    def __str__(self):
        return _("{} ({} : {})").format(self.name, self.address, self.count)

    class Meta:
        verbose_name = _("utilisation de service")
        verbose_name_plural = _("utilisation des services")
        unique_together = ('name', 'user')


def get_object_from_data(data, from_db=False):
    """
    Permet de construire une instance d'un modèle quelconque à partir de sa représentation JSON
    :param data: Dictionnaire de données
    :param from_db: Récupérer l'instance depuis la base de données ?
    :return: Instance (si possible)
    """
    model = None
    if not isinstance(data, dict):
        return data
    if from_db and 'uuid' in data:
        return Entity.from_uuid(data['uuid'])
    if '_content_type' in data:
        content_type = ContentType(**data.pop('_content_type'))
        model = content_type.model_class()
        pk_field = get_pk_field(model).name
        if from_db and pk_field in data:
            return model.objects.filter(**{pk_field: data[pk_field]}).first()
    if model:
        instance = model()
        for key, value in data.items():
            setattr(instance, key, get_object_from_data(value))
        return instance
    return None


def get_data_from_object(instance, types=True, **options):
    """
    Tente d'extraire les données de l'instance d'un modèle
    :param instance: Instance
    :param types: Ajouter le type du modèle ?
    :param options: Options complémentaires de la méthode .to_dict()
    :return: Dictionnaire de données (si possible)
    """
    data = {}
    if not instance:
        return None
    elif hasattr(instance, 'to_dict'):
        data = instance.to_dict(types=types, **options)
    elif isinstance(instance, models.Model):
        data = model_to_dict(instance)
        data.pop('_state', None)  # Donnée non serialisable
        if types:
            content_type = ContentType.objects.get_for_model(instance)
            data.update(_content_type=get_data_from_object(content_type, types=False))
    elif hasattr(instance, '__dict__'):
        data = instance.__dict__
    return data


# Monkey-patch des utilisateurs et groupes pour ajouter les fonctions utilitaires de gestion des métadonnées
setattr(AbstractBaseUser, 'set_metadata', lambda self, **metas: UserMetaData.set(self, **metas))
setattr(AbstractBaseUser, 'get_metadata', lambda self, key=None, groups=True: UserMetaData.get(self, key=key, groups=groups))
setattr(AbstractBaseUser, 'del_metadata', lambda self, key: UserMetaData.remove(self, key))
setattr(AbstractBaseUser, 'merge_metadata', lambda self, *idict, **metas: UserMetaData.merge(self, *idict, **metas))
setattr(Group, 'set_metadata', lambda self, **metas: GroupMetaData.set(self, **metas))
setattr(Group, 'get_metadata', lambda self, key=None: GroupMetaData.get(self, key=key))
setattr(Group, 'del_metadata', lambda self, key: GroupMetaData.remove(self, key))
setattr(Group, 'merge_metadata', lambda self, *idict, **metas: GroupMetaData.merge(self, *idict, **metas))


# Common models
MODELS = [
    Global,
    MetaData,
    History,
    HistoryField,
    Webhook,
    UserMetaData,
    GroupMetaData,
]
