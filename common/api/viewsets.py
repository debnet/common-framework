# coding: utf-8
from rest_framework import serializers
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from common.api.utils import RESERVED_QUERY_PARAMS, url_value
from common.models import Entity
from common.settings import settings
from common.utils import str_to_bool


class CommonModelViewSet(viewsets.ModelViewSet):
    """
    Définition commune de ModelViewSet pour l'API REST
    """

    def get_serializer_class(self):
        # Le serializer par défaut est utilisé en cas de modification/suppression
        default_serializer = getattr(self, 'default_serializer', None)
        if default_serializer and self.action not in ['list', 'retrieve']:
            return default_serializer

        # Le serializer peut être substitué en fonction des paramètres d'appel de l'API
        params = self.request.query_params
        if default_serializer:
            if 'fields' in params:
                # Un serializer avec restriction des champs est créé à la volée
                return type(default_serializer.__name__, (serializers.Serializer, ), {
                    field: serializers.ReadOnlyField(source=field.replace('__', '.') if '__' in field else None)
                    for field in params.get('fields').split(',')})
            elif 'simple' in params:
                return default_serializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        if issubclass(serializer.Meta.model, Entity):
            return serializer.save(_current_user=self.request.user)
        return super().perform_create(serializer)

    def perform_update(self, serializer):
        if issubclass(serializer.Meta.model, Entity):
            return serializer.save(_current_user=self.request.user)
        return super().perform_update(serializer)

    def perform_destroy(self, instance):
        if isinstance(instance, Entity):
            return instance.delete(_current_user=self.request.user)
        return super().perform_destroy(instance)

    def paginate_queryset(self, queryset):
        # Uniquement si toutes les données sont demandées
        all_data = self.request.query_params.get('all', None)
        if all_data:
            return None
        return super().paginate_queryset(queryset)

    def get_queryset(self):
        options = dict(filters=None, order_by=None, distinct=None)
        reserved_query_params = RESERVED_QUERY_PARAMS + getattr(
            self.paginator, '_query_params',
            [self.paginator.page_query_param, self.paginator.page_size_query_param] if self.paginator else [])

        # Erreurs silencieuses
        silent = self.request.query_params.get('silent', None)

        # Requête simplifiée
        queryset = super().get_queryset()
        if self.request.query_params.get('simple', None):
            queryset = queryset.model.objects.all()
            try:
                fields = self.request.query_params.get('fields', None)
                relateds = set()
                for field in (fields or '').split(','):
                    *related, field_name = field.split('__')
                    if related:
                        relateds.add('__'.join(related))
                queryset = queryset.select_related(*relateds)
            except Exception as error:
                if not silent:
                    raise ValidationError(str(error))
        else:
            # Récupération des métadonnées
            metadatas = str_to_bool(self.request.query_params.get('meta', False))
            queryset = queryset.prefetch_related(*getattr(self, 'metadatas', []) if metadatas else [])

        # Filtres
        try:
            filters = {}
            excludes = {}
            for key, value in self.request.query_params.items():
                if key not in reserved_query_params:
                    if key.startswith('-'):
                        excludes[key[1:]] = url_value(key[1:], value)
                    else:
                        filters[key] = url_value(key, value)
            if filters:
                queryset = queryset.filter(**filters)
            if excludes:
                queryset = queryset.exclude(**excludes)
            if filters or excludes:
                options['filters'] = True
        except Exception as error:
            if not silent:
                raise ValidationError(str(error))
            options['filters'] = False
            if settings.DEBUG:
                options['filters_error'] = str(error)

        # Tris
        try:
            order_by = self.request.query_params.get('order_by', None)
            if order_by:
                temp_queryset = queryset.order_by(*order_by.split(','))
                str(temp_queryset.query)  # Force SQL evaluation to retrieve exception
                queryset = temp_queryset
                options['order_by'] = True
        except Exception as error:
            if not silent:
                raise ValidationError(str(error))
            options['order_by'] = False
            if settings.DEBUG:
                options['order_by_error'] = str(error)

        # Distinct
        try:
            distinct = self.request.query_params.get('distinct', None)
            if distinct:
                distincts = distinct.split(',')
                if str_to_bool(distinct) is not None:
                    distincts = []
                queryset = queryset.distinct(*distincts)
                options['distinct'] = True
        except Exception as error:
            if not silent:
                raise ValidationError(str(error))
            options['distinct'] = False
            if settings.DEBUG:
                options['distinct_error'] = str(error)

        # Ajout des options de filtres/tris dans la pagination
        if self.paginator and hasattr(self.paginator, 'additional_data'):
            self.paginator.additional_data = dict(options=options)
        return queryset


class UserViewSet(CommonModelViewSet):
    """
    ViewSet spécifique pour l'utilisateur
    """

    _default_data = {
        'is_active': True,
        'is_staff': False,
        'is_superuser': False,
        'groups': [],
        'user_permissions': [],
    }

    def check_permissions(self, request):
        # Autorise l'utilisateur à modifier ses propres informations ou les informations des utilisateurs non-staff
        if self.action in ['create']:
            # Autorise la création pour tout le monde
            return True
        elif self.action in ['update', 'partial_update']:
            # Autorise la modification de soi-même ou d'un autre utilisateur de rang inféreiru
            current_user = request.user
            user = self.get_object()
            if (current_user.is_staff and not user.is_staff) or (current_user == user):
                return
        # Applique le système de permissions dans les autres cas
        return super().check_permissions(request)

    def check_data(self, data):
        # Assure que l'utilisateur ne s'octroie pas des droits qu'il ne peut pas avoir
        user = self.request.user
        if not user:
            if not user.is_staff and not user.is_superuser:
                data['is_active'] = True
            if not user.is_superuser:
                data['is_staff'] = False
            if not user.is_superuser:
                data['is_superuser'] = False
        if 'groups' in data and data.get('groups'):
            if not user:
                data['groups'] = []
            elif not user.is_superuser:
                groups = user.groups.all()
                data['groups'] = list(set(groups) & set(data.get('groups')))
        if 'user_permissions' in data and data.get('user_permissions'):
            if not user:
                data['user_permissions'] = []
            elif not user.is_superuser:
                user_permissions = user.user_permissions.all()
                data['user_permissions'] = list(set(user_permissions) & set(data.get('user_permissions')))

    def perform_create(self, serializer):
        self.check_data(serializer.validated_data)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self.check_data(serializer.validated_data)
        super().perform_update(serializer)
