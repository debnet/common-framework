# coding: utf-8
import ast
import re
import zoneinfo
from datetime import timedelta
from functools import partial, wraps
from json import JSONDecodeError

from django import VERSION as django_version
from django.contrib.postgres import aggregates as pg_aggregates
from django.contrib.postgres import search as pg_search
from django.core.exceptions import EmptyResultSet
from django.db import connection, models
from django.db.models import F, Q, QuerySet, Value, aggregates, functions
from django.utils.timezone import now
from rest_framework import serializers, viewsets
from rest_framework.decorators import api_view
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response

from common.api.fields import ChoiceDisplayField, ReadOnlyObjectField
from common.settings import settings
from common.utils import (
    get_field_by_path,
    get_model_permissions,
    get_models_from_queryset,
    get_pk_field,
    get_prefetchs,
    get_related,
    json_decode,
    parsedate,
    prefetch_metadata,
    str_to_bool,
    web_to_raw_tsquery,
)

is_postgresql = connection.vendor == "postgresql"

# URLs dans les serializers
HYPERLINKED = settings.REST_FRAMEWORK.get("HYPERLINKED", False)

# Mots clés réservés dans les URLs des APIs
AGGREGATES = {
    "count": aggregates.Count,
    "sum": aggregates.Sum,
    "avg": aggregates.Avg,
    "min": aggregates.Min,
    "max": aggregates.Max,
    "stddev": aggregates.StdDev,
    "variance": aggregates.Variance,
}
if is_postgresql:
    AGGREGATES.update(
        {
            "arrayagg": pg_aggregates.ArrayAgg,
            "bitand": pg_aggregates.BitAnd,
            "bitor": pg_aggregates.BitOr,
            "bitxor": pg_aggregates.BitXor,
            "booland": pg_aggregates.BoolAnd,
            "boolor": pg_aggregates.BoolOr,
            "jsonbagg": pg_aggregates.JSONBAgg,
            "stringagg": pg_aggregates.StringAgg,
            "corr": pg_aggregates.Corr,
            "covarpop": pg_aggregates.CovarPop,
            "regravgx": pg_aggregates.RegrAvgX,
            "regravgy": pg_aggregates.RegrAvgY,
            "regrcount": pg_aggregates.RegrCount,
            "regrintercept": pg_aggregates.RegrIntercept,
            "regrr2": pg_aggregates.RegrR2,
            "regrslope": pg_aggregates.RegrSlope,
            "regrsxx": pg_aggregates.RegrSXX,
            "regrsxy": pg_aggregates.RegrSXY,
            "regrsyy": pg_aggregates.RegrSYY,
        }
    )
CASTS = {
    "bool": models.BooleanField(),
    "date": models.DateField(),
    "datetime": models.DateTimeField(),
    "decimal": models.DecimalField(),
    "float": models.FloatField(),
    "int": models.IntegerField(),
    "str": models.CharField(),
    "text": models.TextField(),
    "time": models.TimeField(),
}
FUNCTIONS = {
    "f": F,
    "cast": functions.Cast,
    "coalesce": functions.Coalesce,
    "collate": functions.Collate,
    "greatest": functions.Greatest,
    "least": functions.Least,
    "nullif": functions.NullIf,
    "extract": functions.Extract,
    "extract_year": functions.ExtractYear,
    "extract_iso_year": functions.ExtractIsoYear,
    "extract_month": functions.ExtractMonth,
    "extract_day": functions.ExtractDay,
    "extract_week_day": functions.ExtractWeekDay,
    "extract_iso_week_day": functions.ExtractIsoWeekDay,
    "extract_week": functions.ExtractWeek,
    "extract_quarter": functions.ExtractQuarter,
    "extract_hour": functions.ExtractHour,
    "extract_minute": functions.ExtractMinute,
    "extract_second": functions.ExtractSecond,
    "now": functions.Now,
    "trunc": functions.Trunc,
    "trunc_date": functions.TruncDate,
    "trunc_year": functions.TruncYear,
    "trunc_month": functions.TruncMonth,
    "trunc_day": functions.TruncDay,
    "trunc_week": functions.TruncWeek,
    "trunc_quarter": functions.TruncQuarter,
    "trunc_time": functions.TruncTime,
    "trunc_hour": functions.TruncHour,
    "trunc_minute": functions.TruncMinute,
    "trunc_second": functions.TruncSecond,
    "abs": functions.Abs,
    "acos": functions.ACos,
    "asin": functions.ASin,
    "atan": functions.ATan,
    "atan2": functions.ATan2,
    "ceil": functions.Ceil,
    "cos": functions.Cos,
    "cot": functions.Cot,
    "degrees": functions.Degrees,
    "exp": functions.Exp,
    "floor": functions.Floor,
    "ln": functions.Ln,
    "log": functions.Log,
    "mod": functions.Mod,
    "pi": functions.Pi,
    "power": functions.Power,
    "radians": functions.Radians,
    "random": functions.Random,
    "round": functions.Round,
    "sign": functions.Sign,
    "sin": functions.Sin,
    "sqrt": functions.Sqrt,
    "tan": functions.Tan,
    "chr": functions.Chr,
    "concat": functions.Concat,
    "left": functions.Left,
    "length": functions.Length,
    "lower": functions.Lower,
    "lpad": functions.LPad,
    "ltrim": functions.LTrim,
    "md5": functions.MD5,
    "ord": functions.Ord,
    "repeat": functions.Repeat,
    "replace": functions.Replace,
    "reverse": functions.Reverse,
    "right": functions.Right,
    "rpad": functions.RPad,
    "rtrim": functions.RTrim,
    "sha1": functions.SHA1,
    "sha224": functions.SHA224,
    "sha256": functions.SHA256,
    "sha384": functions.SHA384,
    "sha512": functions.SHA512,
    "strindex": functions.StrIndex,
    "substr": functions.Substr,
    "trim": functions.Trim,
    "upper": functions.Upper,
}
if is_postgresql:
    FUNCTIONS.update(
        {
            # Search
            "search_vector": pg_search.SearchVector,
            "search_rank": pg_search.SearchRank,
            "search_headline": pg_search.SearchHeadline,
            "trigram_similarity": pg_search.TrigramSimilarity,
            "trigram_word_similarity": pg_search.TrigramWordSimilarity,
            "trigram_distance": pg_search.TrigramDistance,
            "trigram_word_distance": pg_search.TrigramWordDistance,
        }
    )
    if django_version >= (4, 2):
        FUNCTIONS.update(
            {
                "trigram_strict_word_similarity": pg_search.TrigramStrictWordSimilarity,
                "trigram_strict_word_distance": pg_search.TrigramStrictWordDistance,
            }
        )
