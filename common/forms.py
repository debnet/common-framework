# coding: utf-8
from django import forms
from django.forms.formsets import BaseFormSet, formset_factory
from django.forms.models import (
    BaseInlineFormSet,
    BaseModelFormSet,
    ModelForm,
    _get_foreign_key,
    modelform_factory,
    modelformset_factory,
)
from django.utils.translation import gettext_lazy as _

from common.models import Entity, PerishableEntity
from common.utils import get_current_user, json_decode, json_encode


class CommonForm(object):
    """
    Formulaire de base pour la gestion des inlines et des entités
    """

    _ignore_log = False
    _current_user = None
    _reason = None
    _from_admin = False
    _restore = False
    _force_default = False

    def __init__(self, *args, **kwargs):
        self.inlines = []
        super().__init__(*args, **kwargs)

    def construct_inlines(self, data=None, files=None, context=None, **kwargs):
        if not hasattr(self, "_inlines"):
            return
        self.inlines = []
        for inline in self._inlines:
            meta = getattr(getattr(inline, "model", None), "_meta", None)
            instance = getattr(self, "instance", None)
            prefix = getattr(inline, "prefix", getattr(meta, "model_name", None))
            inline = inline(data, files, instance=instance, prefix=prefix, context=context, **kwargs)
            self.inlines.append(inline)
        return self.inlines

    @property
    def media(self):
        media = super().media
        for inline in self.inlines:
            media += inline.media
        return media


class CommonBaseFormSet(CommonForm):
    """
    Sous-formulaire de base
    """

    def _construct_form(self, i, **kwargs):
        if self.context:
            kwargs.update(self.context)
        return super()._construct_form(i, **kwargs)

    def construct_inlines(self, data=None, files=None, context=None, **kwargs):
        for formset in self:
            formset.construct_inlines(data=data, files=files, context=context, **kwargs)


class CommonFormSet(CommonBaseFormSet, BaseFormSet):
    """
    Sous-formulaire de base non relié à un modèle
    """

    def __init__(self, data=None, files=None, context=None, *args, **kwargs):
        self.context = context
        super().__init__(data, files, *args, **kwargs)


class CommonModelForm(CommonForm, ModelForm):
    """
    Formulaire de base relié à un modèle
    """

    def __init__(self, data=None, files=None, context=None, inline_context=None, inline_kwargs=None, *args, **kwargs):
        inline_kwargs = inline_kwargs or {}
        self.context = context
        self.model = self._meta.model
        self.meta = self.model._meta
        super().__init__(data, files, *args, **kwargs)
        self.construct_inlines(data, files, inline_context, **inline_kwargs)

    def save(self, commit=True, _ignore_log=None, _current_user=None, _reason=None, _force_default=None):
        """
        Surcharge de la méthode de sauvegarde pour passer les paramètres spécifiques aux entités
        :param _ignore_log: Ignorer l'historique de modification ?
        :param _current_user: Utilisateur à l'origine de la modification
        :param _reason: Raison de la modification
        :param _force_default: Force la sauvegarde en place ?
        """
        if isinstance(self.instance, Entity):
            self.instance._ignore_log = _ignore_log or self.instance._ignore_log
            self.instance._current_user = _current_user or self.instance._current_user
            self.instance._reason = _reason or self.instance._reason
            self.instance._force_default = _force_default or self.instance._force_default
        instance = super().save(commit=commit)
        for inline in self.inlines:
            inline.instance = instance
            inline.save(
                commit=commit,
                _ignore_log=self._ignore_log,
                _current_user=self._current_user,
                _reason=self._reason,
                _force_default=self._force_default,
            )
        return instance

    def is_valid(self):
        valid = super().is_valid()
        for inline in self.inlines:
            valid = valid and inline.is_valid()
            if not valid:
                return False
        return valid

    def error_count(self):
        count = len(super().errors)
        for inline in self.inlines:
            count += inline.total_error_count()
        return count


