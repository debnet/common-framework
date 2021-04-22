# coding: utf-8
from django.core.exceptions import FieldDoesNotExist, EmptyResultSet
from django.db import ProgrammingError
from django.db.models.query import F, Prefetch, QuerySet
from rest_framework import serializers
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.schemas import AutoSchema

from common.api.utils import AGGREGATES, CACHE_PREFIX, CACHE_TIMEOUT, RESERVED_QUERY_PARAMS, url_value, parse_filters
from common.api.fields import ChoiceDisplayField, ReadOnlyObjectField
from common.models import Entity, MetaData
from common.settings import settings
from common.utils import get_field_by_path, get_pk_field, str_to_bool


class CommonModelViewSet(viewsets.ModelViewSet):
    """
    Définition commune de ModelViewSet pour l'API REST
    """
    url_params = {}
    schema = AutoSchema()

    def get_serializer_class(self):
        # Le serializer par défaut est utilisé en cas de modification/suppression
        default_serializer = getattr(self, 'default_serializer', None)
        if default_serializer and self.action not in ('list', 'retrieve', 'update', 'partial_update'):
            return default_serializer

        # Le serializer peut être substitué en fonction des paramètres d'appel de l'API
        query_params = getattr(self.request, 'query_params', None)
        url_params = self.url_params or (query_params.dict() if query_params else {})
        if default_serializer:

            # Fonction utilitaire d'ajout de champ au serializer
            def add_field_to_serializer(fields, field_name):
                if not field_name:
                    return
                field_name = field_name.strip()
                source = field_name.replace('.', '__')
                # Champ spécifique en cas d'énumération
                choices = getattr(get_field_by_path(self.queryset.model, field_name), 'flatchoices', None)
                if choices and str_to_bool(url_params.get('display')):
                    fields[field_name + '_display'] = ChoiceDisplayField(choices=choices, source=source)
                # Champ spécifique pour l'affichage de la valeur
                fields[field_name] = ReadOnlyObjectField(source=source if '.' in field_name else None)

            # Ajoute les champs d'aggregation au serializer
            aggregations = {}
            for aggregate in AGGREGATES.keys():
                for field in url_params.get(aggregate, '').split(','):
                    if not field:
                        continue
                    field_name = field.strip() + '_' + aggregate
                    source = field_name.replace('.', '__') if '.' in field else None
                    aggregations[field_name] = serializers.ReadOnlyField(source=source)

            # Ajoute les regroupements au serializer
            if 'group_by' in url_params or aggregations:
                fields = {}
                for field in url_params.get('group_by', '').split(','):
                    add_field_to_serializer(fields, field)
                fields.update(aggregations)
                # Un serializer avec les données regroupées est créé à la volée
                return type(default_serializer.__name__, (serializers.Serializer, ), fields)

            # Ajoute la restriction des champs au serializer
            elif 'fields' in url_params:
                fields = {}
                for field in url_params.get('fields').split(','):
                    add_field_to_serializer(fields, field)
                # Un serializer avec restriction des champs est créé à la volée
                return type(default_serializer.__name__, (serializers.Serializer, ), fields)

            # Utilisation du serializer simplifié
            elif str_to_bool(url_params.get('simple')):
                return getattr(self, 'simple_serializer', default_serializer)

            # Utilisation du serializer par défaut en cas de mise à jour sans altération des données
            elif self.action in ('update', 'partial_update'):
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

    def list(self, request, *args, **kwargs):
        # Détournement en cas d'aggregation sans annotation ou de non QuerySet
        queryset = self.get_queryset()
        if not isinstance(queryset, QuerySet):
            from rest_framework.response import Response
            return Response(queryset)
        try:
            return super().list(request, *args, **kwargs)
        except (AttributeError, FieldDoesNotExist) as error:
            self.queryset_error = error
            raise ValidationError(dict(error="fields: {}".format(error)), code='fields')

    def paginate_queryset(self, queryset):
        # Aucune pagination si toutes les données sont demandées ou qu'il ne s'agit pas d'un QuerySet
        if not isinstance(queryset, QuerySet) or str_to_bool(self.request.query_params.get('all', None)):
            return None
        try:
            return super().paginate_queryset(queryset)
        except ProgrammingError as error:
            raise ValidationError(dict(error="page: {}".format(error)), code='page')

    def get_queryset(self):
        # Evite la ré-évaluation du QuerySet en cas d'erreur
        if getattr(self, 'queryset_error', False):
            return

        try:
            # Détournement en cas d'aggregation sans annotation ou de non QuerySet
            queryset = super().get_queryset()
            if not isinstance(queryset, QuerySet):
                return queryset

            options = dict(aggregates=None, distinct=None, filters=None, order_by=None)
            self.url_params = url_params = self.request.query_params.dict()

            # Mots-clés réservés dans les URLs
            default_reserved_query_params = ['format'] + ([
                self.paginator.page_query_param,
                self.paginator.page_size_query_param] if self.paginator else [])
            reserved_query_params = default_reserved_query_params + RESERVED_QUERY_PARAMS

            # Critères de recherche dans le cache
            cache_key = url_params.pop('cache', None)
            if cache_key:
                from django.core.cache import cache
                cache_params = cache.get(CACHE_PREFIX + cache_key, {})
                new_url_params = {}
                new_url_params.update(**cache_params)
                new_url_params.update(**url_params)
                self.url_params = url_params = new_url_params
                new_cache_params = {
                    key: value for key, value in url_params.items()
                    if key not in default_reserved_query_params}
                if new_cache_params:
                    from django.utils.timezone import now
                    from datetime import timedelta
                    cache_timeout = int(url_params.pop('timeout', CACHE_TIMEOUT)) or None
                    cache.set(CACHE_PREFIX + cache_key, new_cache_params, timeout=cache_timeout)
                    options['cache_expires'] = now() + timedelta(seconds=cache_timeout)
                cache_url = '{}?cache={}'.format(self.request.build_absolute_uri(self.request.path), cache_key)
                plain_url = cache_url
                for key, value in url_params.items():
                    url_param = '&{}={}'.format(key, value)
                    if key in default_reserved_query_params:
                        cache_url += url_param
                    plain_url += url_param
                options['cache_data'] = new_cache_params
                options['cache_url'] = cache_url
                options['raw_url'] = plain_url

            # Erreurs silencieuses
            silent = str_to_bool(url_params.get('silent', ''))

            # Requête simplifiée et/ou extraction de champs spécifiques
            fields = url_params.get('fields', '')
            if str_to_bool(url_params.get('simple', '')) or fields:
                # Supprime la récupération des relations
                if queryset.query.select_related:
                    queryset = queryset.select_related(None).prefetch_related(None)
                # Champs spécifiques
                try:
                    relateds = set()
                    field_names = set()
                    for field in fields.replace('.', '__').split(','):
                        if not field:
                            continue
                        field_names.add(field)
                        *related, field_name = field.split('__')
                        if related:
                            relateds.add('__'.join(related))
                    if relateds:
                        queryset = queryset.select_related(*relateds)
                    if field_names:
                        queryset = queryset.values(*field_names)
                except Exception as error:
                    if not silent:
                        raise ValidationError(dict(error="fields: {}".format(error)), code='fields')
            else:
                # Récupération des métadonnées
                metadata = str_to_bool(url_params.get('meta', ''))
                if metadata and hasattr(self, 'metadata'):
                    # Permet d'éviter les conflits entre prefetch lookups identiques
                    viewset_lookups = [
                        prefetch if isinstance(prefetch, str) else prefetch.prefetch_through
                        for prefetch in queryset._prefetch_related_lookups]
                    lookups_metadata = []
                    for lookup in self.metadata or []:
                        if isinstance(lookup, str):
                            lookup = Prefetch(lookup)
                        if lookup.prefetch_through not in viewset_lookups:
                            lookups_metadata.append(lookup)
                        lookup.queryset = MetaData.objects.select_valid()
                    if lookups_metadata:
                        queryset = queryset.prefetch_related(*lookups_metadata)

            # Filtres (dans une fonction pour être appelé par les aggregations sans group_by)
            def do_filter(queryset):
                try:
                    filters, excludes = {}, {}
                    for key, value in url_params.items():
                        key = key.replace('.', '__')
                        if value.startswith('[') and value.endswith(']'):
                            value = F(value[1:-1].replace('.', '__'))
                        if key in reserved_query_params:
                            continue
                        if key.startswith('-'):
                            key = key[1:].strip()
                            excludes[key] = url_value(key, value)
                        else:
                            key = key[1:].strip() if key.startswith('+') else key.strip()
                            filters[key] = url_value(key, value)
                    if filters:
                        queryset = queryset.filter(**filters)
                    if excludes:
                        queryset = queryset.exclude(**excludes)
                    # Filtres génériques
                    others = url_params.get('filters', '')
                    if others:
                        queryset = queryset.filter(parse_filters(others))
                    if filters or others:
                        options['filters'] = True
                except Exception as error:
                    if not silent:
                        raise ValidationError(dict(error="filters: {}".format(error)), code='filters')
                    options['filters'] = False
                    if settings.DEBUG:
                        options['filters_error'] = str(error)
                return queryset

            # Aggregations (uniquement sur les listes)
            if self.action == 'list':
                try:
                    aggregations = {}
                    for aggregate, function in AGGREGATES.items():
                        for field in url_params.get(aggregate, '').split(','):
                            if not field:
                                continue
                            distinct = field.startswith(' ') or field.startswith('+')
                            field = field[1:] if distinct else field
                            field = field.strip().replace('.', '__')
                            aggregations[field + '_' + aggregate] = function(field, distinct=distinct)
                    group_by = url_params.get('group_by', '')
                    if group_by:
                        _queryset = queryset.values(*group_by.replace('.', '__').split(','))
                        if aggregations:
                            _queryset = _queryset.annotate(**aggregations)
                        else:
                            _queryset = _queryset.distinct()
                        queryset = _queryset
                        options['aggregates'] = True
                    elif aggregations:
                        queryset = do_filter(queryset)  # Filtres éventuels
                        return queryset.aggregate(**aggregations)
                except ValidationError:
                    raise
                except Exception as error:
                    if not silent:
                        raise ValidationError(dict(error="aggregates: {}".format(error)), code='aggregates')
                    options['aggregates'] = False
                    if settings.DEBUG:
                        options['aggregates_error'] = str(error)

            # Filtres
            queryset = do_filter(queryset)

            # Tris
            try:
                order_by = url_params.get('order_by', '')
                if order_by:
                    orders = []
                    for order in order_by.replace('.', '__').split(','):
                        nulls_first, nulls_last = order.endswith('<'), order.endswith('>')
                        order = order[:-1] if nulls_first or nulls_last else order
                        if order.startswith('-'):
                            orders.append(F(order[1:]).desc(nulls_first=nulls_first, nulls_last=nulls_last))
                        else:
                            order = order[1:] if order.startswith('+') or order.startswith(' ') else order
                            orders.append(F(order).asc(nulls_first=nulls_first, nulls_last=nulls_last))
                    temp_queryset = queryset.order_by(*orders)
                    str(temp_queryset.query)  # Force SQL evaluation to retrieve exception
                    queryset = temp_queryset
                    options['order_by'] = True
            except EmptyResultSet:
                pass
            except Exception as error:
                if not silent:
                    raise ValidationError(dict(error="order_by: {}".format(error)), code='order_by')
                options['order_by'] = False
                if settings.DEBUG:
                    options['order_by_error'] = str(error)

            # Distinct
            distincts = []
            try:
                distinct = url_params.get('distinct', '')
                if distinct:
                    distincts = distinct.replace('.', '__').split(',')
                    if str_to_bool(distinct) is not None:
                        distincts = []
                    queryset = queryset.distinct(*distincts)
                    options['distinct'] = True
            except EmptyResultSet:
                pass
            except Exception as error:
                if not silent:
                    raise ValidationError(dict(error="distinct: {}".format(error)), code='distinct')
                options['distinct'] = False
                if settings.DEBUG:
                    options['distinct_error'] = str(error)

            # Ajout des options de filtres/tris dans la pagination
            if self.paginator and hasattr(self.paginator, 'additional_data'):
                # Force un tri sur la clé primaire en cas de pagination
                if hasattr(queryset, 'ordered') and not queryset.ordered:
                    primary_key = get_pk_field(queryset.model)
                    queryset = queryset.order_by(
                        *(getattr(queryset, '_fields', None) or distincts or [primary_key.name]))
                self.paginator.additional_data = dict(options=options)
            return queryset
        except ValidationError as error:
            self.queryset_error = error
            raise error