RESERVED_QUERY_PARAMS = (
    [
        "filters",
        "fields",
        "order_by",
        "group_by",
        "all",
        "display",
        "distinct",
        "silent",
        "simple",
        "meta",
        "cache",
        "save_as",
        "timeout",
    ]
    + list(AGGREGATES.keys())
    + list(FUNCTIONS.keys())
)
MULTI_LOOKUPS = ["__in", "__range", "__hasany", "__hasall", "__has_keys", "__has_any_keys", "__overlap"]
BOOL_LOOKUPS = ["__isnull", "__isempty"]
JSON_LOOKUPS = ["__contains", "__contained_by", "__hasdict", "__indict"]
SEARCH_FORMAT = re.compile(r"(?P<search_type>\w+)?\((?P<query>.*)\)(?P<config>\[?[\w.]+]?)?")


def convert_arg(function, arg_index, arg_raw):
    """
    Transforme un argument parsé de l'API en fonction de l'annotation/aggregate utilisée
    :param function: Nom de l'annotation/aggregate
    :param arg_index: Position de l'argument
    :param arg_raw: Valeur brute de l'argument
    :return: Valeur transformée
    """
    converts = CONVERTS.get(function, {})
    if converts is None:
        return None
    arg_name, arg_value, *_ = arg_raw.split(":", maxsplit=1) + [""]
    if not arg_value or arg_name not in converts:
        arg_name, arg_value = None, arg_raw
    try:
        arg_value = ast.literal_eval(arg_value)
    except (SyntaxError, ValueError):
        pass
    if value := parse_arg_value(arg_value, key=function):
        arg_value = value
    else:
        arg_value = converts.get(arg_name or arg_index, Value)(arg_value)
    if arg_name:
        return {arg_name: arg_value}
    return arg_value


def parse_arg_value(value, keep=False, key=None):
    """
    Parse une valeur contenant une référence de champ ou une fonction de recherche
    :param value: Valeur d'argument
    :param keep: Garde la valeur d'entrée par défaut
    :param key: Nom du champ ou de la fonction appelante
    :return: Valeur ou champ de base de données
    """
    if isinstance(value, str):
        if value.startswith("[") and value.endswith("]"):
            value = value[1:-1].replace(".", "__")
            value, cast, *_ = value.split(":") + [""]
            value = F(value)
            if output_field := CASTS.get(cast.lower()):
                value = functions.Cast(value, output_field=output_field)
            return value
        if is_postgresql and key != "filters" and (search := SEARCH_FORMAT.match(value)):
            params = search.groupdict()
            query = params.get("query")
            config = parse_arg_value(params.get("config"), key=key) or params.get("config")
            search_type = (params.pop("search_type") or "custom").lower()
            search_type = {
                "c": "custom",
                "p": "phrase",
                "q": "plain",
                "r": "raw",
                "v": "vector",
                "w": "websearch",
            }.get(search_type, search_type)
            if search_type == "vector":
                return pg_search.SearchVector(*query.split(), config=config)
            elif search_type == "custom":
                query, search_type = web_to_raw_tsquery(query), "raw"
            return pg_search.SearchQuery(query, config=config, search_type=search_type)
    return value if keep else None


def url_value(filter, value):
    """
    Transforme la valeur dans l'URL à partir du filtre
    :param filter: Filtre
    :param value: Valeur
    :return: Valeur
    """
    if not isinstance(value, str):
        return value
    try:
        value = ast.literal_eval(value)
        evaluated = True
    except (SyntaxError, ValueError):
        evaluated = False
    if not filter:
        return value
    if any(filter.endswith(lookup) for lookup in MULTI_LOOKUPS):
        if evaluated:
            if not isinstance(value, (list, set, tuple)):
                return (value,)
        else:
            return value.split(",")
    if any(filter.endswith(lookup) for lookup in BOOL_LOOKUPS):
        return str_to_bool(value)
    if any(filter.endswith(lookup) for lookup in JSON_LOOKUPS):
        if not isinstance(value, str):
            return value
        try:
            return json_decode(value)
        except (JSONDecodeError, TypeError, ValueError):
            if ":" in value:
                data = {}
                for subvalue in value.split(","):
                    key, val = subvalue.split(":")
                    data[key] = val
                return data
            elif "," in value:
                return value.split(",")
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
            filters = filters.replace("'", "\\'").replace('"', '\\"')
            filters = re.sub(r"([\w.]+):([^,/()]*)", r'{"\1":"\2"}', filters)
            filters = re.sub(r"(\w+)\(", r'("\1",', filters)
            filters = ast.literal_eval(filters)
        except Exception as exception:
            raise Exception("{filters}: {exception}".format(filters=filters, exception=exception))
    if isinstance(filters, dict):
        filters = (filters,)
    operator = None
    elements = []
    for filter in filters:
        if isinstance(filter, tuple):
            elements.append(parse_filters(filter))
        elif isinstance(filter, dict):
            fields = {}
            for key, value in filter.items():
                key = key.replace(".", "__")
                value = parse_arg_value(value, key=key) or value
                fields[key] = url_value(key, value)
            elements.append(Q(**fields))
        elif isinstance(filter, str):
            operator = filter.lower()
    if operator == "or":
        q = elements.pop(0)
        for element in elements:
            q |= element
    else:
        q = ~elements.pop(0) if operator == "not" else elements.pop(0)
        for element in elements:
            q &= element
    return q


