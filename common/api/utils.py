# coding: utf-8
from functools import wraps

from django.conf import settings
from django.db.models import F, Q, QuerySet, Count, Sum, Avg, Min, Max
from django.db.models.query import EmptyResultSet
from rest_framework import serializers, viewsets
from rest_framework.decorators import api_view
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.relations import PrimaryKeyRelatedField
from rest_framework.response import Response

from common.api.fields import ChoiceDisplayField, ReadOnlyObjectField
from common.utils import get_field_by_path, get_prefetchs, get_related, parsedate, prefetch_metadata, str_to_bool


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
    'filters', 'fields', 'order_by', 'group_by', 'all', 'display',
    'distinct', 'silent', 'simple', 'meta', 'cache', 'timeout'] + list(AGGREGATES.keys())

# Gestion du cache
CACHE_PREFIX = 'api_'
CACHE_TIMEOUT = 3600  # 1 hour


def url_value(filter, value):
    """
    Transforme la valeur dans l'URL à partir du filtre
    :param filter: Filtre
    :param value: Valeur
    :return: Valeur
    """
    if not isinstance(value, str):
        return value
    if filter and any(filter.endswith(lookup) for lookup in ('__in', '__range', '__any', '__all')):
        return value.split(',')
    if filter and any(filter.endswith(lookup) for lookup in ('__isnull', '__isempty')):
        return str_to_bool(value)
    return value


def parse_filters(filters):
    """
    Parse une chaîne de caractères contenant des conditions au format suivant :
        [and|or|not](champ__lookup:valeur[,champ__lookup:valeur])
    Il est possible de chainer les opérateurs dans un même filtres, exemple :
        or(and(champ_1:valeur_1,champ_2:valeur_2),and(not(champ_3:valeur_3),champ_4:valeur_4))
    :param filters: Filtres sous forme de chaîne de caractères
    :return: Chaîne de conditions Django
    """
    if isinstance(filters, str):
        try:
            import ast
            import re
            filters = filters.replace('\'', '\\\'')
            filters = re.sub(r'(\w+):((\"([^"]*)\")|([^,()]*))', r'{"\1":"\4\5"}', filters)
            filters = re.sub(r'(and|or|not)\(', r'("\1",', filters)
            filters = ast.literal_eval(filters)
        except Exception as exception:
            raise Exception("{filters} : {exception}".format(filters=filters, exception=exception))
    if isinstance(filters, dict):
        filters = filters,
    operator = None
    elements = []
    for filter in filters:
        if isinstance(filter, tuple):
            elements.append(parse_filters(filter))
        elif isinstance(filter, dict):
            fields = {}
            for key, value in filter.items():
                key = key.strip().replace('.', '__')
                if value.startswith('[') and value.endswith(']'):
                    value = F(value[1:-1].replace('.', '__'))
                fields[key] = url_value(key, value)
            elements.append(Q(**fields))
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


def to_model_serializer(model, **metadata):
    """
    Décorateur permettant d'associer un modèle à une définition de serializer
    :param model: Modèle
    :param metadata: Metadonnées du serializer
    :return: Serializer
    """
    from common.api.fields import JsonField as ApiJsonField
    from common.fields import JsonField as ModelJsonField

    def wrapper(serializer):
        for field in model._meta.fields:
            if 'fields' in metadata and field.name not in metadata.get('fields', []):
                continue
            if 'exclude' in metadata and field.name in metadata.get('exclude', []):
                continue

            # Injection des identifiants de clés étrangères
            if HYPERLINKED and field.related_model:
                serializer._declared_fields[field.name + '_id'] = serializers.ReadOnlyField()
                if 'fields' in metadata and 'exclude' not in metadata:
                    metadata['fields'] = list(metadata.get('fields', [])) + [field.name + '_id']

            # Injection des valeurs humaines pour les champs ayant une liste de choix
            if field.choices:
                serializer_field_name = '{}_display'.format(field.name)
                source_field_name = 'get_{}'.format(serializer_field_name)
                serializer._declared_fields[serializer_field_name] = serializers.CharField(
                    source=source_field_name, label=field.verbose_name or field.name, read_only=True)
                if 'fields' in metadata and 'exclude' not in metadata:
                    metadata['fields'] = list(metadata.get('fields', [])) + [serializer_field_name]

            # Injection des données des champs de type JSON
            if isinstance(field, ModelJsonField):
                serializer._declared_fields[field.name] = ApiJsonField(
                    label=field.verbose_name, help_text=field.help_text,
                    required=not field.blank, allow_null=field.null, read_only=not field.editable)

        # Mise à jour des métadonnées du serializer
        if 'fields' not in metadata and 'exclude' not in metadata:
            metadata.update(fields='__all__')
        metadata.update(model=model)
        serializer.Meta = type('Meta', (), metadata)
        return serializer
    return wrapper


