# coding: utf-8
import base64
import decimal
import pickle

from django.contrib.postgres.lookups import Unaccent
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import CharField, Lookup, TextField, Transform, lookups
from django.utils.translation import ugettext_lazy as _

from common.utils import json_decode, json_encode


# Vérifie que l'on utilise le moteur de bases de données PostgreSQL
is_postgresql = lambda connection: \
    connection.settings_dict['ENGINE'] in ['django.db.backends.postgresql_psycopg2', 'django.db.backends.postgresql']
is_mysql = lambda connection: connection.settings_dict['ENGINE'] == 'django.db.backends.mysql'
is_sqlite = lambda connection: connection.settings_dict['ENGINE'] == 'django.db.backends.sqlite3'


class CustomDecimalField(models.DecimalField):
    """
    Champ décimal spécifique pour éviter la représentation scientifique
    """

    def value_from_object(self, obj):
        value = super().value_from_object(obj)
        if isinstance(value, decimal.Decimal):
            return self._transform_decimal(value)
        return value

    def _transform_decimal(self, value):
        context = decimal.Context(prec=self.max_digits)
        return value.quantize(decimal.Decimal(1), context=context) \
            if value == value.to_integral() else value.normalize(context)


class PickleField(models.BinaryField):
    """
    Champ binaire utilisant pickle pour sérialiser des données diverses
    """

    def __init__(self, *args, **kwargs):
        default = kwargs.get('default', None)
        if default is not None:
            kwargs['default'] = pickle.dumps(default)
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, *args, **kwargs):
        return self.to_python(value)

    def to_python(self, value):
        if not value:
            return None
        _value = value
        if isinstance(_value, str):
            _value = bytes(_value, encoding='utf-8')
            try:
                _value = base64.b64decode(_value)
            except Exception:
                pass
        try:
            return pickle.loads(_value)
        except Exception:
            return super().to_python(value)

    def get_prep_value(self, value):
        if not value:
            return None if self.null else b''
        if isinstance(value, bytes):
            return value
        return pickle.dumps(value)

    def value_from_object(self, obj):
        value = super().value_from_object(obj)
        return self.to_python(value)

    def value_to_string(self, obj):
        value = self.value_from_object(obj)
        return base64.b64encode(self.get_prep_value(value))


class JsonDict(dict):
    """
    Hack so repr() called by dumpdata will output JSON instead of Python formatted data. This way fixtures will work!
    """

    def __repr__(self):
        return json_encode(self, sort_keys=True)

    @property
    def base(self):
        return dict(self)


class JsonString(str):
    """
    Hack so repr() called by dumpdata will output JSON instead of Python formatted data. This way fixtures will work!
    """

    def __repr__(self):
        return json_encode(self, sort_keys=True)

    @property
    def base(self):
        return str(self)


class JsonList(list):
    """
    Hack so repr() called by dumpdata will output JSON instead of Python formatted data. This way fixtures will work!
    """

    def __repr__(self):
        return json_encode(self, sort_keys=True)

    @property
    def base(self):
        return list(self)


class JsonField(models.Field):
    """
    JsonField is a generic TextField that neatly serializes/unserializes JSON objects seamlessly.
    """
    empty_strings_allowed = False
    description = _("A JSON object")
    default_error_messages = {
        'invalid': _("Value must be a valid JSON")
    }
    _default_hint = ('dict', '{}')

    def __init__(self, *args, **kwargs):
        null = kwargs.get('null', False)
        default = kwargs.get('default', None)
        self.encoder = kwargs.get('encoder', None)
        if not null and default is None:
            kwargs['default'] = '{}'
        if isinstance(default, (list, dict)):
            kwargs['default'] = json_encode(default, cls=self.encoder, sort_keys=True)
        models.Field.__init__(self, *args, **kwargs)

    def db_type(self, connection):
        if is_postgresql(connection):
            return 'jsonb'
        return super().db_type(connection)

    def get_internal_type(self):
        return 'TextField'

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.default == '{}':
            del kwargs['default']
        if self.encoder is not None:
            kwargs['encoder'] = self.encoder
        return name, path, args, kwargs

    def get_transform(self, name):
        transform = super().get_transform(name)
        if transform:
            return transform
        return JsonKeyTransformFactory(name)

    def from_db_value(self, value, *args, **kwargs):
        return self.to_python(value)

    def to_python(self, value):
        """
        Convert our string value to JSON after we load it from the DB
        """
        if value is None or value == '':
            return {} if not self.null else None
        try:
            while isinstance(value, str):
                value = json_decode(value)
        except ValueError:
            pass
        if isinstance(value, dict):
            return JsonDict(**value)
        elif isinstance(value, str):
            return JsonString(value)
        elif isinstance(value, list):
            return JsonList(value)
        return value

    def get_db_prep_value(self, value, connection, prepared=False):
        """
        Convert our JSON object to a string before we save
        """
        if value is None and self.null:
            return None
        # default values come in as strings; only non-strings should be run through `dumps`
        try:
            while isinstance(value, str):
                value = json_decode(value)
        except ValueError:
            pass
        return json_encode(value, cls=self.encoder, sort_keys=True)

    def validate(self, value, model_instance):
        super().validate(value, model_instance)
        try:
            json_encode(value, cls=self.encoder)
        except TypeError:
            raise ValidationError(
                self.error_messages['invalid'],
                code='invalid',
                params={'value': value},
            )

    def value_from_object(self, obj):
        value = super().value_from_object(obj)
        return self.to_python(value)

    def value_to_string(self, obj):
        value = self.value_from_object(obj)
        return value or ''

    def formfield(self, **kwargs):
        from common.forms import JsonField
        defaults = {'form_class': JsonField}
        defaults.update(kwargs)
        return super().formfield(**defaults)