def to_model_serializer(model, read_only=False, display=True, related_ids=True, **metadata):
    """
    Décorateur permettant d'associer un modèle à une définition de serializer
    :param model: Modèle
    :param read_only: Configure tous les champs en read-only
    :param display: Ajoute la représentation humaine des champs ayant une liste de choix
    :param related_ids: Ajoute les identifiants de clé étrangère en plus des liens
    :param metadata: Metadonnées du serializer
    :return: Serializer
    """
    from common.api.fields import JsonField as ApiJsonField
    from common.fields import JsonField as ModelJsonField

    def wrapper(serializer):
        read_only_fields = set(metadata.pop("read_only_fields", []))
        for field in model._meta.fields:
            if "fields" in metadata and field.name not in metadata.get("fields", []):
                continue
            if "exclude" in metadata and field.name in metadata.get("exclude", []):
                continue
            if read_only:
                read_only_fields.add(field.name)

            # Injection des identifiants de clés étrangères
            if HYPERLINKED and related_ids and field.related_model:
                serializer._declared_fields[field.name + "_id"] = serializers.ReadOnlyField()
                if "fields" in metadata and "exclude" not in metadata:
                    metadata["fields"] = list(metadata.get("fields", [])) + [field.name + "_id"]

            # Injection des valeurs humaines pour les champs ayant une liste de choix
            if display and field.choices:
                serializer_field_name = "{}_display".format(field.name)
                source_field_name = "get_{}".format(serializer_field_name)
                serializer._declared_fields[serializer_field_name] = serializers.CharField(
                    source=source_field_name,
                    label=field.verbose_name or field.name,
                    read_only=True,
                )
                if "fields" in metadata and "exclude" not in metadata:
                    metadata["fields"] = list(metadata.get("fields", [])) + [serializer_field_name]

            # Injection des données des champs de type JSON
            if isinstance(field, ModelJsonField):
                serializer._declared_fields[field.name] = ApiJsonField(
                    label=field.verbose_name,
                    help_text=field.help_text,
                    required=not field.blank,
                    allow_null=field.null,
                    read_only=read_only or not field.editable,
                )

        # Mise à jour des métadonnées du serializer
        if "fields" not in metadata and "exclude" not in metadata:
            metadata.update(fields="__all__")
        if read_only_fields:
            metadata.update(read_only_fields=tuple(read_only_fields))
        metadata.update(model=model)
        metadata.update(ref_name=model._meta.label)
        serializer.Meta = type("Meta", (), metadata)
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
    model = getattr(serializer.Meta, "model", None)
    if model is None:
        return
    fields = getattr(serializer.Meta, "fields", None)
    if fields == "__all__":
        fields = None
        del serializer.Meta.fields
    if fields is None:
        serializer.Meta.exclude = list(
            set(getattr(serializer.Meta, "exclude", [])) | {field.name for field in model._meta.many_to_many}
        )


