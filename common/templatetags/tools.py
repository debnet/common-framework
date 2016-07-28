# coding: utf-8
from django.template import Library

register = Library()


@register.filter(name='meta')
def meta(value, arg, none_value=''):
    if hasattr(value, 'get_metadata'):
        return value.get_metadata(arg)
    return none_value


@register.filter(name='to_date')
def to_date(value, date_only=False):
    from common.utils import parsedate
    _value = parsedate(value)
    if _value and date_only:
        return _value.date()
    return _value


@register.filter(name='dict_value')
def dict_value(dict_entry, key):
    if not dict_entry or key not in dict_entry:
        return ''
    return dict_entry[key]
