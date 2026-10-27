# coding: utf-8
"""Champs de modèle et lookups Common, avec branche JSON historique conservée.

Les compilateurs de lookups renvoient des couples (SQL, paramètres) pour le
moteur Django ; ils ne doivent pas être invoqués comme constructeurs de SQL
libre. Leur implémentation dépend des API internes des backends.
"""

import decimal
import pickle

from django import VERSION as django_version
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import CharField, Lookup, TextField, Transform, lookups
from django.db.models.fields import mixins
from django.utils.translation import gettext_lazy as _

from common.settings import settings
from common.utils import (
    base64_decode,
    base64_encode,
    json_decode,
    json_encode,
    str_to_bool,
)

is_postgresql = lambda connection: connection.vendor == "postgresql"  # noqa
is_oracle = lambda connection: connection.vendor == "oracle"  # noqa
is_mysql = lambda connection: connection.vendor == "mysql"  # noqa
is_sqlite = lambda connection: connection.vendor == "sqlite"  # noqa


# Champs scalaires : représentation décimale et données Python de confiance
# ------------------------------------------------------------------------


class CustomDecimalField(models.DecimalField):
    """
    Champ décimal spécifique pour éviter la représentation scientifique
    """

    def value_from_object(self, obj):
        """Normalise la valeur Decimal pour l'export, sans modifier l'instance ni la base."""
        value = super().value_from_object(obj)
        if isinstance(value, decimal.Decimal):
            return self._transform_decimal(value)
        return value

    def _transform_decimal(self, value):
        """Supprime l'exposant superflu dans un contexte borné par max_digits."""
        context = decimal.Context(prec=self.max_digits)
        return (
            value.quantize(decimal.Decimal(1), context=context)
            if value == value.to_integral()
            else value.normalize(context)
        )


class PickleField(models.BinaryField):
    """
    Champ binaire utilisant pickle pour sérialiser des données Python de confiance.

    pickle.loads peut exécuter du code : ne jamais accepter des payloads non
    fiables, même via un import/fixture ou une écriture directe dans la base.
    """

    def __init__(self, *args, **kwargs):
        """Prépare un default non nul en pickle avant l'initialisation BinaryField."""
        default = kwargs.get("default", None)
        if default is not None:
            kwargs["default"] = pickle.dumps(default)
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, *args, **kwargs):
        """Décode les données binaires de la base avec la même logique que to_python."""
        return self.to_python(value)

    def to_python(self, value):
        """Décode pickle/Base64 ou conserve une valeur Python déjà désérialisée.

        Seul None est nul : 0, False et les collections vides sont préservés.
        Le chemin historique essaie Base64 pour du texte puis le fallback
        BinaryField si le décodage pickle échoue ; ce n'est pas une validation
        de sûreté du pickle ni une isolation de son exécution.
        """
        if value is None:
            return None
        if isinstance(value, str) and value == "":
            return value
        if not isinstance(value, (str, bytes, bytearray, memoryview)):
            return value
        _value = value
        if isinstance(_value, str):
            _value = bytes(_value, encoding="utf-8")
            try:
                _value = base64_decode(_value)
            except Exception:
                pass
        try:
            return pickle.loads(_value)
        except Exception:
            return super().to_python(value)

    def get_prep_value(self, value):
        """Encode une valeur Python en pickle, sans réencoder les bytes fournis.

        None devient NULL si le champ l'autorise, sinon des bytes vides.
        Les autres valeurs falsy sont sérialisées comme les valeurs non vides.
        """
        if value is None:
            return None if self.null else b""
        if isinstance(value, bytes):
            return value
        return pickle.dumps(value)

    def value_from_object(self, obj):
        value = super().value_from_object(obj)
        return self.to_python(value)

    def value_to_string(self, obj):
        value = self.value_from_object(obj)
        return base64_encode(self.get_prep_value(value))


# JSON legacy, transformations de clés et compilateurs par moteur
# -------------------------------------------------------------

