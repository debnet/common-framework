# coding: utf-8
from operator import itemgetter

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as ModelValidationError, FieldDoesNotExist
from rest_framework import serializers
from rest_framework.exceptions import ValidationError as ApiValidationError
from rest_framework.fields import empty
from rest_framework.relations import PrimaryKeyRelatedField
from rest_framework.serializers import HyperlinkedModelSerializer, ALL_FIELDS
from rest_framework.settings import api_settings

from common.api.fields import CustomHyperlinkedIdentityField, CustomHyperlinkedRelatedField
from common.api.utils import create_model_serializer, to_model_serializer
from common.utils import get_pk_field


# URLs dans les serializers
HYPERLINKED = settings.REST_FRAMEWORK.get('HYPERLINKED', False)


class CommonModelSerializer(serializers.HyperlinkedModelSerializer if HYPERLINKED else serializers.ModelSerializer):
    """
    Définition commune de ModelSerializer pour l'API REST
    """
    metadata = serializers.SerializerMethodField(read_only=True)

    serializer_url_field = CustomHyperlinkedIdentityField
    serializer_related_field = CustomHyperlinkedRelatedField if HYPERLINKED else PrimaryKeyRelatedField

    def get_metadata(self, instance):
        request = self.context.get('request', None)
        meta = request and getattr(request, 'query_params', None) and request.query_params.get('meta', False)
        if meta and hasattr(instance, 'metadata'):
            # Soit un QuerySet de MetaData soit un lien vers un modèle ayant un champ "data" (voir User/Group)
            return instance.metadata.data if hasattr(instance.metadata, 'data') \
                else {meta.key: meta.value for meta in instance.metadata.all() if meta.valid}
        return None

    def __init__(self, *args, **kwargs):
        """
        Surcharge de l'initialisateur pour reprendre le nom de la clé primaire
        """
        if HYPERLINKED and self.Meta.model:
            pk_field = get_pk_field(self.Meta.model)
            pk_field_in_excludes = pk_field.name in getattr(self.Meta, 'exclude', [])
            pk_field_in_fields = any(f in getattr(self.Meta, 'fields', [f]) for f in (pk_field.name, ALL_FIELDS))
            if not pk_field_in_excludes and pk_field_in_fields and pk_field.name not in self._declared_fields:
                field, options = self.build_standard_field(pk_field.name, pk_field)
                if hasattr(field, 'view_name') and pk_field.related_model:  # Arbitraire
                    options['view_name'] = '{}-detail'.format(pk_field.related_model._meta.model_name)
                self._declared_fields[pk_field.name] = field(**options)
                self._declared_fields.move_to_end(pk_field.name, last=False)
        super().__init__(*args, **kwargs)

    def create(self, validated_data):
        """
        Surcharge la création de l'instance pour effectuer la validation complète
        :param validated_data: Données validées
        :return: Instance créée
        """
        try:
            model = self.Meta.model
            model_field_names = [field.name for field in model._meta.fields]
            instance = model(**{key: value for key, value in validated_data.items() if key in model_field_names})
            instance.full_clean()
        except ModelValidationError as error:
            raise ApiValidationError(error.messages)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        """
        Surcharge la mise à jour de l'instance pour effectuer la validation complète
        :param instance: Instance à mettre à jour
        :param validated_data: Données validées
        :return: Instance mise à jour
        """
        m2m_data = {}
        for attr, value in validated_data.items():
            try:
                field = instance._meta.get_field(attr)
                if field.many_to_many:
                    m2m_data[attr] = value
                    continue
            except FieldDoesNotExist:
                continue
            setattr(instance, attr, value)
        try:
            instance.full_clean()
            instance.save()
            for attr, value in m2m_data.items():
                getattr(instance, attr).set(value)
        except ModelValidationError as error:
            raise ApiValidationError(error.messages)
        return instance

    def to_internal_value(self, data):
        """
        Permet de gérer l'affectation d'un ID (FK) ou une liste d'IDs (M2M) à la place de l'objet complet (dict)
        """
        if isinstance(data, dict):
            return super().to_internal_value(data)
        model = self.Meta.model
        if isinstance(data, list):
            return model.objects.values().filter(pk__in=data)
        return model.objects.values().get(pk=data)


class BaseCustomSerializer(serializers.Serializer):
    """
    Serializer de base
    """
    def _append_non_field_error(self, error):
        errors = self.errors.get(api_settings.NON_FIELD_ERRORS_KEY, [])
        errors.append(error)
        self._errors.update({api_settings.NON_FIELD_ERRORS_KEY: errors})

    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass


class CustomHyperlinkedModelSerializer(HyperlinkedModelSerializer):
    """
    Surcharge du serializer de modèle avec URLs
    """
    serializer_url_field = CustomHyperlinkedIdentityField
    serializer_related_field = CustomHyperlinkedRelatedField