class UserViewSet(CommonModelViewSet):
    """
    ViewSet spécifique pour l'utilisateur
    """

    def check_permissions(self, request):
        # Autorise l'utilisateur à modifier ses propres informations ou les informations des utilisateurs en dessous
        current_user = request.user
        if current_user.is_superuser:
            return True
        elif self.action in ['create']:
            # Autorise la création pour tout le monde
            return True
        elif self.action in ['update', 'partial_update']:
            # Autorise la modification de soi-même ou d'un autre utilisateur de rang inférieur
            self.kwargs.update({self.lookup_field: self.kwargs.get(self.lookup_url_kwarg or self.lookup_field, None)})
            user = self.get_object()
            if (current_user == user) or (current_user.is_staff and not (user.is_staff or user.is_superuser)):
                return True
        # Applique le système de permissions dans les autres cas
        return super().check_permissions(request)

    def check_data(self, data):
        # Assure que l'utilisateur ne s'octroie pas des droits qu'il ne peut pas avoir
        user = self.request.user
        if not user or (not user.is_staff and not user.is_superuser):
            data['is_active'] = True
        if not user or not user.is_staff:
            data['is_staff'] = False
        if not user or not user.is_superuser:
            data['is_superuser'] = False
        if 'groups' in data and data.get('groups'):
            if not user:
                data['groups'] = []
            elif not user.is_superuser:
                groups = user.groups.all()
                data['groups'] = list(set(groups) & set(data.get('groups', [])))
        if 'user_permissions' in data and data.get('user_permissions'):
            if not user:
                data['user_permissions'] = []
            elif not user.is_superuser:
                user_permissions = user.user_permissions.all()
                data['user_permissions'] = list(set(user_permissions) & set(data.get('user_permissions', [])))

    def perform_create(self, serializer):
        self.check_data(serializer.validated_data)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self.check_data(serializer.validated_data)
        super().perform_update(serializer)
