# coding: utf-8
from datetime import timedelta

from django.core.exceptions import EmptyResultSet, FieldDoesNotExist
from django.db import ProgrammingError
from django.db.models import functions
from django.db.models.query import F, Prefetch, Q, QuerySet
from django.utils.timezone import now
from rest_framework import serializers, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError

from common.api.fields import ChoiceDisplayField, ReadOnlyObjectField
from common.api.utils import (
    AGGREGATES,
    CASTS,
    FUNCTIONS,
    RESERVED_QUERY_PARAMS,
    convert_arg,
    parse_arg_value,
    parse_filters,
    url_value,
)
from common.models import Entity, MetaData
from common.settings import settings
from common.utils import get_field_by_path, get_model_permissions, get_models_from_queryset, get_pk_field, str_to_bool


class CommonModelViewSet(viewsets.ModelViewSet):
    """
    Définition commune de ModelViewSet pour l'API REST
    """

    url_params = {}

    def get_serializer_class(self):
        # Le serializer par défaut est utilisé en cas de modification/suppression
        default_serializer = getattr(self, "default_serializer", None)
        if default_serializer and self.action not in ("list", "retrieve", "update", "partial_update"):
            return default_serializer

        # Le serializer peut être substitué en fonction des paramètres d'appel de l'API
        query_params = getattr(self.request, "query_params", None)
        url_params = self.url_params or (query_params.dict() if query_params else {})
        if default_serializer:
            # Fonction utilitaire d'ajout de champ au serializer
            def add_field_to_serializer(fields, field_name):
                source = field_name.replace(".", "__")
                # Champ spécifique en cas d'énumération
                choices = getattr(get_field_by_path(self.queryset.model, field_name), "flatchoices", None)
                if choices and str_to_bool(url_params.get("display")):
                    fields[field_name + "_display"] = ChoiceDisplayField(choices=choices, source=source)
                # Champ spécifique pour l'affichage de la valeur
                fields[field_name] = ReadOnlyObjectField(source=source if "." in field_name else None)

            # Ajoute les champs d'annotation au serializer
            annotations = {}
            for annotation in url_params:
                if annotation not in FUNCTIONS:
                    continue
                for field in url_params.get(annotation).split(","):
                    field_name, field_rename = (field.split("|") + [""])[:2]
                    source = None
                    if not field_rename:
                        field_name = (annotation + "__" + field_name) if field_name else annotation
                        field_name, *args = field_name.split(";")
                        if any(field_name.endswith(":{}".format(cast)) for cast in CASTS):
                            field_name, *casts = field_name.split(":")
                        source = field_name.replace(".", "__") if "." in field else None
                    field_rename = field_rename or field_name
                    annotations[field_rename] = serializers.ReadOnlyField(source=source)

            # Ajoute les champs d'aggregation au serializer
            aggregations = {}
            for aggregate in url_params:
                if aggregate not in AGGREGATES:
                    continue
                for field in url_params.get(aggregate).split(","):
                    field_name, field_rename = (field.strip().split("|") + [""])[:2]
                    source = None
                    if not field_rename:
                        field_name = (aggregate + "__" + field_name) if field_name else aggregate
                        field_name, *args = field_name.split(";")
                        if any(field_name.endswith(":{}".format(cast)) for cast in CASTS):
                            field_name, *casts = field_name.split(":")
                        source = field_name.replace(".", "__") if "." in field else None
                    field_rename = field_rename or field_name
                    aggregations[field_rename] = serializers.ReadOnlyField(source=source)

            # Ajoute les regroupements au serializer
            if "group_by" in url_params or aggregations:
                fields = {}
                for field in url_params.get("group_by", "").split(","):
                    add_field_to_serializer(fields, field)
                fields.update(aggregations)
                # Un serializer avec les données regroupées est créé à la volée
                return type(default_serializer.__name__, (serializers.Serializer,), fields)

            # Ajoute la restriction des champs au serializer
            elif "fields" in url_params:
                fields = {}
                for field in url_params.get("fields").split(","):
                    add_field_to_serializer(fields, field)
                # Un serializer avec restriction des champs est créé à la volée
                return type(default_serializer.__name__, (serializers.Serializer,), fields)

            # Utilisation du serializer simplifié
            elif str_to_bool(url_params.get("simple")):
                serializer = getattr(self, "simple_serializer", default_serializer)
                serializer._declared_fields.update(annotations)
                return serializer

            # Utilisation du serializer par défaut en cas de mise à jour sans altération des données
            elif self.action in ("update", "partial_update"):
                return default_serializer

            # Ajoute les annotations au serializer par défaut
            elif not aggregations and annotations:
                serializer = super().get_serializer_class()
                serializer._declared_fields.update(annotations)
                return serializer

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
            raise ValidationError({"fields": error}, code="fields")

    def paginate_queryset(self, queryset):
        # Aucune pagination si toutes les données sont demandées ou qu'il ne s'agit pas d'un QuerySet
        if not isinstance(queryset, QuerySet) or str_to_bool(self.request.query_params.get("all", None)):
            return None
        try:
            return super().paginate_queryset(queryset)
        except ProgrammingError as error:
            raise ValidationError({"page": error}, code="page")

    def get_queryset(self):
        # Evite la ré-évaluation du QuerySet en cas d'erreur
        if getattr(self, "queryset_error", False):
            return

        try:
            # Détournement en cas d'aggregation sans annotation ou de non QuerySet
            queryset = super().get_queryset()
            if not isinstance(queryset, QuerySet):
                return queryset

            options = dict(aggregates=None, annotates=None, distinct=None, filters=None, order_by=None)
            self.url_params = url_params = self.request.query_params.dict()

            # Mots-clés réservés dans les URLs
            default_reserved_query_params = ["format"] + (
                [self.paginator.page_query_param, self.paginator.page_size_query_param] if self.paginator else []
            )
            reserved_query_params = default_reserved_query_params + RESERVED_QUERY_PARAMS

            # Copie des modèles d'origine de la requête pour vérification des permissions
            if settings.ENABLE_API_PERMISSIONS:
                base_queryset_models = get_models_from_queryset(queryset)

            base_url = self.request.build_absolute_uri(self.request.path)

            # Critères de recherche dans le cache
            cache_key, save_as = url_params.pop("cache", None), url_params.pop("save_as", None)
            if cache_key:
                from django.core.cache import cache

                cache_params = cache.get(settings.API_CACHE_PREFIX + cache_key, {})
                if cache_params:
                    new_url_params = {}
                    new_url_params.update(**cache_params)
                    new_url_params.update(**url_params)
                    self.url_params = url_params = new_url_params
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
                    for field_name in url_params.get(annotation).split(","):
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
                            field = (
                                functions.Cast(field_name, output_field=output_field) if output_field else field_name
                            )
                        field_rename = field_rename or ((annotation + "__" + field_name) if field_name else annotation)
                        if field:
                            function_args.insert(0, field)
                        annotations[field_rename] = function(*function_args, **function_kwargs)
                if annotations:
                    queryset = queryset.annotate(**annotations)
                    options["annotates"] = True
            except Exception as error:
                if not silent:
                    raise ValidationError({"annotate": error}, code="annotates")
                options["annotates"] = False
                if settings.DEBUG:
                    options["annotates_error"] = str(error)

            # Aggregations
            aggregations = {}
            if self.action == "list":
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
                                field = (
                                    functions.Cast(field_name, output_field=output_field)
                                    if output_field
                                    else field_name
                                )
                            field_rename = field_rename or (aggregate + "__" + field_name)
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

            # Requête simplifiée et/ou extraction de champs spécifiques
            fields = url_params.get("fields", "")
            if str_to_bool(url_params.get("simple", "")) or fields:
                # Supprime la récupération des relations
                if queryset.query.select_related:
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
            else:
                # Récupération des métadonnées
                metadata = str_to_bool(url_params.get("meta", ""))
                if metadata and hasattr(self, "metadata"):
                    # Permet d'éviter les conflits entre prefetch lookups identiques
                    viewset_lookups = [
                        prefetch if isinstance(prefetch, str) else prefetch.prefetch_through
                        for prefetch in queryset._prefetch_related_lookups
                    ]
                    lookups_metadata = []
                    for lookup in self.metadata or []:
                        if isinstance(lookup, str):
                            lookup = Prefetch(lookup)
                        if lookup.prefetch_through not in viewset_lookups:
                            lookups_metadata.append(lookup)
                        lookup.queryset = MetaData.objects.select_valid()
                    if lookups_metadata:
                        queryset = queryset.prefetch_related(*lookups_metadata)

            # Vérifie les droits sur les différents modèles traversés
            if settings.ENABLE_API_PERMISSIONS and self.request.user and hasattr(queryset, "query"):
                new_queryset_models = get_models_from_queryset(queryset) - base_queryset_models
                permissions = get_model_permissions(self.request.user, *new_queryset_models)
                for permission_code, permission_value in permissions.items():
                    if not permission_value:
                        raise PermissionDenied({permission_code: PermissionDenied.default_detail})

            # Ajout des options de filtres/tris dans la pagination
            if self.paginator and hasattr(self.paginator, "additional_data"):
                # Force un tri sur la clé primaire en cas de pagination
                if hasattr(queryset, "ordered") and not queryset.ordered:
                    primary_key = get_pk_field(queryset.model)
                    queryset = queryset.order_by(
                        *(getattr(queryset, "_fields", None) or distincts or [primary_key.name])
                    )
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
        elif self.action in ["create"]:
            # Autorise la création pour tout le monde
            return True
        elif self.action in ["update", "partial_update"]:
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
            data["is_active"] = True
        if not user or not user.is_staff:
            data["is_staff"] = False
        if not user or not user.is_superuser:
            data["is_superuser"] = False
        if "groups" in data and data.get("groups"):
            if not user:
                data["groups"] = []
            elif not user.is_superuser:
                groups = user.groups.all()
                data["groups"] = list(set(groups) & set(data.get("groups", [])))
        if "user_permissions" in data and data.get("user_permissions"):
            if not user:
                data["user_permissions"] = []
            elif not user.is_superuser:
                user_permissions = user.user_permissions.all()
                data["user_permissions"] = list(set(user_permissions) & set(data.get("user_permissions", [])))

    def perform_create(self, serializer):
        self.check_data(serializer.validated_data)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self.check_data(serializer.validated_data)
        super().perform_update(serializer)
