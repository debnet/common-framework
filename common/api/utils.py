# coding: utf-8
from functools import wraps

from django.conf import settings
from django.db.models import Q, QuerySet, Count, Sum, Avg, Min, Max
from django.db.models.query import EmptyResultSet
from rest_framework import serializers, viewsets
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.relations import PrimaryKeyRelatedField
from rest_framework.response import Response

from common.utils import get_prefetchs, get_related, parsedate, prefetch_metadatas, str_to_bool


# URLs dans les serializers
HYPERLINKED = settings.REST_FRAMEWORK.get('HYPERLINKED', False)

# Mots clés réservés dans les URLs des APIs
AGGREGATES = {
    'count': Count,
    'sum': Sum,
    'avg': Avg,
    'min': Min,
    'max': Max,
}
RESERVED_QUERY_PARAMS = [
    'format', 'filters', 'fields', 'order_by', 'group_by', 'all',
    'distinct', 'silent', 'simple', 'meta'] + list(AGGREGATES.keys())


def url_value(filter, value):
    """
    Transforme la valeur dans l'URL à partir du filtre
    :param filter: Filtre
    :param value: Valeur
    :return: Valeur
    """
    if filter and filter.endswith('__in'):
        return value.split(',')
    if filter and filter.endswith('__isnull'):
        return str_to_bool(value)
    return value


def parse_filters(filters):
    """
    Parse une chaîne de caractères contenant des conditions au format suivant :
        [and|or|not](champ__lookup:valeur[,champ__lookup:valeur])
    Il est possible de chainer les opérateurs dans un même filtres, exemple :
        or(and(champ_1:valeur_1,champ_2:valeur_2),and(not(champ_3:valeur_3),champ_4:valeur_4))
    :param filters: Filtres sous forme de chaîne de caract_re
    :return: Chaîne de conditions Django
    """
    if isinstance(filters, str):
        import ast
        import re
        filters = re.sub(r'(\w+):([\w\s-]*)', r'{"\1":"\2"}', filters)
        filters = re.sub(r'(and|or|not)\(', r'("\1",', filters)
        filters = ast.literal_eval(filters)
    if isinstance(filters, dict):
        filters = filters,
    operator = None
    elements = []
    for filter in filters:
        if isinstance(filter, tuple):
            elements.append(parse_filters(filter))
        elif isinstance(filter, dict):
            elements.append(Q(**{key: url_value(key, value) for key, value in filter.items()}))
        else:
            operator = filter.lower()
    if operator == 'or':
        q = elements.pop(0)
        for element in elements:
            q |= element
    else:
        q = ~elements.pop(0) if operator == 'not' else elements.pop(0)
        for element in elements:
            q &= element
    return q


def to_model_serializer(model, **metadatas):
    """
    Décorateur permettant d'associer un modèle à une définition de serializer
    :param model: Modèle
    :param metadatas: Metadonnées du serializer
    :return: Serializer
    """
    from common.api.fields import JsonField as ApiJsonField
    from common.fields import JsonField as ModelJsonField

    def wrapper(serializer):
        for field in model._meta.fields:
            if 'fields' in metadatas and field.name not in metadatas.get('fields'):
                continue
            if 'exclude' in metadatas and field.name in metadatas.get('exclude'):
                continue

            # Injection des identifiants de clés étrangères
            if HYPERLINKED and field.related_model:
                serializer._declared_fields[field.name + '_id'] = serializers.ReadOnlyField()
                if 'fields' in metadatas and 'exclude' not in metadatas:
                    metadatas['fields'] = list(metadatas.get('fields', [])) + [field.name + '_id']

            # Injection des valeurs humaines pour les champs ayant une liste de choix
            if field.choices:
                serializer_field_name = '{}_display'.format(field.name)
                source_field_name = 'get_{}'.format(serializer_field_name)
                serializer._declared_fields[serializer_field_name] = serializers.CharField(
                    source=source_field_name, label=field.verbose_name or field.name, read_only=True)
                if 'fields' in metadatas and 'exclude' not in metadatas:
                    metadatas['fields'] = list(metadatas.get('fields', [])) + [serializer_field_name]

            # Injection des données des champs de type JSON
            if isinstance(field, ModelJsonField):
                serializer._declared_fields[field.name] = ApiJsonField(
                    label=field.verbose_name, help_text=field.help_text,
                    required=not field.blank, allow_null=field.null, read_only=not field.editable)

        # Mise à jour des métadonnées du serializer
        if 'fields' not in metadatas and 'exclude' not in metadatas:
            metadatas.update(fields='__all__')
        metadatas.update(model=model)
        serializer.Meta = type('Meta', (), metadatas)
        return serializer
    return wrapper