def create_model_serializer(model, bases=None, attributes=None, hyperlinked=HYPERLINKED, **metas):
    """
    Permet de créer le ModelSerializer pour le modèle fourni en paramètre
    :param model: Modèle à sérialiser
    :param bases: Classes dont devra hériter le serializer
    :param attributes: Attributs spécifiques du serializer
    :param hyperlinked: Active ou non la gestion des URLs pour la clé primaire
    :param metas: Métadonnées du serializer
    :return: serializer
    """
    from common.api.serializers import BaseCommonModelSerializer, CommonHyperlinkedModelSerializer

    serializer = type(
        "{}GenericSerializer".format(model._meta.object_name),
        (bases or (CommonHyperlinkedModelSerializer,) if hyperlinked else (BaseCommonModelSerializer,)),
        (attributes or {}),
    )
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
    model,
    foreign_keys=False,
    many_to_many=False,
    one_to_one=False,
    one_to_many=False,
    fks_in_related=False,
    null_fks=False,
    serializer_base=None,
    viewset_base=None,
    serializer_data=None,
    viewset_data=None,
    permissions=None,
    queryset=None,
    metas=None,
    exclude_related=None,
    hyperlinked=HYPERLINKED,
    depth=1,
    height=1,
    _level=0,
    _origin=None,
    _field=None,
    **options,
):
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
    :param hyperlinked: Génère des serializers avec des URLs
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
    from common.api.serializers import BaseCommonModelSerializer, CommonHyperlinkedModelSerializer
    from common.api.viewsets import CommonModelViewSet

    _serializer_base = (serializer_base or {}).get(
        model, (CommonHyperlinkedModelSerializer,) if hyperlinked else (BaseCommonModelSerializer,)
    )
    _viewset_base = (viewset_base or {}).get(model, (CommonModelViewSet,))

    # Ajout du serializer des hyperlinks à la liste si ils sont activés
    _bases = _serializer_base  # Le serializer par défaut des viewsets ne doit pas hériter du serializer des hyperlinks

    # Si aucune surcharge des serializer et/ou du viewset, utilisation des modèles par défaut
    _serializer_base = _serializer_base or (serializers.ModelSerializer,)
    _viewset_base = _viewset_base or (viewsets.ModelViewSet,)

    # Données complémentaires du serializer et viewset
    _serializer_data = (serializer_data or {}).get(model, {}).copy()
    _viewset_data = (viewset_data or {}).get(model, {}).copy()

    # Métadonnées du serializer
    exclude_related = exclude_related if isinstance(exclude_related, dict) else {model: exclude_related or []}
    metadata = (metas or {}).get(model, {})
    metadata.update(options)
    metadata["extra_kwargs"] = metadata.get("extra_kwargs", {})

    # Vérifie qu'un nom de champ donné est inclu ou exclu
    def field_allowed(field_name):
        return field_name in metadata.get("fields", []) or (
            field_name not in metadata.get("exclude", []) and field_name not in exclude_related.get(model, [])
        )

    # Création du serializer et du viewset
    serializer = to_model_serializer(model, **metadata)(
        type(object_name + "Serializer", _serializer_base, _serializer_data)
    )
    viewset = to_model_viewset(model, serializer, permissions, bases=_bases, **metadata)(
        type(object_name + "ViewSet", _viewset_base, _viewset_data)
    )

    # Surcharge du queryset par défaut dans le viewset
    if queryset is not None:
        viewset.queryset = queryset

    # Gestion des clés étrangères
    relateds = set()
    prefetchs = set()
    prefetchs_metadata = set()  # Prefetch pour récupérer les métadonnées à chaque niveau
    excludes = set()

    for field in model._meta.fields:
        if field.primary_key or not field.remote_field or field.related_model is _origin:
            continue
        # Vérification que le champ est bien inclu ou n'est pas exclu
        if not field_allowed(field.name):
            excludes.add(field.name)
            continue
        # Ajout du serializer pour la relation de clé étrangère
        if (foreign_keys and 0 >= _level > -height) or (fks_in_related and _level > 0):
            fk_serializer, fk_viewset = create_model_serializer_and_viewset(
                field.related_model,
                foreign_keys=foreign_keys,
                many_to_many=False,
                one_to_one=False,
                one_to_many=False,
                fks_in_related=False,
                null_fks=False,
                serializer_base=serializer_base,
                viewset_base=viewset_base,
                serializer_data=serializer_data,
                viewset_data=viewset_data,
                exclude_related=exclude_related,
                hyperlinked=hyperlinked,
                metas=metas,
                depth=0,
                height=height,
                _level=_level - 1,
                _origin=model,
                _field=field.name,
            )
            serializer._declared_fields[field.name] = fk_serializer(read_only=True)
            relateds.add(field.name)
            # Récupération des relations de plus haut niveau si nécessaire
            field_relateds = get_related(field.related_model, nullables=null_fks, height=height - 1, _models=[model])
            relateds.update(
                [
                    "__".join([field.name, field_related])
                    for field_related in field_relateds
                    if field_related not in exclude_related.get(field.related_model, [])
                ]
            )
        elif _level > 0:
            # Les clés étrangères des relations inversées qui pointent sur le modèle d'origine peuvent être nulles
            if field.remote_field and not field.primary_key and field.related_model is _origin:
                serializer.Meta.extra_kwargs[field.name] = dict(required=False, allow_null=True)
        # Prefetch des métadonnées
        prefetchs_metadata.update(prefetch_metadata(field.related_model, field.name))

    # Gestion des many-to-many
    if many_to_many and depth > _level:
        for field in model._meta.many_to_many:
            # Vérification que le champ est bien inclu ou n'est pas exclu
            if not field_allowed(field.name):
                excludes.add(field.name)
                continue
            # Ajout du serializer pour la relation many-to-many
            m2m_serializer, m2m_viewset = create_model_serializer_and_viewset(
                field.related_model,
                foreign_keys=False,
                many_to_many=False,
                one_to_one=False,
                one_to_many=False,
                fks_in_related=False,
                null_fks=False,
                serializer_base=serializer_base,
                viewset_base=viewset_base,
                serializer_data=serializer_data,
                viewset_data=viewset_data,
                exclude_related=exclude_related,
                hyperlinked=hyperlinked,
                metas=metas,
                depth=0,
                height=0,
                _level=0,
                _origin=model,
                _field=field.name,
            )
            serializer._declared_fields[field.name] = m2m_serializer(many=True, read_only=True)
            prefetchs.add(field.name)
            # Prefetch des métadonnées
            prefetchs_metadata.update(prefetch_metadata(field.related_model, field.name))
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
                excludes.add(field.name)
                continue
            field_name = field.get_accessor_name()
            # Ajout du serializer pour la relation inversée
            child_serializer, child_viewset = create_model_serializer_and_viewset(
                field.related_model,
                foreign_keys=foreign_keys,
                many_to_many=many_to_many,
                one_to_one=one_to_one,
                one_to_many=one_to_many,
                fks_in_related=fks_in_related,
                null_fks=null_fks,
                serializer_base=serializer_base,
                viewset_base=viewset_base,
                serializer_data=serializer_data,
                viewset_data=viewset_data,
                exclude_related=exclude_related,
                hyperlinked=hyperlinked,
                metas=metas,
                depth=depth,
                height=0,
                _level=_level + 1,
                _origin=model,
                _field=field_name,
            )
            serializer._declared_fields[field_name] = child_serializer(read_only=True)
            relateds.add(field_name)
            # Récupération des relations de plus haut niveau si nécessaire
            field_relateds = get_related(
                field.related_model, one_to_one=True, nullables=null_fks, height=height - 1, _models=[model]
            )
            relateds.update(
                [
                    "__".join([field_name, field_related])
                    for field_related in field_relateds
                    if field_related not in exclude_related.get(field.related_model, [])
                ]
            )

    # Gestion des one-to-many
    if one_to_many and depth > _level:
        for field in model._meta.related_objects:
            if not field.auto_created or not field.one_to_many:
                continue
            # Vérification que le champ est bien inclu ou n'est pas exclu, et qu'il s'agisse bien d'un champ
            if not field_allowed(field.name):
                excludes.add(field.name)
                continue
            field_name = field.get_accessor_name()
            # Ajout du serializer pour la relation inversée
            child_serializer, child_viewset = create_model_serializer_and_viewset(
                field.related_model,
                foreign_keys=foreign_keys,
                many_to_many=many_to_many,
                one_to_one=one_to_one,
                one_to_many=one_to_many,
                fks_in_related=fks_in_related,
                null_fks=null_fks,
                serializer_base=serializer_base,
                viewset_base=viewset_base,
                serializer_data=serializer_data,
                viewset_data=viewset_data,
                exclude_related=exclude_related,
                hyperlinked=hyperlinked,
                metas=metas,
                depth=depth,
                height=0,
                _level=_level + 1,
                _origin=model,
                _field=field_name,
            )
            serializer._declared_fields[field_name] = child_serializer(many=True, read_only=True)

    # Récupération des relations inversées
    arguments = dict(
        depth=depth,
        excludes=excludes,
        foreign_keys=fks_in_related,
        one_to_one=one_to_one,
        one_to_many=one_to_many,
        many_to_many=many_to_many,
        nullables=null_fks,
    )
    prefetchs.update(get_prefetchs(model, **arguments))
    prefetchs_metadata.update(get_prefetchs(model, metadata=True, **arguments))

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
        request = item.request if hasattr(item, "request") else item
        valid = None
        valid_date = None
        params = request.data if request.data else request.query_params
        if params:
            valid = str_to_bool(params.get("valid", None))
            valid_date = parsedate(params.get("valid_date", None))
        setattr(request, "valid", valid)
        setattr(request, "valid_date", valid_date)
        setattr(request, "valid_filter", dict(valid=valid, date=valid_date))
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
            try:
                many = isinstance(result, (list, QuerySet))
                return Response(serializer(result, many=many, context=dict(request=request)).data)
            except:  # noqa
                return Response(result)

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
                post_handler = getattr(view_class, "post", None)
                if post_handler:

                    def handler(self, request, *args, **kwargs):
                        serializer_instance = input_serializer(data=request.data)
                        serializer_instance.is_valid(raise_exception=True)
                        request.validated_data = serializer_instance.validated_data
                        return post_handler(self, request, *args, **kwargs)

                    view_class.post = handler
                # PUT
                put_handler = getattr(view_class, "put", None)
                if put_handler:

                    def handler(self, request, *args, **kwargs):
                        partial = kwargs.pop("partial", False)
                        instance = self.get_object()
                        serializer_instance = input_serializer(instance, data=request.data, partial=partial)
                        serializer_instance.is_valid(raise_exception=True)
                        request.validated_data = serializer_instance.validated_data
                        return post_handler(self, request, *args, **kwargs)

                    view_class.put = handler
        return view

    return decorator