class CommonBaseModelFormSet(CommonBaseFormSet):
    """
    Sous-formulaire de base relié à un modèle
    """

    def save(self, commit=True, _ignore_log=None, _current_user=None, _reason=None, _force_default=None):
        """
        Surcharge de la méthode de sauvegarde pour passer les paramètres spécifiques aux entités
        :param _ignore_log: Ignorer l'historique de modification ?
        :param _current_user: Utilisateur à l'origine de la modification
        :param _reason: Raison de la modification
        :param _force_default: Force la sauvegarde en place ?
        """
        self._ignore_log = _ignore_log or self._ignore_log
        self._current_user = _current_user or self._current_user or get_current_user()
        self._reason = _reason or self._reason
        self._force_default = _force_default or self._force_default
        for form in self.forms:
            instance = form.instance
            instance._from_admin = self._from_admin
            instance._ignore_log = self._ignore_log
            instance._current_user = self._current_user
            instance._reason = self._reason
            instance._force_default = self._force_default or self._from_admin
            if hasattr(self, "fk"):
                pk_value = getattr(self.instance, self.fk.remote_field.field_name)
                setattr(instance, self.fk.get_attname(), getattr(pk_value, "pk", pk_value))
        instance = super().save(commit=commit)
        for inline in self.inlines:
            inline.instance = instance
            inline.save(
                commit=commit,
                _ignore_log=self._ignore_log,
                _current_user=self._current_user,
                _reason=self._reason,
                _force_default=self._force_default,
            )
        return instance

    def clean(self):
        for inline in self.inlines:
            inline.clean()
        return super().clean()


class CommonModelFormSet(CommonBaseModelFormSet, BaseModelFormSet):
    """
    Formulaire générique relié à un modèle
    """

    def __init__(self, data=None, files=None, context=None, inline_context=None, inline_kwargs=None, *args, **kwargs):
        inline_kwargs = inline_kwargs or {}
        self.context = context
        self.meta = self.model._meta
        super().__init__(data, files, *args, **kwargs)
        self.construct_inlines(data, files, inline_context, **inline_kwargs)

    def is_valid(self):
        valid = super().is_valid()
        for form in self.forms:
            for inline in form.inlines:
                valid = valid and inline.is_valid()
                if not valid:
                    return False
        return valid

    def error_count(self):
        count = super().total_error_count()
        for form in self.forms:
            for inline in form.inlines:
                count += inline.total_error_count()
        return count


class CommonInlineFormSet(CommonBaseModelFormSet, BaseInlineFormSet):
    """
    Sous-formulaire générique relié à un modèle
    """

    def __init__(self, data=None, files=None, context=None, *args, **kwargs):
        self.context = context
        self.meta = self.model._meta
        super().__init__(data, files, *args, **kwargs)
        if issubclass(self.model, PerishableEntity) and not self._from_admin:
            self.queryset = self.queryset.select_valid()


def get_formset(form, formset=None, **kwargs):
    """
    Raccourci de `formset_factory` avec la surcharge des formulaires
    """
    return formset_factory(form, formset=formset or CommonFormSet, **kwargs)


def get_model_formset(model, form=None, formset=None, **kwargs):
    """
    Raccourci de `modelformset_factory` avec la surcharge des formulaires
    """
    return modelformset_factory(model, form=form or CommonModelForm, formset=formset or CommonModelFormSet, **kwargs)