def to_model_viewset(model, serializer, permissions=None, queryset=None, bases=None, **metadatas):
    """
    Décorateur permettant d'associer un modèle et un serializer à une définition de viewset
    :param model: Modèle
    :param serializer: Serializer
    :param permissions: Permissions spécifiques
    :param queryset: Surcharge du queryset par défaut pour le viewset
    :param bases: Classes dont devra hériter le serializer par défaut
    :param metadatas: Metadonnées du serializer
    :return: ViewSet
    """
    from common.api.permissions import CommonModelPermissions

    def wrapper(viewset):
        viewset.queryset = queryset or model.objects.all()
        viewset.model = model
        viewset.serializer_class = serializer
        viewset.simple_serializer = create_model_serializer(model, bases=bases, **metadatas)
        excludes_many_to_many_from_serializer(viewset.simple_serializer)
        viewset.default_serializer = create_model_serializer(model, bases=bases, hyperlinked=False, **metadatas)
        viewset.permission_classes = permissions or [CommonModelPermissions]
        return viewset
    return wrapper


def excludes_many_to_many_from_serializer(serializer):
    """
    Permet d'exclure les champs de type many-to-many d'un serializer de modèle
    :param serializer: Serializer (classe)
    :return: Rien
    """
    model = getattr(serializer.Meta, 'model', None)
    if model is None:
        return
    fields = getattr(serializer.Meta, 'fields', None)
    if fields == '__all__':
        fields = None
        del serializer.Meta.fields
    if fields is None:
        serializer.Meta.exclude = list(
            set(getattr(serializer.Meta, 'exclude', [])) |
            {field.name for field in model._meta.many_to_many})