# Substitue le champ JSON du common par la version générique introduite par Django 3.1
if django_version < (3, 1) or settings.COMMON_JSONFIELD:

    class JsonDict(dict):
        """
        Hack so repr() called by dumpdata will output JSON instead of Python formatted data.
        This way fixtures will work!
        """

        def __repr__(self):
            return json_encode(self)

        @property
        def base(self):
            return dict(self)

    class JsonString(str):
        """
        Hack so repr() called by dumpdata will output JSON instead of Python formatted data.
        This way fixtures will work!
        """

        def __repr__(self):
            return json_encode(self)

        @property
        def base(self):
            return str(self)

    class JsonList(list):
        """
        Hack so repr() called by dumpdata will output JSON instead of Python formatted data.
        This way fixtures will work!
        """

        def __repr__(self):
            return json_encode(self)

        @property
        def base(self):
            return list(self)

    class JsonField(mixins.CheckFieldDefaultMixin, models.Field):
        """
        JsonField is a generic TextField that neatly serializes/unserializes JSON objects seamlessly.
        """

        is_django = False
        empty_strings_allowed = False
        description = _("A JSON object")
        default_error_messages = {"invalid": _("Value must be valid JSON")}
        _default_hint = ("dict", "{}")

        def __init__(self, encoder=None, decoder=None, *args, **kwargs):
            """Configure le stockage JSON texte/jsonb et les codecs facultatifs.

            Sans null ni default explicite, le fallback historique utilise {}.
            Préférer default=dict lors des déclarations pour éviter un défaut mutable.
            """
            self.encoder = encoder
            self.decoder = decoder
            null = kwargs.get("null", False)
            default = kwargs.get("default", None)
            if not null and default is None:
                kwargs["default"] = {}
            if isinstance(default, (list, dict)):
                json_encode(default)
                kwargs["default"] = default
            super().__init__(*args, **kwargs)

        def deconstruct(self):
            """Restitue les codecs explicites dans la description du champ pour les migrations."""
            name, path, args, kwargs = super().deconstruct()
            if self.default == {}:
                del kwargs["default"]
            if self.encoder is not None:
                kwargs["encoder"] = self.encoder
            if self.decoder is not None:
                kwargs["decoder"] = self.decoder
            return name, path, args, kwargs

        def db_type(self, connection):
            if is_postgresql(connection):
                return "jsonb"
            return super().db_type(connection)

        def get_internal_type(self):
            return "TextField"

        def get_transform(self, name):
            """Résout un transform enregistré ou traite name comme une clé JSON dynamique."""
            transform = super().get_transform(name)
            if transform:
                return transform
            return JsonKeyTransformFactory(name)

        def from_db_value(self, value, *args, **kwargs):
            return self.to_python(value)

        def to_python(self, value):
            """Décode les chaînes JSON imbriquées puis enveloppe dict/list/str.

            None ou une chaîne vide donne {} ou None suivant null. Le décodage
            répété est un comportement legacy ; ne pas supposer une préservation
            stricte de toutes les chaînes JSON scalaires comme en mode natif.
            """
            if value is None or value == "":
                return {} if not self.null else None
            try:
                while isinstance(value, str):
                    value = json_decode(value, cls=self.decoder)
            except ValueError:
                pass
            if isinstance(value, dict):
                return JsonDict(**value)
            elif isinstance(value, str):
                return JsonString(value)
            elif isinstance(value, list):
                return JsonList(value)
            return value

        def get_prep_value(self, value):
            if value is not None:
                return json_encode(value, cls=self.encoder)
            return value

        def validate(self, value, model_instance):
            """Valide les contraintes du champ puis la sérialisabilité JSON de la valeur."""
            super().validate(value, model_instance)
            try:
                json_encode(value, cls=self.encoder)
            except TypeError:
                raise ValidationError(
                    self.error_messages["invalid"],
                    code="invalid",
                    params={"value": value},
                )

        def value_from_object(self, obj):
            value = super().value_from_object(obj)
            return self.to_python(value)

        def value_to_string(self, obj):
            return self.value_from_object(obj) or ""

        def formfield(self, **kwargs):
            """Construit le champ de formulaire Common avec les mêmes codecs que le modèle."""
            from common.forms import JsonField

            return super().formfield(
                **{
                    "form_class": JsonField,
                    "encoder": self.encoder,
                    "decoder": self.decoder,
                    **kwargs,
                }
            )

    class JsonGenericHasKey(Lookup):
        lookup_name = None
        lookup_operator = None
        logical_operator = None

        def as_sql(self, compiler, connection, template=None):
            """Compile les chemins JSON des clés recherchées pour les moteurs compatibles.

            Combine plusieurs clés avec logical_operator et renvoie leurs
            chemins comme paramètres. Les variantes vendor adaptent la syntaxe.
            """
            # Process JSON path from the left-hand side.
            if isinstance(self.lhs, JsonKeyTransform):
                lhs, lhs_params, lhs_key_transforms = self.lhs.preprocess_lhs(
                    compiler, connection
                )
                lhs_json_path = compile_json_path(lhs_key_transforms)
            else:
                lhs, lhs_params = self.process_lhs(compiler, connection)
                lhs_json_path = "$"
            sql = template % lhs
            # Process JSON path from the right-hand side.
            rhs = self.rhs
            rhs_params = []
            if not isinstance(rhs, (list, tuple)):
                rhs = [rhs]
            for key in rhs:
                if isinstance(key, JsonKeyTransform):
                    *_, rhs_key_transforms = key.preprocess_lhs(compiler, connection)
                else:
                    rhs_key_transforms = [key]
                rhs_params.append(
                    "%s%s"
                    % (
                        lhs_json_path,
                        compile_json_path(rhs_key_transforms, include_root=False),
                    )
                )
            # Add condition for each key.
            if self.logical_operator:
                sql = "(%s)" % self.logical_operator.join([sql] * len(rhs_params))
            return sql, tuple(lhs_params) + tuple(rhs_params)

        def as_mysql(self, compiler, connection):
            return self.as_sql(
                compiler, connection, template="JSON_CONTAINS_PATH(%s, 'one', %%s)"
            )

        def as_oracle(self, compiler, connection):
            """Adapte JSON_EXISTS au chemin littéral attendu par le backend Oracle legacy."""
            sql, params = self.as_sql(
                compiler, connection, template="JSON_EXISTS(%s, '%%s')"
            )
            return sql % tuple(params), []

        def as_postgresql(self, compiler, connection):
            """Compile l'opérateur de présence de clé PostgreSQL avec ses paramètres.

            Une clé exprimée par un transform imbriqué est réaffectée au côté
            gauche afin de conserver l'opérateur attendu sur le dernier segment.
            """
            if isinstance(self.rhs, JsonKeyTransform):
                *_, rhs_key_transforms = self.rhs.preprocess_lhs(compiler, connection)
                for key in rhs_key_transforms[:-1]:
                    self.lhs = JsonKeyTransform(key, self.lhs)
                self.rhs = rhs_key_transforms[-1]
            lhs, lhs_params = self.process_lhs(compiler, connection)
            rhs, rhs_params = self.process_rhs(compiler, connection)
            params = tuple(lhs_params) + tuple(rhs_params)
            return "%s %s %s" % (lhs, self.lookup_operator, rhs), params

        def as_sqlite(self, compiler, connection):
            return self.as_sql(
                compiler, connection, template="JSON_TYPE(%s, %%s) IS NOT NULL"
            )

    @JsonField.register_lookup
    class JsonHasKey(JsonGenericHasKey):
        """
        Recherche un élément dans un champ JSON contenant un tableau de chaînes de caractères ou un dictionnaire
        """

        lookup_name = "has"
        lookup_operator = "?"
        prepare_rhs = False

    JsonField.register_lookup(JsonHasKey, lookup_name="has_key")

    class JsonArrayLookup(JsonGenericHasKey):
        """
        Lookup standard pour la recherche multiple dans des tableaux de chaînes de caractères
        """

        lookup_name = None
        lookup_operator = None

        def as_postgresql(self, compiler, connection):
            """Compile les opérateurs de tableaux JSON avec un array de paramètres.

            Utilisé par hasall/hasany/overlap ; le RHS doit être une collection
            de valeurs préparées, pas une chaîne SQL à concaténer.
            """
            if isinstance(self.rhs, JsonKeyTransform):
                *_, rhs_key_transforms = self.rhs.preprocess_lhs(compiler, connection)
                for key in rhs_key_transforms[:-1]:
                    self.lhs = JsonKeyTransform(key, self.lhs)
                self.rhs = rhs_key_transforms[-1]
            lhs, lhs_params = self.process_lhs(compiler, connection)
            rhs, rhs_params = self.process_rhs(compiler, connection)
            value, *_ = rhs_params
            rhs = ",".join(["%s"] * len(value))
            return "%s %s array[%s]" % (lhs, self.lookup_operator, rhs), value

        def get_prep_lookup(self):
            return self.rhs

    @JsonField.register_lookup
    class JsonHasAll(JsonArrayLookup):
        """
        Recherche les éléments dont toutes les valeurs sont présentes dans la liste fournie en paramètre
        """

        lookup_name = "hasall"
        lookup_operator = "?&"
        logical_operator = " AND "

    JsonField.register_lookup(JsonHasAll, lookup_name="has_keys")

    @JsonField.register_lookup
    class JsonHasAny(JsonArrayLookup):
        """
        Recherche les éléments dont au moins une valeur est présente dans la liste fournie en paramètre
        """

        lookup_name = "hasany"
        lookup_operator = "?|"
        logical_operator = " OR "

    JsonField.register_lookup(JsonHasAny, lookup_name="has_any_keys")

    @JsonField.register_lookup
    class JsonOverlap(JsonArrayLookup):
        """
        Recherche les éléments dont au moins une valeur est commune entre les deux listes
        """

        lookup_name = "overlap"
        lookup_operator = "&&"
        logical_operator = " AND "

    class JsonDictLookup(Lookup):
        """
        Lookup standard pour la recherche multiple dans des dictionnaires
        Uniquement pour PostgreSQL
        """

        lookup_name = None
        lookup_operator = None

        def as_postgresql(self, compiler, connection):
            """Compare le document JSON au paramètre RHS converti en jsonb."""
            lhs, lhs_params = self.process_lhs(compiler, connection)
            rhs, rhs_params = self.process_rhs(compiler, connection)
            value, *_ = rhs_params
            return "%s %s %s::jsonb" % (lhs, self.lookup_operator, rhs), (value,)

    @JsonField.register_lookup
    class JsonContains(JsonDictLookup):
        """
        Recherche les éléments qui contiennent le dictionnaire fourni en paramètre
        """

        lookup_name = "hasdict"
        lookup_operator = "@>"

        def as_sql(self, compiler, connection):
            """Compile JSON_CONTAINS(document, valeur) pour la variante générique legacy."""
            lhs, lhs_params = self.process_lhs(compiler, connection)
            rhs, rhs_params = self.process_rhs(compiler, connection)
            params = tuple(lhs_params) + tuple(rhs_params)
            return "JSON_CONTAINS(%s, %s)" % (lhs, rhs), params

    JsonField.register_lookup(JsonContains, lookup_name="contains")

    @JsonField.register_lookup
    class JsonContained(JsonDictLookup):
        """
        Recherche les éléments qui sont contenus dans le dictionnaire fourni en paramètre
        """

        lookup_name = "indict"
        lookup_operator = "<@"

        def as_sql(self, compiler, connection):
            """Inverse les opérandes de JSON_CONTAINS pour le test contenu-par."""
            lhs, lhs_params = self.process_lhs(compiler, connection)
            rhs, rhs_params = self.process_rhs(compiler, connection)
            params = tuple(rhs_params) + tuple(lhs_params)
            return "JSON_CONTAINS(%s, %s)" % (rhs, lhs), params

    JsonField.register_lookup(JsonContained, lookup_name="contained_by")

    @JsonField.register_lookup
    class JsonExact(lookups.Exact):
        can_use_none_as_rhs = True

        def process_lhs(self, compiler, connection, **extra):
            """Distingue sous SQLite le JSON null du NULL SQL en comparant JSON_TYPE."""
            lhs, lhs_params = super().process_lhs(compiler, connection)
            if is_sqlite(connection):
                rhs, rhs_params = super().process_rhs(compiler, connection)
                if rhs == "%s" and rhs_params == [None]:
                    lhs = "JSON_TYPE(%s, '$')" % lhs
            return lhs, lhs_params

        def process_rhs(self, compiler, connection, **extra):
            """Prépare JSON null et adapte l'extraction de paramètres JSON sous MySQL."""
            rhs, rhs_params = super().process_rhs(compiler, connection)
            if rhs == "%s" and rhs_params == [None]:
                rhs_params = ["null"]
            if is_mysql(connection):
                func = ["JSON_EXTRACT(%s, '$')"] * len(rhs_params)
                rhs = rhs % tuple(func)
            return rhs, rhs_params

    def compile_json_path(key_transforms, include_root=True):
        """Construit un chemin JSON : clés citées/échappées et indices numériques.

        include_root contrôle le préfixe '$', pour pouvoir concaténer un suffixe.
        Le résultat est une valeur de chemin, pas une expression SQL autonome.
        """
        path = ["$"] if include_root else []
        for key_transform in key_transforms:
            try:
                num = int(key_transform)
            except ValueError:  # non-integer
                path.append(".")
                path.append(json_encode(key_transform))
            else:
                path.append("[%s]" % num)
        return "".join(path)

    class JsonKeyTransform(Transform):
        """
        Transformation générale pour JsonField
        """

        operator = "->"
        nested_operator = "#>"

        def __init__(self, key_name, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.key_name = key_name

        def preprocess_lhs(self, compiler, connection, lhs_only=False):
            """Compile la colonne source et collecte les clés de transforms imbriqués.

            Renvoie (SQL, paramètres, clés), ou seulement (SQL, paramètres) si
            lhs_only. Les clés Oracle ont leurs pourcents doublés pour la phase
            d'interpolation propre à ce compilateur historique.
            """
            if not lhs_only:
                key_transforms = [self.key_name]
            previous = self.lhs
            while isinstance(previous, JsonKeyTransform):
                if not lhs_only:
                    key_transforms.insert(0, previous.key_name)
                previous = previous.lhs
            lhs, params = compiler.compile(previous)
            if is_oracle(connection):
                key_transforms = [key.replace("%", "%%") for key in key_transforms]
            return (lhs, params, key_transforms) if not lhs_only else (lhs, params)

        def as_mysql(self, compiler, connection, **extra):
            """Compile JSON_EXTRACT avec le chemin en paramètre du backend MySQL."""
            lhs, params, key_transforms = self.preprocess_lhs(compiler, connection)
            json_path = compile_json_path(key_transforms)
            return "JSON_EXTRACT(%s, %%s)" % lhs, tuple(params) + (json_path,)

        def as_oracle(self, compiler, connection, **extra):
            """Combine JSON_QUERY et JSON_VALUE pour les valeurs JSON Oracle legacy."""
            lhs, params, key_transforms = self.preprocess_lhs(compiler, connection)
            json_path = compile_json_path(key_transforms)
            return (
                "COALESCE(JSON_QUERY(%s, '%s'), JSON_VALUE(%s, '%s'))"
                % ((lhs, json_path) * 2)
            ), tuple(params) * 2

        def as_postgresql(self, compiler, connection, **extra):
            """Utilise -> pour une clé et #> pour un chemin PostgreSQL imbriqué."""
            lhs, params, key_transforms = self.preprocess_lhs(compiler, connection)
            if len(key_transforms) > 1:
                return "(%s %s %%s)" % (lhs, self.nested_operator), params + [
                    key_transforms
                ]
            try:
                lookup = int(self.key_name)
            except ValueError:
                lookup = self.key_name
            return "(%s %s %%s)" % (lhs, self.operator), tuple(params) + (lookup,)

        def as_sqlite(self, compiler, connection, **extra):
            """Compile JSON_EXTRACT sur SQLite sans interpoler le chemin dans le SQL."""
            lhs, params, key_transforms = self.preprocess_lhs(compiler, connection)
            json_path = compile_json_path(key_transforms)
            return "JSON_EXTRACT(%s, %%s)" % lhs, tuple(params) + (json_path,)

    class JsonKeyTextTransform(JsonKeyTransform):
        """
        Transformation pour JsonField afin d'utiliser les lookups sur les éléments texte
        """

        operator = "->>"
        nested_operator = "#>>"
        output_field = TextField()

    class JsonKeyTransformTextLookupMixin(object):
        def __init__(self, key_transform, *args, **kwargs):
            """Convertit un JsonKeyTransform en extraction texte avant le lookup.

            Refuse les expressions d'un autre type avec TypeError : les
            opérateurs texte ont besoin d'un chemin JSON explicitement reconnu.
            """
            if not isinstance(key_transform, JsonKeyTransform):
                raise TypeError(
                    "Transform should be an instance of JsonKeyTransform in order to use this lookup."
                )
            key_text_transform = JsonKeyTextTransform(
                key_transform.key_name,
                *key_transform.source_expressions,
                **key_transform.extra,
            )
            super().__init__(key_text_transform, *args, **kwargs)

    class KeyTransformIsNull(lookups.IsNull):
        def as_oracle(self, compiler, connection):
            if not self.rhs:
                return JsonHasKey(self.lhs.lhs, self.lhs.key_name).as_oracle(
                    compiler, connection
                )
            return super().as_sql(compiler, connection)

        def as_sqlite(self, compiler, connection):
            if not self.rhs:
                return JsonHasKey(self.lhs.lhs, self.lhs.key_name).as_sqlite(
                    compiler, connection
                )
            return super().as_sql(compiler, connection)

    @JsonKeyTransform.register_lookup
    class JsonKeyTransformExact(JsonExact):
        def process_lhs(self, compiler, connection, **extra):
            """Conserve le chemin de clé lorsqu'une valeur JSON null est comparée sur SQLite."""
            lhs, lhs_params = super().process_lhs(compiler, connection)
            if is_sqlite(connection):
                rhs, rhs_params = super().process_rhs(compiler, connection)
                if rhs == "%s" and rhs_params == ["null"]:
                    lhs, _ = self.lhs.preprocess_lhs(
                        compiler, connection, lhs_only=True
                    )
                    lhs = "JSON_TYPE(%s, %%s)" % lhs
            return lhs, lhs_params

        def process_rhs(self, compiler, connection, **extra):
            """Prépare une égalité de clé JSON selon le backend et le type du RHS.

            Un autre transform reste une expression. Les scalaires/objets sont
            adaptés à JSON_VALUE/JSON_QUERY Oracle ou JSON_EXTRACT SQLite.
            """
            if isinstance(self.rhs, JsonKeyTransform):
                return super(lookups.Exact, self).process_rhs(compiler, connection)
            rhs, rhs_params = super().process_rhs(compiler, connection)
            if is_oracle(connection):
                func = []
                for value in rhs_params:
                    value = json_decode(value)
                    function = (
                        "JSON_QUERY"
                        if isinstance(value, (list, dict))
                        else "JSON_VALUE"
                    )
                    func.append(
                        "%s('%s', '$.value')"
                        % (
                            function,
                            json_encode({"value": value}),
                        )
                    )
                rhs = rhs % tuple(func)
                rhs_params = []
            elif is_sqlite(connection):
                func = [
                    "JSON_EXTRACT(%s, '$')" if value != "null" else "%s"
                    for value in rhs_params
                ]
                rhs = rhs % tuple(func)
            return rhs, rhs_params

        def as_oracle(self, compiler, connection):
            """Combine présence de clé et test de null pour distinguer une clé absente."""
            rhs, rhs_params = super().process_rhs(compiler, connection)
            if rhs_params == ["null"]:
                has_key_expr = JsonHasKey(self.lhs.lhs, self.lhs.key_name)
                has_key_sql, has_key_params = has_key_expr.as_oracle(
                    compiler, connection
                )
                is_null_expr = self.lhs.get_lookup("isnull")(self.lhs, True)
                is_null_sql, is_null_params = is_null_expr.as_sql(compiler, connection)
                return (
                    "%s AND %s" % (has_key_sql, is_null_sql),
                    tuple(has_key_params) + tuple(is_null_params),
                )
            return super().as_sql(compiler, connection)

    class CaseInsensitiveMixin:
        def process_lhs(self, compiler, connection):
            """Applique LOWER au côté gauche sur MySQL, qui compare ici du JSON binaire."""
            lhs, lhs_params = super().process_lhs(compiler, connection)
            if is_mysql(connection):
                return "LOWER(%s)" % lhs, lhs_params
            return lhs, lhs_params

        def process_rhs(self, compiler, connection):
            """Applique le même LOWER au côté droit sur MySQL pour une comparaison symétrique."""
            rhs, rhs_params = super().process_rhs(compiler, connection)
            if is_mysql(connection):
                return "LOWER(%s)" % rhs, rhs_params
            return rhs, rhs_params

    @JsonKeyTransform.register_lookup
    class JsonKeyTransformIExact(
        CaseInsensitiveMixin, JsonKeyTransformTextLookupMixin, lookups.IExact
    ):
        pass

    @JsonKeyTransform.register_lookup
    class JsonKeyTransformContains(JsonKeyTransformTextLookupMixin, lookups.Contains):
        pass

    @JsonKeyTransform.register_lookup
    class JsonKeyTransformIContains(
        CaseInsensitiveMixin, JsonKeyTransformTextLookupMixin, lookups.IContains
    ):
        pass

    @JsonKeyTransform.register_lookup
    class JsonKeyTransformStartsWith(
        JsonKeyTransformTextLookupMixin, lookups.StartsWith
    ):
        pass

    @JsonKeyTransform.register_lookup
    class JsonKeyTransformIStartsWith(
        CaseInsensitiveMixin, JsonKeyTransformTextLookupMixin, lookups.IStartsWith
    ):
        pass

    @JsonKeyTransform.register_lookup
    class JsonKeyTransformEndsWith(JsonKeyTransformTextLookupMixin, lookups.EndsWith):
        pass

    @JsonKeyTransform.register_lookup
    class JsonKeyTransformIEndsWith(
        CaseInsensitiveMixin, JsonKeyTransformTextLookupMixin, lookups.IEndsWith
    ):
        pass

    @JsonKeyTransform.register_lookup
    class JsonKeyTransformRegex(JsonKeyTransformTextLookupMixin, lookups.Regex):
        pass

    @JsonKeyTransform.register_lookup
    class JsonKeyTransformIRegex(
        CaseInsensitiveMixin, JsonKeyTransformTextLookupMixin, lookups.IRegex
    ):
        pass

    class JsonKeyTransformNumericLookupMixin:
        def process_rhs(self, compiler, connection):
            """Décode les paramètres numériques si le backend n'a pas de JSON natif."""
            rhs, rhs_params = super().process_rhs(compiler, connection)
            if not connection.features.has_native_json_field:
                rhs_params = [json_decode(value) for value in rhs_params]
            return rhs, rhs_params

    @JsonKeyTransform.register_lookup
    class KeyTransformLt(JsonKeyTransformNumericLookupMixin, lookups.LessThan):
        pass

    @JsonKeyTransform.register_lookup
    class KeyTransformLte(JsonKeyTransformNumericLookupMixin, lookups.LessThanOrEqual):
        pass

    @JsonKeyTransform.register_lookup
    class KeyTransformGt(JsonKeyTransformNumericLookupMixin, lookups.GreaterThan):
        pass

    @JsonKeyTransform.register_lookup
    class KeyTransformGte(
        JsonKeyTransformNumericLookupMixin, lookups.GreaterThanOrEqual
    ):
        pass

    class JsonKeyTransformFactory(object):
        def __init__(self, key_name):
            self.key_name = key_name

        def __call__(self, *args, **kwargs):
            return JsonKeyTransform(self.key_name, *args, **kwargs)


# Substitue le champ JSON du common par la version générique introduite par Django 3.1
else:
    from django.db.models.fields.json import (
        ContainedBy,
        DataContains,
        HasAnyKeys,
        HasKey,
        HasKeys,
        JSONField,
    )

    from common.utils import JsonDecoder, JsonEncoder

    class JsonField(JSONField):
        is_django = True

        def __init__(self, *args, **kwargs):
            """Ajoute les codecs Common par défaut sans remplacer une surcharge explicite.

            Le stockage, la validation et les migrations restent ceux du champ
            JSON natif de Django ; les aliases de lookups Common sont enregistrés
            sur cette sous-classe, pas sur tous les JSONField de Django.
            """
            if "encoder" not in kwargs:
                kwargs["encoder"] = JsonEncoder
            if "decoder" not in kwargs:
                kwargs["decoder"] = JsonDecoder
            super().__init__(*args, **kwargs)

    JsonField.register_lookup(HasKey, lookup_name="has")
    JsonField.register_lookup(HasKeys, lookup_name="hasall")
    JsonField.register_lookup(HasAnyKeys, lookup_name="hasany")
    JsonField.register_lookup(DataContains, lookup_name="hasdict")
    JsonField.register_lookup(ContainedBy, lookup_name="indict")


# Lookups partagés et intégrations optionnelles (Bakery / PostgreSQL)
# -----------------------------------------------------------------


@JsonField.register_lookup
class JsonEmpty(Lookup):
    """
    Recherche les éléments dont la valeur est considérée comme vide ou nulle
    """

    lookup_name = "isempty"
    empty_values = ["{}", "[]", "", "null"]

    def as_sql(self, compiler, connection):
        """Compare NULL et les représentations JSON vides, éventuellement en négatif.

        Le test repose sur empty_values et caste en texte sous PostgreSQL.
        Cette extension legacy ne correspond pas à une notion universelle de
        valeur vide identique entre tous les moteurs et tous les scalaires JSON.
        """
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        lhs_field = lhs % tuple(repr(lhs_param) for lhs_param in lhs_params)
        value, *_ = rhs_params
        rhs = ",".join(["%s"] * len(self.empty_values))
        cast = "::text" if is_postgresql(connection) else ""
        if str_to_bool(value):
            return (
                "(%s IS NULL OR %s%s IN (%s))" % (lhs_field, lhs_field, cast, rhs),
                self.empty_values,
            )
        return (
            "(%s IS NOT NULL AND %s%s NOT IN (%s))" % (lhs_field, lhs_field, cast, rhs),
            self.empty_values,
        )


# Bakery monkey-patch for CustomDecimalField and JsonField
try:
    from model_bakery.generators import default_mapping

    default_mapping[CustomDecimalField] = default_mapping.get(models.DecimalField)
    if django_version < (3, 1) or settings.COMMON_JSONFIELD:
        from django.contrib.postgres.fields import JSONField
    else:
        from django.db.models.fields.json import JSONField
    default_mapping[JsonField] = default_mapping.get(JSONField)
except ImportError:
    pass


try:
    from django.contrib.postgres.lookups import Unaccent

    @CharField.register_lookup
    @TextField.register_lookup
    class CustomUnaccent(Unaccent):
        has_unaccent = None
        lookup_name = "unaccent"

        def as_sql(self, compiler, connection, **kwargs):
            """Détecte f_unaccent une fois puis choisit cette fonction si disponible.

            La détection exécute une lecture du catalogue PostgreSQL pendant
            la compilation SQL. Son cache de classe n'est pas isolé par alias
            de base ; un déploiement multi-base doit garder cette limite en tête.
            """
            if CustomUnaccent.has_unaccent is None and is_postgresql(connection):
                cursor = connection.cursor()
                cursor.execute(
                    "SELECT COUNT(proname) FROM pg_proc WHERE proname = 'f_unaccent';"
                )
                response = cursor.fetchone()
                CustomUnaccent.has_unaccent = response and response[0] > 0
            if CustomUnaccent.has_unaccent:
                CustomUnaccent.function = "F_UNACCENT"
            return super().as_sql(compiler, connection, **kwargs)

except ImportError:
    pass