def to_model_viewset(model, serializer, permissions=None, queryset=None, bases=None, **metadata):
    """
    Décorateur permettant d'associer un modèle et un serializer à une définition de viewset
    :param model: Modèle
    :param serializer: Serializer
    :param permissions: Permissions spécifiques
    :param queryset: Surcharge du queryset par défaut pour le viewset
    :param bases: Classes dont devra hériter le serializer par défaut
    :param metadata: Metadonnées du serializer
    :return: ViewSet
    """
    from common.api.permissions import CommonModelPermissions

    def wrapper(viewset):
        viewset.queryset = queryset or model.objects.all()
        viewset.model = model
        viewset.serializer_class = serializer
        viewset.simple_serializer = create_model_serializer(model, bases=bases, **metadata)
        excludes_many_to_many_from_serializer(viewset.simple_serializer)
        viewset.default_serializer = create_model_serializer(model, bases=bases, hyperlinked=False, **metadata)
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
    serializer = type('{}GenericSerializer'.format(model._meta.object_name),
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
    metadata = (metas or {}).get(model, {})
    metadata.update(options)
    metadata['extra_kwargs'] = metadata.get('extra_kwargs', {})

    # Vérifie qu'un nom de champ donné est inclu ou exclu
    def field_allowed(field_name):
        return field_name in metadata.get('fields', []) or (
            field_name not in metadata.get('exclude', []) and
            field_name not in exclude_related.get(model, []))

    # Création du serializer et du viewset
    serializer = to_model_serializer(model, **metadata)(
        type(object_name + 'Serializer', _serializer_base, _serializer_data))
    viewset = to_model_viewset(model, serializer, permissions, bases=_bases, **metadata)(
        type(object_name + 'ViewSet', _viewset_base, _viewset_data))

    # Surcharge du queryset par défaut dans le viewset
    if queryset is not None:
        viewset.queryset = queryset

    # Gestion des clés étrangères
    relateds = []
    prefetchs = []
    prefetchs_metadata = []  # Prefetch pour récupérer les métadonnées à chaque niveau
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
            field_relateds = get_related(
                field.related_model, null=null_fks, height=height - 1, _models=[model])
            relateds += ['__'.join([field.name, field_related]) for field_related in field_relateds
                         if field_related not in exclude_related.get(field.related_model, [])]
        elif _level > 0:
            # Les clés étrangères des relations inversées qui pointent sur le modèle d'origine peuvent être nulles
            if field.remote_field and not field.primary_key and field.related_model is _origin:
                serializer.Meta.extra_kwargs[field.name] = dict(required=False, allow_null=True)
        # Prefetch des métadonnées
        prefetchs_metadata += prefetch_metadata(field.related_model, field.name)

    # Gestion des many-to-many
    if many_to_many and depth > _level:
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
            prefetchs_metadata += prefetch_metadata(field.related_model, field.name)
    else:
        # Exclusion du champ many-to-many du serializer
        excludes_many_to_many_from_serializer(viewset.serializer_class)

    # Gestion des one-to-one
    if one_to_one and depth > _level:
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
            field_relateds = get_related(
                field.related_model, one_to_one=True, null=null_fks, height=height - 1, _models=[model])
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
    prefetchs_metadata += get_prefetchs(model, metadata=True, **arguments)

    # Injection des clés étrangères dans le queryset du viewset
    if relateds:
        viewset.queryset = viewset.queryset.select_related(*relateds)
    # Injection des many-to-many et des relations inversées dans le queryset du viewset
    if prefetchs:
        viewset.queryset = viewset.queryset.prefetch_related(*prefetchs)
    viewset.metadata = prefetchs_metadata
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
    :param input_serializer: Serializer des données d'entrée
    :param serializer: Serializer des données de sortie
    :param validation: Exécuter la validation des données d'entrée ? (request contiendra alors "validated_data")
    :return: APIView
    """
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


def auto_view(http_method_names=None, input_serializer=None, serializer=None, validation=True, many=False,
              custom_func=None, query_func=None, func_args=None, func_kwargs=None):
    """
    Décorateur permettant de générer le corps d'une APIView à partir d'un QuerySet
    :param http_method_names: Méthodes HTTP supportées
    :param input_serializer: Serializer des données d'entrée
    :param serializer: Serializer des données de sortie
    :param validation: Exécuter la validation des données d'entrée ? (request contiendra alors "validated_data")
    :param many: Affichage de plusieurs éléments ou élément individuel (404 si élément non trouvé) ?
    :param custom_func: Fonction facultive de transformation du QuerySet
        fonction(request: Request, queryset: QuerySet) -> Union[QuerySet, Tuple[QuerySet, dict]]
    :param query_func: Fonction de récupération des éléments ('first' ou 'all' par défaut selon le paramètre 'many')
    :param func_args: Arguments optionnels de la fonction de récupération (pour 'latest' ou 'earliest' par exemple)
    :param func_kwargs: Arguments optionnels nommés de la fonction de récupération
    :return: API View
    """
    query_func = (query_func or QuerySet.all) if many else (query_func or QuerySet.first)
    func_args = func_args or []
    func_kwargs = func_kwargs or {}

    def wrapper(func):
        @wraps(func)
        def wrapped(request, **kwargs):
            context = {}
            queryset = func(request, **kwargs)
            if isinstance(queryset, tuple):
                # (Facultatif) La fonction peut retourner un contexte en plus de son QuerySet
                queryset, context = queryset
            if custom_func:
                queryset = custom_func(request, queryset)
            if many and serializer:
                return api_paginate(
                    request, queryset, serializer, context=context,
                    query_func=query_func, func_args=func_args, func_kwargs=func_kwargs)
            queryset = query_func(queryset, *func_args, **func_kwargs)
            if not isinstance(queryset, QuerySet) and not queryset:
                raise NotFound()
            if not serializer:
                return Response(queryset)
            return Response(serializer(queryset, context=dict(request=request, **context)).data)

        return api_view_with_serializer(
            http_method_names=http_method_names,
            input_serializer=input_serializer,
            serializer=serializer,
            validation=validation)(wrapped)
    return wrapper


def api_paginate(request, queryset, serializer, pagination=None, enable_options=True,
                 context=None, query_func=None, func_args=None, func_kwargs=None):
    """
    Ajoute de la pagination aux résultats d'un QuerySet dans un serializer donné
    :param request: Requête HTTP
    :param queryset: QuerySet
    :param serializer: Serializer
    :param pagination: Classe de pagination
    :param enable_options: Active toutes les options de filtre/tri/aggregation/distinct
    :param context: Contexte du serializer
    :param query_func: Fonction spécifique à exécuter sur le QuerySet avant la pagination
    :param func_args: Arguments de la fonction
    :param func_kwargs: Arguments mots-clés de la fonction
    :return: Réponse HTTP des résultats avec pagination
    """
    from common.api.pagination import CustomPageNumberPagination
    pagination = pagination or CustomPageNumberPagination

    # Mots-clés réservés dans les URLs
    default_reserved_query_params = ['format', pagination.page_query_param, pagination.page_size_query_param]
    reserved_query_params = default_reserved_query_params + RESERVED_QUERY_PARAMS

    url_params = request.query_params.dict()
    context = dict(request=request, **(context or {}))
    options = dict(aggregates=None, distinct=None, filters=None, order_by=None)

    # Activation des options
    if enable_options:

        # Fonction de récupération des données depuis les paramètres
        def get(name):
            return url_params.get(name, '').replace('.', '__').replace(' ', '')

        # Critères de recherche dans le cache
        cache_key = url_params.pop('cache', None)
        if cache_key:
            from django.core.cache import cache
            cache_params = cache.get(CACHE_PREFIX + cache_key, {})
            new_url_params = {}
            new_url_params.update(**cache_params)
            new_url_params.update(**url_params)
            url_params = new_url_params
            new_cache_params = {key: value for key, value in url_params.items() if key not in default_reserved_query_params}
            if new_cache_params:
                from django.utils.timezone import now
                from datetime import timedelta
                cache_timeout = int(url_params.pop('timeout', CACHE_TIMEOUT)) or None
                cache.set(CACHE_PREFIX + cache_key, new_cache_params, timeout=cache_timeout)
                options['cache_expires'] = now() + timedelta(seconds=cache_timeout)
            cache_url = '{}?cache={}'.format(request.build_absolute_uri(request.path), cache_key)
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
        silent = str_to_bool(get('silent'))

        # Extraction de champs spécifiques
        fields = get('fields')
        if fields:
            # Supprime la récupération des relations
            queryset = queryset.select_related(None).prefetch_related(None)
            # Champs spécifiques
            try:
                relateds = set()
                field_names = set()
                for field in fields.split(','):
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
                    raise ValidationError("fields: {}".format(error))

        # Filtres (dans une fonction pour être appelé par les aggregations sans group_by)
        def do_filter(queryset):
            try:
                filters = {}
                excludes = {}
                for key, value in url_params.items():
                    key = key.replace('.', '__')
                    if value.startswith('(') and value.endswith(')'):
                        value = F(value[1:-1])
                    if key in reserved_query_params:
                        continue
                    if key.startswith('-'):
                        key = key[1:]
                        excludes[key] = url_value(key, value)
                    else:
                        key = key.strip()
                        filters[key] = url_value(key, value)
                if filters:
                    queryset = queryset.filter(**filters)
                if excludes:
                    queryset = queryset.exclude(**excludes)
                # Filtres génériques
                others = url_params.get('filters', '')
                if others:
                    queryset = queryset.filter(parse_filters(others))
                if filters or excludes or others:
                    options['filters'] = True
            except Exception as error:
                if not silent:
                    raise ValidationError("filters: {}".format(error))
                options['filters'] = False
                if settings.DEBUG:
                    options['filters_error'] = str(error)
            return queryset

        # Aggregations
        try:
            aggregations = {}
            for aggregate, function in AGGREGATES.items():
                for field in url_params.get(aggregate, '').split(','):
                    if not field:
                        continue
                    distinct = field.startswith(' ')
                    field = field.strip().replace('.', '__')
                    aggregations[field + '_' + aggregate] = function(field, distinct=distinct)
            group_by = get('group_by')
            if group_by:
                _queryset = queryset.values(*group_by.split(','))
                if aggregations:
                    _queryset = _queryset.annotate(**aggregations)
                else:
                    _queryset = _queryset.distinct()
                queryset = _queryset
                options['aggregates'] = True
            elif aggregations:
                queryset = do_filter(queryset)  # Filtres éventuels
                return queryset.aggregate(**aggregations)
        except Exception as error:
            if not silent:
                raise ValidationError("aggregates: {}".format(error))
            options['aggregates'] = False
            if settings.DEBUG:
                options['aggregates_error'] = str(error)

        # Filtres
        queryset = do_filter(queryset)

        # Tris
        try:
            order_by = get('order_by')
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
        distincts = []
        try:
            distinct = get('distinct')
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

        # Fonction utilitaire d'ajout de champ au serializer
        def add_field_to_serializer(fields, field_name):
            field_name = field_name.strip()
            source = field_name.strip().replace('.', '__')
            # Champ spécifique en cas d'énumération
            choices = getattr(get_field_by_path(queryset.model, field_name), 'flatchoices', None)
            if choices and str_to_bool(get('display')):
                fields[field_name + '_display'] = ChoiceDisplayField(choices=choices, source=source)
            # Champ spécifique pour l'affichage de la valeur
            fields[field_name] = ReadOnlyObjectField(source=source if '.' in field_name else None)

        # Création de serializer à la volée en cas d'aggregation ou de restriction de champs
        aggregations = {}
        for aggregate in AGGREGATES.keys():
            for field in url_params.get(aggregate, '').split(','):
                if not field:
                    continue
                field_name = field.strip() + '_' + aggregate
                source = field_name.replace('.', '__') if '.' in field else None
                aggregations[field_name] = serializers.ReadOnlyField(source=source)
        # Regroupements & aggregations
        if 'group_by' in url_params or aggregations:
            fields = {}
            for field in url_params.get('group_by', '').split(','):
                add_field_to_serializer(fields, field)
            fields.update(aggregations)
            # Un serializer avec les données groupées est créé à la volée
            serializer = type(serializer.__name__, (serializers.Serializer, ), fields)
        # Restriction de champs
        elif 'fields' in url_params:
            fields = {}
            for field in url_params.get('fields', '').split(','):
                add_field_to_serializer(fields, field)
            # Un serializer avec restriction des champs est créé à la volée
            serializer = type(serializer.__name__, (serializers.Serializer, ), fields)

    # Fonction spécifique
    if query_func:
        func_args = func_args or []
        func_kwargs = func_kwargs or {}
        queryset = query_func(queryset, *func_args, **func_kwargs)

    # Uniquement si toutes les données sont demandées
    all_data = str_to_bool(get('all'))
    if all_data:
        return Response(serializer(queryset, context=context, many=True).data)

    # Pagination avec ajout des options de filtres/tris dans la pagination
    paginator = pagination()
    if enable_options and hasattr(paginator, 'additional_data'):
        paginator.additional_data = dict(options=options)
    # Force un tri sur la clé primaire en cas de pagination
    if hasattr(queryset, 'ordered') and not queryset.ordered:
        queryset = queryset.order_by(*(
            getattr(queryset, '_fields', None) or (enable_options and distincts) or [queryset.model._meta.pk.name]))
    serializer = serializer(paginator.paginate_queryset(queryset, request), context=context, many=True)
    return paginator.get_paginated_response(serializer.data)


def create_api(*models, default_config=None, router=None, all_serializers=None, all_viewsets=None,
               all_bases_serializers=None, all_bases_viewsets=None, all_data_serializers=None, all_data_viewsets=None,
               all_querysets=None, all_metadata=None, all_configs=None):
    """
    Crée les APIs REST standard pour les modèles donnés
    :param models: Liste des modèles
    :param default_config: Configuration par défaut des APIs
    :param router: Router existant à mettre à jour
    :param all_serializers: Tous les serializers créés jusqu'à présent
    :param all_viewsets: Tous les viewsets créés jusqu'à présent
    :param all_bases_serializers: Toutes les bases de serializers créées jusqu'à présent
    :param all_bases_viewsets: Toutes les bases de viewsets créées jusqu'à présent
    :param all_metadata: Toutes les métadonnées créées jusqu'à présent
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
        QUERYSETS, METADATA, CONFIGS, DEFAULT_CONFIG)
    all_serializers = all_serializers or SERIALIZERS
    all_viewsets = all_viewsets or VIEWSETS
    all_bases_serializers = all_bases_serializers or SERIALIZERS_BASE
    all_bases_viewsets = all_bases_viewsets or VIEWSETS_BASE
    all_data_serializers = all_data_serializers or SERIALIZERS_DATA
    all_data_viewsets = all_data_viewsets or VIEWSETS_DATA
    all_querysets = all_querysets or QUERYSETS
    all_metadata = all_metadata or METADATA
    all_configs = all_configs or CONFIGS
    default_config = default_config or DEFAULT_CONFIG

    # Création des serializers et viewsets par défaut
    for model in models:
        if not model:
            continue
        serializers[model], viewsets[model] = create_model_serializer_and_viewset(
            model, serializer_base=all_bases_serializers, viewset_base=all_bases_viewsets,
            serializer_data=all_data_serializers, viewset_data=all_data_viewsets,
            queryset=all_querysets.get(model, None), metas=all_metadata,
            **all_configs.get(model, default_config or {}))

    # Création des routes par défaut
    from rest_framework import routers
    router = router or routers.DefaultRouter()
    for model, viewset in sorted(viewsets.items(), key=lambda key: key[0]._meta.model_name):
        code = model._meta.model_name
        router.register(code, viewset, basename=code)

    # Mise à jour des serializers et viewsets par défaut
    all_serializers.update(serializers)
    all_viewsets.update(viewsets)
    return router, serializers, viewsets


def disable_relation_fields(*models, all_metadata=None):
    """
    Remplace la liste de choix par un simple champ de saisie pour toutes les relations des modèles donnés
    (Permet d'améliorer significativement les performances lors de l'affichage du formulaire dans les APIs)
    :param models: Liste des modèles
    :param all_metadata: Toutes les métadonnées créées jusqu'à présent
    :return: Rien
    """
    from common.api.base import METADATA
    all_metadata = all_metadata or METADATA

    for model in models:
        if not model:
            continue
        metas = {}
        for field in model._meta.get_fields():
            if field.concrete and not field.auto_created and field.related_model:
                metas[field.name] = dict(style={'base_template': 'input.html', 'placeholder': str(field.verbose_name)})
        if metas:
            metadata = all_metadata[model] = all_metadata.get(model, {})
            extra_kwargs = metadata['extra_kwargs'] = metadata.get('extra_kwargs', {})
            for key, value in metas.items():
                extra_kwargs[key] = extra_kwargs.get(key, {})
                extra_kwargs[key].update(value)