def create_model_serializer_and_viewset(
        model, foreign_keys=True, many_to_many=False, one_to_one=True, one_to_many=False,
        fks_in_related=False, null_fks=False,
        serializer_base=None, viewset_base=None, serializer_data=None, viewset_data=None,
        permissions=None, queryset=None, metas=None, exclude_related=None, depth=0, height=1,
        _level=0, _origin=None, _field=None, **options):
    """
    Permet de créer les classes de serializer et de viewset associés à un modèle
    :param model: Modèle
    :param foreign_keys: Récupérer les données des clés étrangères ?
    :param many_to_many: Récupérer les données des many-to-many ?
    :param one_to_one: Récupérer les données des one-to-one (selon profondeur) ?
    :param one_to_many: Récupérer les données des one-to-many (selon profondeur) ?
    :param fks_in_related: Récupérer les données de clés étrangères dans les relations inversées ?
    :param null_fks: Récupérer les données de clés étrangères pouvant être nulles ?
    :param serializer_base: Classes dont devra hériter le serializer (dictionnaire organisé par modèle)
    :param viewset_base: Classes dont devra hériter le viewset (dictionnaire organisé par modèle)
    :param serializer_data: Données complémentaires à ajouter dans le serializer (dictionnaire organisé par modèle)
    :param viewset_data: Données complémentaires à ajouter dans le viewset (dictionnaire organisé par modèle)
    :param permissions: Permissions à vérifier dans le viewset
    :param queryset: Surcharge du queryset dans le viewset
    :param permissions: Permissions à vérifier dans le viewset
    :param metas: Metadonnées des serializers dépendants (dictionnaire organisé par modèle)
    :param exclude_related: Nom des relations inversées à exclure
    :param depth: Profondeur de récupération des modèles dépendants
    :param height: Hauteur maximale de récupération des clés étrangères
    :param _level: Profondeur actuelle (utilisé par la récursivité)
    :param _origin: Modèle d'origine dans la récursivité pour éviter la redondance (utilisé par la récursivité)
    :param _field: Nom du champ dans le modèle d'origine (utilisé par la récursivité)
    :param options: Metadonnées du serializer de base
    :return: Tuple (serializer, viewset)
    """
    object_name = model._meta.object_name

    # Héritages du serializer et viewset
    from common.api.serializers import CommonModelSerializer
    from common.api.viewsets import CommonModelViewSet
    _serializer_base = (serializer_base or {}).get(model, (CommonModelSerializer, ))
    _viewset_base = (viewset_base or {}).get(model, (CommonModelViewSet, ))

    # Ajout du serializer des hyperlinks à la liste si ils sont activés
    _bases = _serializer_base  # Le serializer par défaut des viewsets ne doit pas hériter du serializer des hyperlinks

    # Si aucune surcharge des serializer et/ou du viewset, utilisation des modèles par défaut
    _serializer_base = _serializer_base or (serializers.ModelSerializer, )
    _viewset_base = _viewset_base or (viewsets.ModelViewSet, )

    # Données complémentaires du serializer et viewset
    _serializer_data = (serializer_data or {}).get(model, {}).copy()
    _viewset_data = (viewset_data or {}).get(model, {}).copy()

    # Métadonnées du serializer
    exclude_related = exclude_related if isinstance(exclude_related, dict) else {model: exclude_related or []}
    metadatas = (metas or {}).get(model, {})
    metadatas.update(options)
    metadatas['extra_kwargs'] = metadatas.get('extra_kwargs', {})

    # Vérifie qu'un nom de champ donné est inclu ou exclu
    def field_allowed(field_name):
        return field_name in metadatas.get('fields', []) or (
            field_name not in metadatas.get('exclude', []) and
            field_name not in exclude_related.get(model, []))

    # Création du serializer et du viewset
    serializer = to_model_serializer(model, **metadatas)(
        type(object_name + 'Serializer', _serializer_base, _serializer_data))
    viewset = to_model_viewset(model, serializer, permissions, bases=_bases, **metadatas)(
        type(object_name + 'ViewSet', _viewset_base, _viewset_data))

    # Surcharge du queryset par défaut dans le viewset
    if queryset is not None:
        viewset.queryset = queryset

    # Gestion des clés étrangères
    relateds = []
    prefetchs = []
    prefetchs_metadatas = []  # Prefetch pour récupérer les métadonnées à chaque niveau
    excludes = []

    for field in model._meta.fields:
        if field.primary_key or not field.remote_field or field.related_model is _origin:
            continue
        # Vérification que le champ est bien inclu ou n'est pas exclu
        if not field_allowed(field.name):
            excludes.append(field.name)
            continue
        # Ajout du serializer pour la relation de clé étrangère
        if (foreign_keys and 0 >= _level > -height) or (fks_in_related and _level > 0):
            fk_serializer, fk_viewset = create_model_serializer_and_viewset(
                field.related_model, foreign_keys=foreign_keys, many_to_many=False,
                one_to_one=False, one_to_many=False, fks_in_related=False, null_fks=False,
                serializer_base=serializer_base, viewset_base=viewset_base,
                serializer_data=serializer_data, viewset_data=viewset_data,
                exclude_related=exclude_related, metas=metas, depth=0, height=height,
                _level=_level - 1, _origin=model, _field=field.name)
            serializer._declared_fields[field.name] = fk_serializer(read_only=True)
            relateds.append(field.name)
            # Récupération des relations de plus haut niveau si nécessaire
            field_relateds = get_related(field.related_model, null=null_fks, height=height - 1)
            relateds += ['__'.join([field.name, field_related]) for field_related in field_relateds
                         if field_related not in exclude_related.get(field.related_model, [])]
        elif _level > 0:
            # Les clés étrangères des relations inversées qui pointent sur le modèle d'origine peuvent être nulles
            if field.remote_field and not field.primary_key and field.related_model is _origin:
                serializer.Meta.extra_kwargs[field.name] = dict(required=False, allow_null=True)
        # Prefetch des métadonnées
        prefetchs_metadatas += prefetch_metadatas(field.related_model, field.name)

    # Gestion des many-to-many
    if many_to_many:
        for field in model._meta.many_to_many:
            # Vérification que le champ est bien inclu ou n'est pas exclu
            if not field_allowed(field.name):
                excludes.append(field.name)
                continue
            # Ajout du serializer pour la relation many-to-many
            m2m_serializer, m2m_viewset = create_model_serializer_and_viewset(
                field.related_model, foreign_keys=False, many_to_many=False,
                one_to_one=False, one_to_many=False, fks_in_related=False, null_fks=False,
                serializer_base=serializer_base, viewset_base=viewset_base,
                serializer_data=serializer_data, viewset_data=viewset_data,
                exclude_related=exclude_related, metas=metas, depth=0, height=0,
                _level=0, _origin=model, _field=field.name)
            serializer._declared_fields[field.name] = m2m_serializer(many=True, read_only=True)
            prefetchs.append(field.name)
            # Prefetch des métadonnées
            prefetchs_metadatas += prefetch_metadatas(field.related_model, field.name)
    else:
        # Exclusion du champ many-to-many du serializer
        excludes_many_to_many_from_serializer(viewset.serializer_class)

    # Gestion des one-to-one
    if one_to_one:
        for field in model._meta.related_objects:
            if not field.auto_created or not field.one_to_one:
                continue
            # Vérification que le champ est bien inclu ou n'est pas exclu
            if not field_allowed(field.name):
                excludes.append(field.name)
                continue
            field_name = field.get_accessor_name()
            # Ajout du serializer pour la relation inversée
            child_serializer, child_viewset = create_model_serializer_and_viewset(
                field.related_model, foreign_keys=foreign_keys, many_to_many=many_to_many,
                one_to_one=one_to_one, one_to_many=one_to_many, fks_in_related=fks_in_related, null_fks=null_fks,
                serializer_base=serializer_base, viewset_base=viewset_base,
                serializer_data=serializer_data, viewset_data=viewset_data,
                exclude_related=exclude_related, metas=metas, depth=depth, height=0,
                _level=_level + 1, _origin=model, _field=field_name)
            serializer._declared_fields[field_name] = child_serializer(read_only=True)
            relateds.append(field_name)
            # Récupération des relations de plus haut niveau si nécessaire
            field_relateds = get_related(field.related_model, one_to_one=True, null=null_fks, height=height - 1)
            relateds += ['__'.join([field_name, field_related]) for field_related in field_relateds
                         if field_related not in exclude_related.get(field.related_model, [])]

    # Gestion des one-to-many
    if one_to_many and depth > _level:
        for field in model._meta.related_objects:
            if not field.auto_created or not field.one_to_many:
                continue
            # Vérification que le champ est bien inclu ou n'est pas exclu, et qu'il s'agisse bien d'un champ
            if not field_allowed(field.name):
                excludes.append(field.name)
                continue
            field_name = field.get_accessor_name()
            # Ajout du serializer pour la relation inversée
            child_serializer, child_viewset = create_model_serializer_and_viewset(
                field.related_model, foreign_keys=foreign_keys, many_to_many=many_to_many,
                one_to_one=one_to_one, one_to_many=one_to_many, fks_in_related=fks_in_related, null_fks=null_fks,
                serializer_base=serializer_base, viewset_base=viewset_base,
                serializer_data=serializer_data, viewset_data=viewset_data,
                exclude_related=exclude_related, metas=metas, depth=depth, height=0,
                _level=_level + 1, _origin=model, _field=field_name)
            serializer._declared_fields[field_name] = child_serializer(many=True, read_only=True)

    # Récupération des relations inversées
    arguments = dict(
        depth=depth,
        excludes=excludes,
        foreign_keys=fks_in_related,
        one_to_one=one_to_one,
        one_to_many=one_to_many,
        many_to_many=many_to_many,
        null=null_fks)
    prefetchs += get_prefetchs(model, **arguments)
    prefetchs_metadatas += get_prefetchs(model, metadatas=True, **arguments)

    # Injection des clés étrangères dans le queryset du viewset
    if relateds:
        viewset.queryset = viewset.queryset.select_related(*relateds)
    # Injection des many-to-many et des relations inversées dans le queryset du viewset
    if prefetchs:
        viewset.queryset = viewset.queryset.prefetch_related(*prefetchs)
    viewset.metadatas = prefetchs_metadatas
    return serializer, viewset