def get_model_form(
    base_model=None,
    base_form=None,
    inline_models=None,
    inline_forms=None,
    inline_options=None,
    common_options=None,
    formset=False,
    **kwargs
):
    """
    Permet de construire d'un coup un formulaire de modèle et ses sous-formulaires éventuels
    Les listes `inline_models`, `inline_forms` et `inline_kwargs` doivent être de même taille et dans le même ordre
    :param base_model: Modèle de base (obligatoire)
    :param base_form: Formulaire pour le modèle de base (facultatif)
    :param inline_models: Sous-modèles à relier (obligatoire)
    :param inline_forms: Sous-formulaires pour chaque sous-modèle (facultatif)
    :param inline_options: Paramètres de chaque sous-formulaire (facultatif)
    :param formset: En faire un ensemble de formulaires ?
    :param kwargs: Paramètres optionnels utilisés uniquement pour le formulaire principal
    :return: Formulaire principal et ses sous-formulaires
    """
    from itertools import zip_longest

    common_options = common_options or {}
    inline_models, inline_forms, inline_options = inline_models or [], inline_forms or [], inline_options or []
    inlines = []
    for model, form, options in zip_longest(inline_models, inline_forms, inline_options, fillvalue=None):
        if not model:
            continue
        options = options or {}
        options.update(common_options)
        fk = _get_foreign_key(base_model, model)
        if fk.unique:
            options["max_num"] = 1
        inline = get_model_formset(model, form=form, formset=CommonInlineFormSet, **options)
        inline.fk = fk
        inlines.append(inline)
    if formset:
        formset = get_model_formset(base_model, form=base_form, **kwargs)
        formset._inlines = inlines
        return formset
    elif not base_form:
        base_form = modelform_factory(base_model, form=CommonModelForm, **kwargs)
    base_form._inlines = inlines
    return base_form


class JsonField(forms.CharField):
    """
    Champ de formulaire spécifique pour le JsonField
    """

    default_error_messages = {
        "invalid": _("'%(value)s' value must be valid JSON."),
    }

    class InvalidInput(str):
        pass

    def __init__(self, encoder=None, decoder=None, **kwargs):
        self.encoder = encoder
        self.decoder = decoder
        kwargs.setdefault("widget", forms.Textarea)
        super().__init__(**kwargs)

    def to_python(self, value):
        if self.disabled:
            return value
        from common.fields import JsonString

        if value in self.empty_values:
            return None
        elif isinstance(value, (list, dict, int, float, JsonString)):
            return value
        try:
            converted = json_decode(value, cls=self.decoder)
        except ValueError:
            raise forms.ValidationError(
                self.error_messages["invalid"],
                code="invalid",
                params={"value": value},
            )
        if isinstance(converted, str):
            return JsonString(converted)
        else:
            return converted

    def bound_data(self, data, initial):
        if self.disabled:
            return initial
        try:
            return json_decode(data, cls=self.decoder)
        except ValueError:
            return JsonField.InvalidInput(data)

    def prepare_value(self, value):
        if isinstance(value, JsonField.InvalidInput):
            return value
        return json_encode(value, cls=self.encoder)


class BaseFilterForm(forms.Form):
    """
    Classe de base pour les filtres de formulaires
    Les lookups sous forme de fonction doivent déclarer les paramètres suivants dans cet ordre :
        valeur, liste de sous-filtres, dictionnaire de filtres, liste des sous-filtres à exécuter un par un
    et doivent retourner True si un distinct est nécessaire, False|None sinon
    """

    distinct = False
    count = 0
    _lookups, filled = {}, {}

    @property
    def filters(self):
        self.count = 0
        self.filled = {}
        distinct = self.distinct
        args, kwargs, largs = [], {}, []
        for key, value in self.cleaned_data.items():
            field = self[key]
            is_bool = isinstance(field.field, (forms.BooleanField, forms.NullBooleanField))
            if (not is_bool and not field.data) or (is_bool and field.data is None):
                continue
            if key not in self._lookups:
                continue
            lookup = self._lookups.get(key, None)
            if callable(lookup):
                distinct |= lookup(self, value, args, kwargs, largs) or False
            elif isinstance(lookup, tuple):
                _lookup, _distinct = lookup
                distinct |= _distinct
                kwargs[_lookup] = value
            elif lookup:
                kwargs[lookup] = value
            if value == "" or value == field.initial:
                continue
            self.count += 1
            self.filled[key] = value
        return args, kwargs, largs, distinct

    def apply(self, queryset):
        if not self.is_valid():
            return queryset.none()
        fargs, fkwargs, flargs, distinct = self.filters
        queryset = queryset.filter(*fargs, **fkwargs)
        for flarg in flargs:
            queryset = queryset.filter(flarg)
        if distinct:
            queryset = queryset.distinct()
        return queryset
