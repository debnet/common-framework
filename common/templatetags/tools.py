# coding: utf-8
from django.template import Library
from django.utils.formats import localize


register = Library()


@register.simple_tag(name='meta')
def tag_meta(instance, key, default=''):
    """
    Récupération d'une métadonnée sur une instance
    :param instance: Instance
    :param key: Clé
    :param default: Valeur par défaut
    :return: Valeur
    """
    if hasattr(instance, 'get_metadata'):
        return localize(instance.get_metadata(key))
    return localize(default)


@register.simple_tag(name='parsedate')
def tag_parsedate(value, **options):
    """
    Parse une date ou un datetime dans n'importe quel format
    :param value: Date ou datetime au format texte
    :return: Date ou datetime
    """
    from common.utils import parsedate
    return localize(parsedate(value, **options))


@register.simple_tag(name='get')
def tag_get(value, key, default=''):
    """
    Permet de récupérer une valeur depuis un objet quelconque
    :param value: Objet
    :param key: Clé ou index
    :param default: Valeur par défaut
    :return: Valeur
    """
    if isinstance(value, dict):
        result = value.get(key, default=default)
    elif isinstance(value, (list, tuple)):
        result = value[int(key)]
    else:
        result = getattr(value, key, default)
    return localize(result)


@register.simple_tag(name='query', takes_context=True)
def tag_query(context, queryset, save='', **kwargs):
    """
    Permet de faire des opérations complémentaires sur un QuerySet
    :param context: Contexte local
    :param queryset: QuerySet
    :param save: Nom du contexte qui contiendra le nouveau QuerySet
    :param kwargs: Options de filtre/tri/etc...
    :return: Rien
    """
    from common.api.utils import url_value, AGGREGATES
    from django.db.models import F, QuerySet

    if not isinstance(queryset, QuerySet):
        return queryset

    # Fonction de récupération des données depuis les paramètres
    def get(name):
        return kwargs.get(name, '').replace('.', '__').replace(' ', '')

    reserved_keywords = (
        'filters', 'fields', 'order_by', 'group_by', 'distinct',
        'select_related', 'prefetch_related', 'limit',
    ) + tuple(AGGREGATES.keys())

    # Filtres (dans une fonction pour être appelé par les aggregations sans group_by)
    def do_filter(queryset):
        filters = {}
        excludes = {}
        for key, value in kwargs.items():
            if key in reserved_keywords:
                continue
            key = key.replace('.', '__')
            if value.startswith('(') and value.endswith(')'):
                value = F(value[1:-1])
            if key.startswith('_'):
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
        others = kwargs.get('filters', None)
        if others:
            from common.api.utils import parse_filters
            queryset = queryset.filter(parse_filters(others))
        return queryset

    # Jointures
    select_related = get('select_related')
    if select_related:
        queryset = queryset.select_related(*select_related.split(','))
    prefetch_related = get('prefetch_related')
    if prefetch_related:
        queryset = queryset.prefetch_related(*prefetch_related.split(','))

    # Aggregations
    aggregations = {}
    for aggregate, function in AGGREGATES.items():
        for field in kwargs.get(aggregate, '').split(','):
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
    elif aggregations:
        queryset = do_filter(queryset)  # Filtres éventuels
        return queryset.aggregate(**aggregations)

    # Filtres
    queryset = do_filter(queryset)

    # Extraction de champs spécifiques
    fields = get('fields')
    if fields:
        # Supprime la récupération des relations
        queryset = queryset.select_related(None).prefetch_related(None)
        # Champs spécifiques
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
            queryset = queryset.values_list(*field_names, named=True)

    # Tris
    order_by = get('order_by')
    if order_by:
        _queryset = queryset.order_by(*order_by.split(','))
        str(_queryset.query)  # Force SQL evaluation to retrieve exception
        queryset = _queryset

    # Distinct
    distinct = get('distinct')
    if distinct:
        if distinct is True:
            distincts = ()
        else:
            distincts = distinct.split(',')
        queryset = queryset.distinct(*distincts)

    # Limite
    limit = get('limit')
    if limit:
        limit = [int(l) for l in limit.split(',')]
        limit_inf, limit_sup = (0, limit[0]) if len(limit) == 1 else limit[:2]
        queryset = queryset[limit_inf:limit_sup]

    context[save] = queryset
    return ''