def perishable_view(func):
    """
    Décorateur permettant d'enrichir la request utilisée par la fonction des attributs 'date_de_reference' (date) et
    'valide' (bool) ainsi que du valid_filter à appliquer sur le select_valid récupérés dans les query_params
    (None si non présents)
    :param func: Fonction à décorer
    :return: Fonction avec la request enrichie
    """
    @wraps(func)
    def wrapper(item, *args, **kwargs):
        # "request = item.request" dans le cas d'une ViewSet, "item" dans le cas d'une api_view
        request = item.request if hasattr(item, 'request') else item
        valid = None
        valid_date = None
        params = request.data if request.data else request.query_params
        if params:
            valid = str_to_bool(params.get('valid', None))
            valid_date = parsedate(params.get('valid_date', None))
        setattr(request, 'valid', valid)
        setattr(request, 'valid_date', valid_date)
        setattr(request, 'valid_filter', dict(valid=valid, date=valid_date))
        return func(item, *args, **kwargs)
    return wrapper


def api_view_with_serializer(http_method_names=None, input_serializer=None, serializer=None, validation=True):
    """
    Décorateur permettant de créer une APIView à partir d'une fonction suivant la structure d'un serializer
    Elle remplace le décorateur @api_view fourni par défaut dans Django REST Framework
    :param http_method_names: Méthodes HTTP supportées
    :param input_serializer: Serializer des données d'entrée (et de sortie si le serializer associé n'est pas fourni)
    :param serializer: Serializer des données de sortie uniquement
    :param validation: Exécuter la validation des données d'entrée ? (request contiendra alors "validated_data")
    :return: APIView
    """
    serializer = serializer or input_serializer

    def decorator(func):
        @wraps(func)
        def inner_func(request, *args, **kwargs):
            result = func(request, *args, **kwargs)
            if isinstance(result, Response):
                return result
            if not serializer:
                return Response(result)
            many = isinstance(result, (list, QuerySet))
            return Response(serializer(result, many=many, context=dict(request=request)).data)

        view = api_view(http_method_names)(inner_func)
        if input_serializer:
            view_class = view.view_class
            view_class.serializer_class = input_serializer
            # Reprise des méthodes d'accès au serializer pour les métadonnées de l'APIView
            from rest_framework.generics import GenericAPIView
            view_class.get_serializer = GenericAPIView.get_serializer
            view_class.get_serializer_context = GenericAPIView.get_serializer_context
            view_class.get_serializer_class = GenericAPIView.get_serializer_class

            if validation:
                # POST
                post_handler = getattr(view_class, 'post', None)
                if post_handler:
                    def handler(self, request, *args, **kwargs):
                        serializer_instance = input_serializer(data=request.data)
                        serializer_instance.is_valid(raise_exception=True)
                        request.validated_data = serializer_instance.validated_data
                        return post_handler(self, request, *args, **kwargs)
                    view_class.post = handler
                # PUT
                put_handler = getattr(view_class, 'put', None)
                if put_handler:
                    def handler(self, request, *args, **kwargs):
                        partial = kwargs.pop('partial', False)
                        instance = self.get_object()
                        serializer_instance = input_serializer(instance, data=request.data, partial=partial)
                        serializer_instance.is_valid(raise_exception=True)
                        request.validated_data = serializer_instance.validated_data
                        return post_handler(self, request, *args, **kwargs)
                    view_class.put = handler
        return view
    return decorator