def auto_view(
    http_method_names=None,
    input_serializer=None,
    serializer=None,
    validation=True,
    many=False,
    enable_options=True,
    custom_func=None,
    query_func=None,
    func_args=None,
    func_kwargs=None,
):
    """
    Décorateur permettant de générer le corps d'une APIView à partir d'un QuerySet
    :param http_method_names: Méthodes HTTP supportées
    :param input_serializer: Serializer des données d'entrée
    :param serializer: Serializer des données de sortie
    :param validation: Exécuter la validation des données d'entrée ? (request contiendra alors "validated_data")
    :param many: Affichage de plusieurs éléments ou élément individuel (404 si élément non trouvé) ?
    :param enable_options: Active toutes les options de filtre/tri/aggregation/distinct
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
                    request,
                    queryset,
                    serializer,
                    enable_options=enable_options,
                    context=context,
                    query_func=query_func,
                    func_args=func_args,
                    func_kwargs=func_kwargs,
                )
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
            validation=validation,
        )(wrapped)

    return wrapper


def api_paginate(
    request,
    queryset,
    serializer,
    pagination=None,
    enable_options=True,
    context=None,
    query_func=None,
    func_args=None,
    func_kwargs=None,
):
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
    default_reserved_query_params = ["format", pagination.page_query_param, pagination.page_size_query_param]
    reserved_query_params = default_reserved_query_params + RESERVED_QUERY_PARAMS

    url_params = request.query_params.dict()
    context = dict(request=request, **(context or {}))
    options = dict(aggregates=None, annotates=None, distinct=None, filters=None, order_by=None)

    # Activation des options
    if enable_options:
        # Copie des modèles d'origine de la requête pour vérification des permissions
        if settings.ENABLE_API_PERMISSIONS:
            base_queryset_models = get_models_from_queryset(queryset)

        base_url = request.build_absolute_uri(request.path)

        # Critères de recherche dans le cache
        cache_key, save_as = url_params.pop("cache", None), url_params.pop("save_as", None)
        if cache_key:
            from django.core.cache import cache

            cache_params = cache.get(settings.API_CACHE_PREFIX + cache_key, {})
            if cache_params:
                new_url_params = {}
                new_url_params.update(**cache_params)
                new_url_params.update(**url_params)
                url_params = new_url_params
                raw_url, cache_url = "{}?".format(base_url), "{}?cache={}".format(base_url, cache_key)
                for key, value in url_params.items():
                    raw_url += "&{}={}".format(key, value)
                options["raw_url"] = raw_url
                options["cache_url"] = cache_url
                options["cache_data"] = cache_params

        # Enregistrement dans le cache
        if save_as or options.get("cache_data"):
            from django.core.cache import cache

            cache_key = save_as or cache_key
            cache_params = url_params if save_as else options.get("cache_data")
            if cache_params:
                cache_timeout = int(url_params.pop("timeout", settings.API_CACHE_TIMEOUT)) or None
                cache_expires = now() + timedelta(seconds=cache_timeout) if cache_timeout else "never"
                cache.set(settings.API_CACHE_PREFIX + cache_key, cache_params, timeout=cache_timeout)
                if not options.get("cache_data"):
                    raw_url, cache_url = "{}?".format(base_url), "{}?cache={}".format(base_url, cache_key)
                    for key, value in url_params.items():
                        raw_url += "&{}={}".format(key, value)
                    options["raw_url"] = raw_url
                    options["cache_url"] = cache_url
                    options["cache_data"] = url_params
                options["cache_expires"] = cache_expires

        # Erreurs silencieuses
        silent = str_to_bool(url_params.get("silent", ""))

        # Filtres (dans une fonction pour être appelé par les aggregations sans group_by)
        def do_filter(queryset):
            try:
                filters = []
                for key, value in url_params.items():
                    if key in reserved_query_params:
                        continue
                    is_exclude = key.startswith("-")
                    key = key.strip().strip("-").strip("+").strip("@").replace(".", "__")
                    value = url_value(key, parse_arg_value(value, key=key) or value)
                    filters.append(~Q(**{key: value}) if is_exclude else Q(**{key: value}))
                for filter in filters:
                    queryset = queryset.filter(filter)
                # Filtres génériques
                others = url_params.get("filters", "")
                if others:
                    queryset = queryset.filter(parse_filters(others))
                if filters or others:
                    options["filters"] = True
            except Exception as error:
                if not silent:
                    raise ValidationError({"filters": error}, code="filters")
                options["filters"] = False
                if settings.DEBUG:
                    options["filters_error"] = str(error)
            return queryset

        # Annotations
        annotations = {}
        try:
            for annotation in url_params:
                if annotation not in FUNCTIONS:
                    continue
                function = FUNCTIONS[annotation]
                for field_name in url_params.pop(annotation).split(","):
                    field_name, field_rename = (field_name.split("|") + [""])[:2]
                    field_name, *args = field_name.split(";")
                    function_args, function_kwargs = [], {}
                    for index, arg in enumerate(args, start=1):
                        value = convert_arg(annotation, index, arg)
                        if isinstance(value, dict):
                            function_kwargs.update(value)
                        else:
                            function_args.append(value)
                    field_name = field_name.replace(".", "__")
                    field = field_name
                    if any(field_name.endswith(":{}".format(cast)) for cast in CASTS):
                        field_name, *_, cast = field_name.split(":")
                        output_field = CASTS.get(cast.lower())
                        field = functions.Cast(field_name, output_field=output_field) if output_field else field_name
                    field_rename = field_rename or ((annotation + "__" + field_name) if field_name else annotation)
                    if field:
                        function_args.insert(0, field)
                    annotations[field_rename] = function(*function_args, **function_kwargs)
            if annotations:
                queryset = queryset.annotate(**annotations)
                options["annotates"] = True
        except Exception as error:
            if not silent:
                raise ValidationError({"annotates": error}, code="annotates")
            options["annotates"] = False
            if settings.DEBUG:
                options["annotates_error"] = str(error)

        # Aggregations
        aggregations = {}
        try:
            for aggregate in url_params:
                if aggregate not in AGGREGATES:
                    continue
                function = AGGREGATES[aggregate]
                for field_name in url_params.get(aggregate).split(","):
                    distinct = field_name.startswith(" ") or field_name.startswith("+")
                    field_name, field_rename = (field_name.split("|") + [""])[:2]
                    field_name = field_name[1:] if distinct else field_name
                    field_name, *args = field_name.split(";")
                    function_args, function_kwargs = [], {}
                    for index, arg in enumerate(args, start=1):
                        value = convert_arg(aggregate, index, arg)
                        if isinstance(value, dict):
                            function_kwargs.update(value)
                        else:
                            function_args.append(value)
                    field_name = field_name.replace(".", "__")
                    field = field_name
                    if any(field_name.endswith(":{}".format(cast)) for cast in CASTS):
                        field_name, *_, cast = field_name.split(":")
                        output_field = CASTS.get(cast.lower())
                        field = functions.Cast(field_name, output_field=output_field) if output_field else field_name
                    field_rename = field_rename or ((aggregate + "__" + field_name) if field_name else aggregate)
                    if distinct:
                        function_kwargs.update(distinct=distinct)
                    aggregations[field_rename] = function(field, *function_args, **function_kwargs)
            group_by = url_params.get("group_by", "")
            if group_by:
                _queryset = queryset.values(*group_by.replace(".", "__").split(","))
                if aggregations:
                    _queryset = _queryset.annotate(**aggregations)
                else:
                    _queryset = _queryset.distinct()
                queryset = _queryset
                options["aggregates"] = True
            elif aggregations:
                options["aggregates"] = True
                queryset = do_filter(queryset)  # Filtres éventuels
                return queryset.aggregate(**aggregations)
        except ValidationError:
            raise
        except Exception as error:
            if not silent:
                raise ValidationError({"aggregates": error}, code="aggregates")
            options["aggregates"] = False
            if settings.DEBUG:
                options["aggregates_error"] = str(error)

        # Filtres
        queryset = do_filter(queryset)

        # Tris
        orders = []
        try:
            order_by = url_params.get("order_by", "")
            if order_by:
                for order in order_by.replace(".", "__").split(","):
                    order_by_kwargs = {}
                    if order.endswith("<"):
                        order_by_kwargs.update(nulls_first=True)
                    elif order.endswith(">"):
                        order_by_kwargs.update(nulls_last=True)
                    order = order[:-1] if order_by_kwargs else order
                    if order.startswith("-"):
                        orders.append(F(order[1:]).desc(**order_by_kwargs))
                    else:
                        order = order[1:] if order.startswith(" ") or order.startswith("+") else order
                        orders.append(F(order).asc(**order_by_kwargs))
                temp_queryset = queryset.order_by(*orders)
                str(temp_queryset.query)  # Force SQL evaluation to retrieve exception
                queryset = temp_queryset
                options["order_by"] = True
        except EmptyResultSet:
            pass
        except Exception as error:
            if not silent:
                raise ValidationError({"order_by": error}, code="order_by")
            options["order_by"] = False
            if settings.DEBUG:
                options["order_by_error"] = str(error)

        # Distinct
        distincts = []
        try:
            distinct = url_params.get("distinct", "")
            if distinct:
                distincts = distinct.replace(".", "__").split(",")
                if str_to_bool(distinct) is not None:
                    distincts = []
                queryset = queryset.distinct(*distincts)
                options["distinct"] = True
        except EmptyResultSet:
            pass
        except Exception as error:
            if not silent:
                raise ValidationError({"distinct": error}, code="distinct")
            options["distinct"] = False
            if settings.DEBUG:
                options["distinct_error"] = str(error)

        # Extraction de champs spécifiques
        fields = url_params.get("fields", "")
        if fields:
            # Supprime la récupération des relations
            queryset = queryset.select_related(None).prefetch_related(None)
            # Champs spécifiques
            try:
                relateds = set()
                field_names = set()
                for field in fields.replace(".", "__").split(","):
                    if not field:
                        continue
                    field_names.add(field)
                    *related, field_name = field.split("__")
                    if related and field not in annotations:
                        relateds.add("__".join(related))
                if relateds:
                    queryset = queryset.select_related(*relateds)
                if field_names:
                    queryset = queryset.values(*field_names)
            except Exception as error:
                if not silent:
                    raise ValidationError({"fields": error}, code="fields")

        # Fonction utilitaire d'ajout de champ au serializer
        def add_field_to_serializer(fields, field_name):
            source = field_name.replace(".", "__")
            # Champ spécifique en cas d'énumération
            choices = getattr(get_field_by_path(queryset.model, field_name), "flatchoices", None)
            if choices and str_to_bool(url_params.get("display", "")):
                fields[field_name + "_display"] = ChoiceDisplayField(choices=choices, source=source)
            # Champ spécifique pour l'affichage de la valeur
            fields[field_name] = ReadOnlyObjectField(source=source if "." in field_name else None)

        # Création de serializer à la volée en cas d'aggregation ou de restriction de champs
        aggregations = {}
        for aggregate in url_params:
            if aggregate not in AGGREGATES:
                continue
            for field in url_params.get(aggregate).split(","):
                field_name = (aggregate + "__" + field.strip()) if field else aggregate
                field_name, field_rename = (field_name.split("|") + [""])[:2]
                source = field_name.replace(".", "__") if "." in field else None
                aggregations[field_rename or field_name] = serializers.ReadOnlyField(source=source)

        # Regroupements & aggregations
        if "group_by" in url_params or aggregations:
            fields = {}
            for field in url_params.get("group_by", "").split(","):
                add_field_to_serializer(fields, field)
            fields.update(aggregations)
            fields.update(annotations)
            # Un serializer avec les données groupées est créé à la volée
            serializer = type(serializer.__name__, (serializers.Serializer,), fields)
        # Restriction de champs
        elif "fields" in url_params:
            fields = {}
            for field in url_params.get("fields", "").split(","):
                add_field_to_serializer(fields, field)
            fields.update(annotations)
            # Un serializer avec restriction des champs est créé à la volée
            serializer = type(serializer.__name__, (serializers.Serializer,), fields)
        elif annotations:
            serializer._declared_fields.update({key: serializers.ReadOnlyField() for key, value in annotations.items()})

        # Vérifie les droits sur les différents modèles traversés
        if settings.ENABLE_API_PERMISSIONS and request.user and hasattr(queryset, "query"):
            new_queryset_models = get_models_from_queryset(queryset) - base_queryset_models
            permissions = get_model_permissions(request.user, *new_queryset_models)
            for permission_code, permission_value in permissions.items():
                if not permission_value:
                    raise PermissionDenied({permission_code: PermissionDenied.default_detail})

    # Fonction spécifique
    if query_func:
        func_args = func_args or []
        func_kwargs = func_kwargs or {}
        queryset = query_func(queryset, *func_args, **func_kwargs)

    # Uniquement si toutes les données sont demandées
    all_data = str_to_bool(url_params.get("all", ""))
    if all_data:
        return Response(serializer(queryset, context=context, many=True).data)

    # Pagination avec ajout des options de filtres/tris dans la pagination
    paginator = pagination()
    if enable_options and hasattr(paginator, "additional_data"):
        paginator.additional_data = dict(options=options)
    # Force un tri sur la clé primaire en cas de pagination
    if hasattr(queryset, "ordered") and not queryset.ordered:
        primary_key = get_pk_field(queryset.model)
        queryset = queryset.order_by(
            *(getattr(queryset, "_fields", None) or (enable_options and distincts) or [primary_key.name])
        )

    serializer = serializer(paginator.paginate_queryset(queryset, request), context=context, many=True)
    return paginator.get_paginated_response(serializer.data)


def create_api(
    *models,
    default_config=None,
    router=None,
    all_serializers=None,
    all_viewsets=None,
    all_bases_serializers=None,
    all_bases_viewsets=None,
    all_data_serializers=None,
    all_data_viewsets=None,
    all_querysets=None,
    all_metadata=None,
    all_configs=None,
    **config,
):
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
    :param all_configs: Toutes les configurations créées jusqu'à présent
    :param config: Configuration spécifique aux modèles
    :return: Router, Serializers, Viewsets
    """
    serializers = {}
    viewsets = {}

    # Récupération de la configuration générale
    from common.api.base import (
        CONFIGS,
        DEFAULT_CONFIG,
        METADATA,
        QUERYSETS,
        SERIALIZERS,
        SERIALIZERS_BASE,
        SERIALIZERS_DATA,
        VIEWSETS,
        VIEWSETS_BASE,
        VIEWSETS_DATA,
    )

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
        configuration = all_configs.get(model, default_config or {})
        configuration.update(config)
        serializers[model], viewsets[model] = create_model_serializer_and_viewset(
            model,
            serializer_base=all_bases_serializers,
            viewset_base=all_bases_viewsets,
            serializer_data=all_data_serializers,
            viewset_data=all_data_viewsets,
            queryset=all_querysets.get(model, None),
            metas=all_metadata,
            **configuration,
        )

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
                metas[field.name] = dict(style={"base_template": "input.html", "placeholder": str(field.verbose_name)})
        if metas:
            metadata = all_metadata[model] = all_metadata.get(model, {})
            extra_kwargs = metadata["extra_kwargs"] = metadata.get("extra_kwargs", {})
            for key, value in metas.items():
                extra_kwargs[key] = extra_kwargs.get(key, {})
                extra_kwargs[key].update(value)