class GenericFormSerializer(CommonModelSerializer):
    """
    Serializer générique gérant le create des related_objects imbriqués pour la génération de formulaire de modèle
    """
    serializer_related_field = PrimaryKeyRelatedField

    def __init__(self, instance=None, data=empty, label_singulier=None, formats=None, **kwargs):
        """
        Surcharge de l'init pour accepter le label_singulier et les formats
        """
        self.label_singulier = label_singulier
        self.formats = formats or {}
        super().__init__(instance, data, **kwargs)

    def create(self, validated_data):
        """
        Gestion de la création des modèles imbriqués (O2M & O2O)
        """
        model = self.Meta.model
        # Récupère la liste des noms de champs relatifs au modèle
        field_names = {field.name for field in model._meta.get_fields()}
        related_objects = {}
        for related_object in model._meta.related_objects:
            if not related_object.one_to_many and not related_object.one_to_one:
                continue
            accessor_name = related_object.get_accessor_name()
            if accessor_name not in validated_data:
                continue
            # Récupération du field_name associé (en cas de renommage du champ)
            field_name = accessor_name
            for name, field in self.fields.fields.items():
                if field.source == accessor_name:
                    field_name = name
                    break
            if field_name in self._declared_fields:
                related_objects[(field_name, accessor_name)] = related_object
        # Récupère les données propres à chaque relation inversée tout en les supprimant des données du modèle courant
        relations_data = {
            field_name: validated_data.pop(relation_name)
            for field_name, relation_name in related_objects.keys()}
        # Données externes au modèle
        donnees_externes = {key: value for key, value in validated_data.items() if key not in field_names}
        # Sauvegarde l'instance du modèle courant
        item = self.create_object(validated_data)
        # Traite les données des relations inversées
        for (field_name, relation_name), related_object in related_objects.items():
            relation_data = relations_data.get(field_name)
            if relation_data:
                # Injecte dans chaque objet les données non relatives au modèle
                for relation_item in ([relation_data] if isinstance(relation_data, dict) else relation_data):
                    relation_item.update(**donnees_externes)
                # Appel du create pour le ListSerializer des one_to_many
                if related_object.one_to_many:
                    for relation_item in relation_data:
                        relation_item[related_object.field.name] = item
                    type(self._declared_fields.get(field_name).child)(context=self.context, many=True)\
                        .create(relation_data)
                # Appel du create pour le serializer des one_to_one
                elif related_object.one_to_one:
                    relation_data[related_object.field.name] = item
                    type(self._declared_fields.get(field_name))(context=self.context).create(relation_data)
        return item

    def create_object(self, validated_data):
        """
        Fonction permettant de créer les objets à la validation du formulaire
        (cette fonction est à surcharger pour induire des comportements de créations d'objets spécifiques)
        :param validated_data: Données validées
        :return: Instance
        """
        return super().create(validated_data)


# Modèle utilisateur courant
User = get_user_model()


@to_model_serializer(User)
class UserSerializer(serializers.ModelSerializer):
    """
    Serializer spécifique pour la création et la mise à jour d'un utilisateur
    """
    password = serializers.CharField(required=False, write_only=True, style={'input_type': 'password'})

    def create(self, validated_data):
        groups = validated_data.pop('groups', [])
        permissions = validated_data.pop('user_permissions', [])
        is_superuser = validated_data.pop('is_superuser', False)
        password = validated_data.get('password', None)
        try:
            validate_password(password)
        except ModelValidationError as error:
            raise ApiValidationError({'password': error.messages})
        if is_superuser:
            user = User.objects.create_superuser(**validated_data)
        else:
            user = User.objects.create_user(**validated_data)
        user.groups.set(groups)
        user.user_permissions.set(permissions)
        return user

    def update(self, instance, validated_data):
        groups = validated_data.pop('groups', None) or instance.groups.all()
        permissions = validated_data.pop('user_permissions', None) or instance.user_permissions.all()
        password = validated_data.pop('password', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            try:
                validate_password(password, user=instance)
            except ModelValidationError as error:
                raise ApiValidationError({'password': error.messages})
            instance.set_password(password)
        instance.save()
        instance.groups.set(groups)
        instance.user_permissions.set(permissions)
        return instance


@to_model_serializer(User, exclude=['user_permissions'])
class UserInfosSerializer(CommonModelSerializer):
    """
    Serializer de la synthèse des informations utilisateur (groupes, permissions et métadonnées)
    """
    username = serializers.CharField(required=False)
    password = serializers.CharField(required=False, write_only=True, style={'input_type': 'password'})

    groups = create_model_serializer(Group, exclude=['permissions'])(many=True)
    permissions = serializers.SerializerMethodField()

    def get_permissions(self, user):
        permissions = {}
        permission_serializer_class = create_model_serializer(Permission, fields=['id', 'codename', 'name'])
        for group in user.groups.all():
            for permission in group.permissions.all():
                if permission.id not in permissions:
                    permissions[permission.id] = permission_serializer_class(permission).data
        for permission in user.user_permissions.all():
            if permission.id not in permissions:
                permissions[permission.id] = permission_serializer_class(permission).data
        return sorted(permissions.values(), key=itemgetter('id'))

    def get_metadata(self, user):
        return user.get_metadata()