def create_model_serializer(model, bases=None, attributes=None, hyperlinked=True, **metas):
    """
    Permet de créer le ModelSerializer pour le modèle fourni en paramètre
    :param model: Modèle à sérialiser
    :param bases: Classes dont devra hériter le serializer
    :param attributes: Attributs spécifiques du serializer
    :param hyperlinked: Active ou non la gestion des URLs pour la clé primaire
    :param metas: Métadonnées du serializer
    :return: serializer
    """
    from common.api.serializers import CommonModelSerializer
    serializer = type('{}AutoSerializer'.format(model._meta.object_name),
                      (bases or (CommonModelSerializer, )), (attributes or {}))
    if not hyperlinked:
        serializer.serializer_related_field = PrimaryKeyRelatedField
    return to_model_serializer(model, **metas)(serializer)


def serializer_factory(excludes):
    """
    Factory fournissant les 2 méthodes de récuperation de classe et d'instance du serializer
    :param excludes: Liste de champs à exclure du ModelSerializer
    :return: Méthode de récupération de la classe du serializer, méthode de récupération de l'instance du serializer
    """
    def get_serializer_class(model):
        return create_model_serializer(model, excludes=excludes.get(model, ()))

    def get_serializer(model, *args, **kwargs):
        return get_serializer_class(model)(*args, **kwargs)

    return get_serializer_class, get_serializer