def parse_ordering(ordering):
    """
    Parse une instruction de tri pour certaines fonctions d'aggregation
    :param ordering: Chaîne de tri
    :return: Liste de champs à trier
    """
    if isinstance(ordering, (list, tuple)):
        return ordering
    if isinstance(ordering, str):
        return [item.replace(".", "__") for item in re.split(r"[^-\w\.]", ordering)]
    return None


# Conversion des arguments des fonctions
parse_param = partial(parse_arg_value, keep=True)
FUNC_COMMON = {"output_field": CASTS.get}
TRUNC_BASE_CONVERT = {
    1: str,
    2: CASTS.get,
    3: zoneinfo.ZoneInfo,
    "kind": str,
    "tzinfo": zoneinfo.ZoneInfo,
    **FUNC_COMMON,
}
TRUNC_CONVERT = {
    1: CASTS.get,
    2: zoneinfo.ZoneInfo,
    "tzinfo": zoneinfo.ZoneInfo,
    **FUNC_COMMON,
}
EXTRACT_CONVERT = {1: zoneinfo.ZoneInfo, "tzinfo": zoneinfo.ZoneInfo, **FUNC_COMMON}
AGGREGATE_BASE = {"filter": parse_filters, "default": Value, **FUNC_COMMON}
AGGREGATE_COMMON = {"distinct": str_to_bool, **AGGREGATE_BASE}
AGGREGATE_STATS = {1: str, "y": str, **AGGREGATE_BASE}
TRIGRAM_COMMON = {1: str, 2: str}
CONVERTS = {
    # Functions
    "cast": {1: CASTS.get, **FUNC_COMMON},
    "coalesce": {**{i: str for i in range(10)}, **FUNC_COMMON},
    "collate": {1: str, "collation": str, **FUNC_COMMON},
    "greatest": {**{i: str for i in range(10)}, **FUNC_COMMON},
    "least": {**{i: str for i in range(10)}, **FUNC_COMMON},
    "nullif": {1: str, **FUNC_COMMON},
    "extract": {1: str, 2: zoneinfo.ZoneInfo, "tzinfo": zoneinfo.ZoneInfo, **FUNC_COMMON},
    "extract_year": EXTRACT_CONVERT,
    "extract_iso_year": EXTRACT_CONVERT,
    "extract_month": EXTRACT_CONVERT,
    "extract_day": EXTRACT_CONVERT,
    "extract_week_day": EXTRACT_CONVERT,
    "extract_iso_week_day": EXTRACT_CONVERT,
    "extract_week": EXTRACT_CONVERT,
    "extract_quarter": EXTRACT_CONVERT,
    "extract_hour": EXTRACT_CONVERT,
    "extract_minute": EXTRACT_CONVERT,
    "extract_second": EXTRACT_CONVERT,
    "now": None,
    "trunc": TRUNC_BASE_CONVERT,
    "trunc_year": TRUNC_CONVERT,
    "trunc_month": TRUNC_CONVERT,
    "trunc_week": TRUNC_CONVERT,
    "trunc_quarter": TRUNC_CONVERT,
    "trunc_date": TRUNC_CONVERT,
    "trunc_time": TRUNC_CONVERT,
    "trunc_day": TRUNC_CONVERT,
    "trunc_hour": TRUNC_CONVERT,
    "trunc_minute": TRUNC_CONVERT,
    "trunc_second": TRUNC_CONVERT,
    "pi": None,
    "random": None,
    "round": {1: int, "precision": int, **FUNC_COMMON},
    "left": {1: int, "length": int, **FUNC_COMMON},
    "lpad": {1: int, 2: Value, "length": int, "fill_value": Value, **FUNC_COMMON},
    "repeat": {1: int, "number": int, **FUNC_COMMON},
    "replace": {1: Value, 2: Value, "text": Value, "replacement": Value, **FUNC_COMMON},
    "right": {1: int, "length": int, **FUNC_COMMON},
    "rpad": {1: int, 2: Value, "length": int, "fill_value": Value, **FUNC_COMMON},
    "strindex": {1: Value, **FUNC_COMMON},
    "substr": {1: int, 2: int, "pos": int, "length": int, **FUNC_COMMON},
    # Aggregates
    "count": AGGREGATE_COMMON,
    "sum": AGGREGATE_COMMON,
    "avg": AGGREGATE_COMMON,
    "min": AGGREGATE_COMMON,
    "max": AGGREGATE_COMMON,
    "stddev": {"sample": str_to_bool, **AGGREGATE_COMMON},
    "variance": {"sample": str_to_bool, **AGGREGATE_COMMON},
    "arrayagg": {"ordering": parse_ordering, **AGGREGATE_COMMON},
    "bitand": AGGREGATE_COMMON,
    "bitor": AGGREGATE_COMMON,
    "bitxor": AGGREGATE_COMMON,
    "booland": AGGREGATE_COMMON,
    "boolor": AGGREGATE_COMMON,
    "jsonbagg": {"ordering": parse_ordering, **AGGREGATE_COMMON},
    "stringagg": {1: str, "delimiter": str, "ordering": parse_ordering, **AGGREGATE_COMMON},
    "corr": AGGREGATE_STATS,
    "covarpop": {2: str_to_bool, "sample": str_to_bool, **AGGREGATE_STATS},
    "regravgx": AGGREGATE_STATS,
    "regravgy": AGGREGATE_STATS,
    "regrcount": AGGREGATE_STATS,
    "regrintercept": AGGREGATE_STATS,
    "regrr2": AGGREGATE_STATS,
    "regrslope": AGGREGATE_STATS,
    "regrsxx": AGGREGATE_STATS,
    "regrsxy": AGGREGATE_STATS,
    "regrsyy": AGGREGATE_STATS,
    # Search
    "search_vector": {"config": parse_param, "weight": str},
    "search_rank": {1: parse_param, 2: parse_param, "weights": tuple, "cover_density": str_to_bool},
    "search_headline": {
        1: str,
        2: parse_param,
        "config": str,
        "start_sel": str,
        "stop_sel": str,
        "max_words": int,
        "min_words": int,
        "short_word": int,
        "highlight_all": str_to_bool,
        "max_fragments": int,
        "fragment_delimiter": str,
    },
    "trigram_similarity": TRIGRAM_COMMON,
    "trigram_word_similarity": TRIGRAM_COMMON,
    "trigram_distance": TRIGRAM_COMMON,
    "trigram_word_distance": TRIGRAM_COMMON,
    "trigram_strict_word_similarity": TRIGRAM_COMMON,
    "trigram_strict_word_distance": TRIGRAM_COMMON,
}