# Mommy monkey-patch for CustomDecimalField
try:
    from django.contrib.postgres.fields import JSONField
    from model_mommy.generators import default_mapping
    default_mapping[CustomDecimalField] = default_mapping.get(models.DecimalField)
    default_mapping[JsonField] = default_mapping.get(JSONField)
except ImportError:
    pass


class JsonKeyTransform(Transform):
    operator = '->'
    nested_operator = '#>'

    def __init__(self, key_name, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.key_name = key_name

    def as_sql(self, compiler, connection, **kwargs):
        key_transforms = [self.key_name]
        previous = self.lhs
        while isinstance(previous, JsonKeyTransform):
            key_transforms.insert(0, previous.key_name)
            previous = previous.lhs
        lhs, params = compiler.compile(previous)
        if len(key_transforms) > 1:
            return "(%s %s %%s)" % (lhs, self.nested_operator), [key_transforms] + params
        try:
            int(self.key_name)
        except ValueError:
            lookup = "'%s'" % self.key_name
        else:
            lookup = "%s" % self.key_name
        return "(%s %s %s)" % (lhs, self.operator, lookup), params


class JsonKeyTextTransform(JsonKeyTransform):
    """
    Transformation pour JsonField afin d'utiliser les lookups sur les éléments texte
    """
    operator = '->>'
    nested_operator = '#>>'
    _output_field = TextField()


class JsonKeyTransformTextLookupMixin(object):
    def __init__(self, key_transform, *args, **kwargs):
        assert isinstance(key_transform, JsonKeyTransform)
        key_text_transform = JsonKeyTextTransform(
            key_transform.key_name, *key_transform.source_expressions, **key_transform.extra)
        super(JsonKeyTransformTextLookupMixin, self).__init__(key_text_transform, *args, **kwargs)


@JsonKeyTransform.register_lookup
class JsonKeyTransformIExact(JsonKeyTransformTextLookupMixin, lookups.IExact):
    pass


@JsonKeyTransform.register_lookup
class JsonKeyTransformIContains(JsonKeyTransformTextLookupMixin, lookups.IContains):
    pass


@JsonKeyTransform.register_lookup
class JsonKeyTransformStartsWith(JsonKeyTransformTextLookupMixin, lookups.StartsWith):
    pass


@JsonKeyTransform.register_lookup
class JsonKeyTransformIStartsWith(JsonKeyTransformTextLookupMixin, lookups.IStartsWith):
    pass


@JsonKeyTransform.register_lookup
class JsonKeyTransformEndsWith(JsonKeyTransformTextLookupMixin, lookups.EndsWith):
    pass


@JsonKeyTransform.register_lookup
class JsonKeyTransformIEndsWith(JsonKeyTransformTextLookupMixin, lookups.IEndsWith):
    pass


@JsonKeyTransform.register_lookup
class JsonKeyTransformRegex(JsonKeyTransformTextLookupMixin, lookups.Regex):
    pass


@JsonKeyTransform.register_lookup
class JsonKeyTransformIRegex(JsonKeyTransformTextLookupMixin, lookups.IRegex):
    pass


class JsonKeyTransformFactory(object):

    def __init__(self, key_name):
        self.key_name = key_name

    def __call__(self, *args, **kwargs):
        return JsonKeyTransform(self.key_name, *args, **kwargs)


@JsonField.register_lookup
class JsonHas(Lookup):
    """
    Recherche un élément dans un champ JSON contenant un tableau de chaînes de caractères ou un dictionnaire
    Uniquement pour PostgreSQL
    """
    lookup_name = 'has'

    def as_sql(self, compiler, connection):
        if is_postgresql(connection):
            lhs, lhs_params = self.process_lhs(compiler, connection)
            rhs, rhs_params = self.process_rhs(compiler, connection)
            assert len(rhs_params) == 1, _("A string must be provided as argument")
            # assert all(isinstance(e, str) for e in rhs_params), _("Argument must be of type string")
            params = lhs_params + rhs_params
            return '%s ? %s' % (lhs, rhs), params
        raise NotImplementedError(
            _("The lookup '{lookup}' is only supported in PostgreSQL").format(
                lookup=self.lookup_name))


class JsonArrayLookup(Lookup):
    """
    Lookup standard pour la recherche multiple dans des tableaux de chaînes de caractères
    Uniquement pour PostgreSQL
    """

    def as_sql(self, compiler, connection):
        if is_postgresql(connection):
            lhs, lhs_params = self.process_lhs(compiler, connection)
            rhs, rhs_params = self.process_rhs(compiler, connection)
            assert len(rhs_params) == 1, _("A list of strings must be provided as argument")
            value, *junk = rhs_params
            rhs = ','.join(['%s'] * len(value))
            # assert isinstance(value, list), _("Lookup argument must be a list of strings")
            return '%s %s array[%s]' % (lhs, self.lookup_operator, rhs), value
        raise NotImplementedError(
            _("The lookup '{lookup}' is only supported in PostgreSQL").format(
                lookup=self.lookup_name))


@JsonField.register_lookup
class JsonInAny(JsonArrayLookup):
    """
    Recherche les éléments dans au moins une valeur est présente dans la liste fournie en paramètre
    Uniquement pour PostgreSQL
    """
    lookup_name = 'any'
    lookup_operator = '?|'


@JsonField.register_lookup
class JsonInAll(JsonArrayLookup):
    """
    Recherche les éléments dans toutes les valeurs sont présentes dans la liste fournie en paramètre
    Uniquement pour PostgreSQL
    """
    lookup_name = 'all'
    lookup_operator = '?&'


@JsonField.register_lookup
class JsonOverlap(JsonArrayLookup):
    """
    Recherche les éléments dans au moins une valeur est présente dans la liste fournie en paramètre
    Uniquement pour PostgreSQL
    """
    lookup_name = 'overlap'
    lookup_operator = '&&'


class JsonDictLookup(Lookup):
    """
    Lookup standard pour la recherche multiple dans des dictionnaires
    Uniquement pour PostgreSQL
    """

    def as_sql(self, compiler, connection):
        if is_postgresql(connection):
            lhs, lhs_params = self.process_lhs(compiler, connection)
            rhs, rhs_params = self.process_rhs(compiler, connection)
            assert len(rhs_params) == 1, _("A dictionary must be provided as argument")
            value, *junk = rhs_params
            # assert isinstance(value, dict), _("Lookup argument must be a dictionary")
            return '%s %s %s::jsonb' % (lhs, self.lookup_operator, rhs), [json_encode(value)]
        raise NotImplementedError(
            _("The lookup '{lookup}' is only supported in PostgreSQL").format(
                lookup=self.lookup_name))


@JsonField.register_lookup
class JsonContains(JsonDictLookup):
    """
    Recherche les éléments qui contiennent le dictionnaire fourni en paramètre
    Uniquement pour PostgreSQL
    """
    lookup_name = 'contains'
    lookup_operator = '@>'


@JsonField.register_lookup
class JsonContained(JsonDictLookup):
    """
    Recherche les éléments qui sont contenus dans le dictionnaire fourni en paramètre
    Uniquement pour PostgreSQL
    """
    lookup_name = 'contained'
    lookup_operator = '<@'


@JsonField.register_lookup
class JsonEmpty(Lookup):
    """
    Recherche les éléments dont la valeur est considérée comme vide ou nulle
    Uniquement pour PostgreSQL
    """
    lookup_name = 'isempty'
    empty_values = ['{}', '[]', '', 'null', None]

    def as_sql(self, compiler, connection):
        if is_postgresql(connection):
            lhs, lhs_params = self.process_lhs(compiler, connection)
            rhs, rhs_params = self.process_rhs(compiler, connection)
            assert len(rhs_params) == 1, _("A boolean must be provided as argument")
            value, *junk = rhs_params
            assert isinstance(value, bool), _("Lookup argument must be a boolean")
            rhs = ','.join(['%s'] * len(self.empty_values))
            if value:
                return '%s IS NULL OR %s::text IN (%s)' % (lhs, lhs, rhs), self.empty_values
            return '%s IS NOT NULL AND %s::text NOT IN (%s)' % (lhs, lhs, rhs), self.empty_values
        raise NotImplementedError(
            _("The lookup '{lookup}' is only supported in PostgreSQL").format(
                lookup=self.lookup_name))


@CharField.register_lookup
@TextField.register_lookup
class CustomUnaccent(Unaccent):
    has_unaccent = None
    lookup_name = 'unaccent'

    def as_sql(self, compiler, connection, **kwargs):
        if CustomUnaccent.has_unaccent is None:
            cursor = connection.cursor()
            cursor.execute("SELECT COUNT(proname) FROM pg_proc WHERE proname = 'f_unaccent';")
            response = cursor.fetchone()
            CustomUnaccent.has_unaccent = response and response[0] > 0
        if CustomUnaccent.has_unaccent:
            CustomUnaccent.function = 'F_UNACCENT'
        return super().as_sql(compiler, connection, **kwargs)
