# coding: utf-8
import ast
import base64
import uuid
from itertools import cycle

from django.conf import settings
from django.http import QueryDict
from django.template import Library, Node
from django.utils.formats import localize
from django.utils.translation import gettext as _


register = Library()


@register.filter(name='meta')
def filter_meta(instance, key):
    """
    Récupération d'une métadonnée sur une instance
    :param key: Clé
    :return: Valeur
    """
    if hasattr(instance, 'get_metadata'):
        return instance.get_metadata(key)
    return None


@register.filter(name='parsedate')
def filter_parsedate(value, options=''):
    """
    Parse une date ou un datetime dans n'importe quel format
    :param value: Date ou datetime au format texte
    :param options: Options de parsing (au format query string)
    :return: Date ou datetime
    """
    from common.utils import parsedate
    options = QueryDict(options)
    return parsedate(value, **options)


@register.filter(name='get')
def filter_get(value, key):
    """
    Permet de récupérer une valeur depuis un objet quelconque
    :param value: Objet
    :param key: Clé ou index
    :return: Valeur
    """
    try:
        if isinstance(value, dict):
            return value.get(str(key)) or value.get(int(key))
        elif isinstance(value, (list, tuple)):
            return value[int(key)]
        else:
            return getattr(value, key, None)
    except (TypeError, ValueError):
        return None


@register.filter(name='localize')
def filter_localize(value, use_l10n=None):
    """
    Localise une valeur brute
    :param value: Valeur
    :param use_l10n: Force ou non la localisation
    :return: Valeur localisée (si possible)
    """
    return localize(value, use_l10n=use_l10n) or value


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
            if isinstance(value, str) and value.startswith('[') and value.endswith(']'):
                value = F(value[1:-1].replace('.', '__'))
            if key.startswith('_'):
                key = key[1:].strip()
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
            distinct = field.startswith(' ') or field.startswith('+')
            field = field[1:] if distinct else field
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
        limit = [int(lim) for lim in limit.split(',')]
        limit_inf, limit_sup = (0, limit[0]) if len(limit) == 1 else limit[:2]
        queryset = queryset[limit_inf:limit_sup]

    context[save] = queryset
    return ''


class PermNode(Node):
    """
    Classe utilitaire pour le template tag des permissions
    """
    def __init__(self, nodelist, *perms, obj=None, any=False):
        self.nodelist = nodelist
        self.perms = perms
        self.obj = obj
        self.any = any

    def render(self, context):
        if not hasattr(context, 'request'):
            return ''
        user = context.request.user
        valid = any(user.has_perm(perm, self.obj) for perm in self.perms) if self.any \
            else user.has_perms(self.perms, self.obj)
        if valid:
            return self.nodelist.render(context)
        return ''


@register.tag(name='perm')
def tag_perm(parser, token):
    """
    Permet d'afficher ou non un contenu en fonction des permissions de l'utilisateur connecté (toutes)
    """
    nodelist = parser.parse(('endperm',))
    parser.delete_first_token()
    args = [ast.literal_eval(bit) for bit in token.split_contents()[1:]]
    return PermNode(nodelist, *args)


@register.tag(name='anyperm')
def tag_anyperm(parser, token):
    """
    Permet d'afficher ou non un contenu en fonction des permissions de l'utilisateur connecté (au moins une)
    """
    nodelist = parser.parse(('endanyperm',))
    parser.delete_first_token()
    args = [ast.literal_eval(bit) for bit in token.split_contents()[1:]]
    return PermNode(nodelist, *args, any=True)


@register.filter(name='conf')
def filter_conf(value):
    """
    Retourne la valeur d'un paramètre de configuration
    """
    return getattr(settings, value, None)


@register.filter(name='gather')
def filter_gather(value, key='', sep=None):
    """
    Permet de rassembler des données selon une clé
    """
    sep = sep or _(", ")
    if not key:
        return sep.join(value)
    return sep.join((v.get(key) if isinstance(v, dict) else getattr(v, key) for v in value))


@register.filter(name='split')
def filter_split(value, sep=' '):
    """
    Permet de diviser une chaîne en fonction d'un séparateur
    """
    return value.split(sep)


@register.simple_tag(name='eval', takes_context=True)
def tag_evaluate(context, text):
    """
    Evalue une chaîne comme un template
    """
    from django.template import Context, Template
    return Template(text).render(Context(context))


class MarkdownNode(Node):
    """
    Classe utilitaire pour le template tag markdown
    """
    def __init__(self, nodelist, *extras):
        self.nodelist = nodelist
        self.extras = extras

    def render(self, context):
        output = self.nodelist.render(context)
        try:
            import markdown2
            return markdown2.markdown(output.strip(), extras=self.extras)
        except:
            import markdown
            markdown.markdown(output.strip(), extensions=self.extras)


@register.tag(name='markdown')
def tag_markdown(parser, token):
    """
    Permet de convertir un format markdown en HTML
    """
    nodelist = parser.parse(('endmarkdown',))
    parser.delete_first_token()
    args = [ast.literal_eval(bit) for bit in token.split_contents()[1:]]
    return MarkdownNode(nodelist, *args)


class ObfuscatorNode(Node):
    """
    Classe utilitaire pour obfusquer les données
    """
    def __init__(self, nodelist, key=None, *args):
        self.nodelist = nodelist
        self.key = key

    def encode(self, data, key=''):
        xored = ''.join(chr(ord(x) ^ ord(y)) for (x, y) in zip(data, cycle(key)))
        return base64.encodebytes(xored.encode()).strip().decode()

    def render(self, context):
        output = self.nodelist.render(context)
        if self.key:
            encoded = self.encode(output, self.key)
            return f'<div data-code="{encoded}"></div>'
        else:
            key = uuid.uuid4().hex
            encoded = self.encode(output, key)
            return f'<div data-key="{key}" data-code="{encoded}"></div>'


@register.tag(name='obfuscate')
def tag_obfuscate(parser, token):
    """
    Permet d'obfusquer des données
    """
    nodelist = parser.parse(('endobfuscate',))
    parser.delete_first_token()
    args = [ast.literal_eval(bit) for bit in token.split_contents()[1:]]
    return ObfuscatorNode(nodelist, *args)


register.filter('any', lambda value: any(value))
register.filter('all', lambda value: all(value))