def paginate(request, queryset, serializer, pagination=None,
             context=None, func=None, func_args=None, func_kwargs=None):
    """
    Ajoute de la pagination aux résultats d'un QuerySet dans un serializer donné
    :param request: Requête HTTP
    :param queryset: QuerySet
    :param serializer: Serializer
    :param pagination: Classe de pagination
    :param context: Contexte du serializer
    :param func: Fonction spécifique à exécuter sur le QuerySet avant la pagination
    :param func_args: Arguments de la fonction
    :param func_kwargs: Arguments mots-clés de la fonction
    :return: Réponse HTTP des résultats avec pagination
    """
    from common.api.pagination import CustomPageNumberPagination
    pagination = pagination or CustomPageNumberPagination

    context = dict(request=request, **(context or {}))
    options = dict(filters=None, order_by=None, distinct=None)
    url_params = request.query_params.dict()

    # Mots-clés réservés dans les URLs
    reserved_query_params = RESERVED_QUERY_PARAMS + [pagination.page_query_param, pagination.page_size_query_param]

    # Erreurs silencieuses
    silent = str_to_bool(url_params.get('silent', None))

    # Filtres
    try:
        filters = {}
        excludes = {}
        for key, value in url_params.items():
            if key not in reserved_query_params:
                if key.startswith('-'):
                    excludes[key[1:]] = url_value(key[1:], value)
                else:
                    filters[key] = url_value(key, value)
        if filters:
            queryset = queryset.filter(**filters)
        if excludes:
            queryset = queryset.exclude(**excludes)
        # Filtres génériques
        others = url_params.get('filters', None)
        if others:
            queryset = queryset.filter(parse_filters(others))
        if filters or excludes or others:
            options['filters'] = True
        # Fonction spécifique
        if func:
            func_args = func_args or []
            func_kwargs = func_kwargs or {}
            queryset = func(queryset, *func_args, **func_kwargs)
    except Exception as error:
        if not silent:
            raise ValidationError("filters: {}".format(error))
        options['filters'] = False
        if settings.DEBUG:
            options['filters_error'] = str(error)

    # Tris
    try:
        order_by = url_params.get('order_by', None)
        if order_by:
            temp_queryset = queryset.order_by(*order_by.split(','))
            str(temp_queryset.query)  # Force SQL evaluation to retrieve exception
            queryset = temp_queryset
            options['order_by'] = True
    except EmptyResultSet:
        pass
    except Exception as error:
        if not silent:
            raise ValidationError("order_by: {}".format(error))
        options['order_by'] = False
        if settings.DEBUG:
            options['order_by_error'] = str(error)

    # Distinct
    try:
        distinct = url_params.get('distinct', None)
        if distinct:
            distincts = distinct.split(',')
            if str_to_bool(distinct) is not None:
                distincts = []
            queryset = queryset.distinct(*distincts)
            options['distinct'] = True
    except EmptyResultSet:
        pass
    except Exception as error:
        if not silent:
            raise ValidationError("distinct: {}".format(error))
        options['distinct'] = False
        if settings.DEBUG:
            options['distinct_error'] = str(error)

    # Uniquement si toutes les données sont demandées
    all_data = str_to_bool(url_params.get('all', False))
    if all_data:
        return Response(serializer(queryset, context=context, many=True).data)

    # Pagination avec ajout des options de filtres/tris dans la pagination
    paginator = pagination()
    if hasattr(paginator, 'additional_data'):
        paginator.additional_data = dict(options=options)
    serializer = serializer(paginator.paginate_queryset(queryset, request), context=context, many=True)
    return paginator.get_paginated_response(serializer.data)


def create_api(*models, default_config=None, router=None, all_serializers=None, all_viewsets=None,
               all_bases_serializers=None, all_bases_viewsets=None, all_data_serializers=None, all_data_viewsets=None,
               all_querysets=None, all_metadatas=None, all_configs=None):
    """
    Crée les APIs REST standard pour les modèles donnés
    :param models: Liste des modèles
    :param default_config: Configuration par défaut des APIs
    :param router: Router existant à mettre à jour
    :param all_serializers: Tous les serializers créés jusqu'à présent
    :param all_viewsets: Tous les viewsets créés jusqu'à présent
    :param all_bases_serializers: Toutes les bases de serializers créées jusqu'à présent
    :param all_bases_viewsets: Toutes les bases de viewsets créées jusqu'à présent
    :param all_metadatas: Toutes les métadonnées créées jusqu'à présent
    :param all_data_serializers: Toutes les données de serializers créées jusqu'à présent
    :param all_data_viewsets: Toutes les données de viewsets créées jusqu'à présent
    :param all_querysets: Toutes les requêtes créées jusqu'à présent
    :param all_configs: Toutes les configs créées jusqu'à présent
    :return: Router, Serializers, Viewsets
    """
    serializers = {}
    viewsets = {}

    # Récupération de la configuration générale
    from common.api.base import (
        SERIALIZERS, VIEWSETS,
        SERIALIZERS_BASE, VIEWSETS_BASE,
        SERIALIZERS_DATA, VIEWSETS_DATA,
        QUERYSETS, METADATAS, CONFIGS, DEFAULT_CONFIG)
    all_serializers = all_serializers or SERIALIZERS
    all_viewsets = all_viewsets or VIEWSETS
    all_bases_serializers = all_bases_serializers or SERIALIZERS_BASE
    all_bases_viewsets = all_bases_viewsets or VIEWSETS_BASE
    all_data_serializers = all_data_serializers or SERIALIZERS_DATA
    all_data_viewsets = all_data_viewsets or VIEWSETS_DATA
    all_querysets = all_querysets or QUERYSETS
    all_metadatas = all_metadatas or METADATAS
    all_configs = all_configs or CONFIGS
    default_config = default_config or DEFAULT_CONFIG

    # Création des serializers et viewsets par défaut
    for model in models:
        serializers[model], viewsets[model] = create_model_serializer_and_viewset(
            model, serializer_base=all_bases_serializers, viewset_base=all_bases_viewsets,
            serializer_data=all_data_serializers, viewset_data=all_data_viewsets,
            queryset=all_querysets.get(model, None), metas=all_metadatas,
            **all_configs.get(model, default_config or {}))

    # Création des routes par défaut
    from rest_framework import routers
    router = router or routers.DefaultRouter()
    for model, viewset in sorted(viewsets.items(), key=lambda key: key[0]._meta.model_name):
        code = model._meta.model_name
        router.register(code, viewset, base_name=code)

    # Mise à jour des serializers et viewsets par défaut
    all_serializers.update(serializers)
    all_viewsets.update(viewsets)
    return router, serializers, viewsets


def disable_relation_fields(*models, all_metadatas=None):
    """
    Remplace la liste de choix par un simple champ de saisie pour toutes les relations des modèles donnés
    (Permet d'améliorer significativement les performances lors de l'affichage du formulaire dans les APIs)
    :param models: Liste des modèles
    :param all_metadatas: Toutes les métadonnées créées jusqu'à présent
    :return: Rien
    """
    from common.api.base import METADATAS
    all_metadatas = all_metadatas or METADATAS

    for model in models:
        metas = {}
        for field in model._meta.get_fields():
            if field.concrete and not field.auto_created and field.related_model:
                metas[field.name] = dict(style={'base_template': 'input.html', 'placeholder': str(field.verbose_name)})
        if metas:
            metadatas = all_metadatas[model] = all_metadatas.get(model, {})
            extra_kwargs = metadatas['extra_kwargs'] = metadatas.get('extra_kwargs', {})
            for key, value in metas.items():
                extra_kwargs[key] = extra_kwargs.get(key, {})
                extra_kwargs[key].update(value)
