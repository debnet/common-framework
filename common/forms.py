# coding: utf-8
from django import forms
from django.forms.formsets import BaseFormSet, formset_factory
from django.forms.models import _get_foreign_key as get_foreign_key
from django.forms.models import BaseInlineFormSet, BaseModelFormSet, ModelForm, modelformset_factory
from django.utils.translation import ugettext_lazy as _

from common.models import Entity, PerishableEntity
from common.utils import json_decode, json_encode


class CommonForm(object):
    _ignore_log = False
    _current_user = None
    _reason = None
    _from_admin = False
    _restore = False
    _force_default = False

    def __init__(self, *args, **kwargs):
        self.inlines = []
        super().__init__(*args, **kwargs)

    def init_inlines(self, data=None, files=None, context=None, **kwargs):
        if not hasattr(self, '_inlines'):
            return
        for formset in self:
            formset.inlines = []
            for inline in self._inlines:
                inline = inline(
                    data,
                    files,
                    instance=formset.instance,
                    prefix=formset.prefix,
                    context=context,
                    **kwargs)
                formset.inlines.append(inline)
                self.inlines.append(inline)


class CommonBaseFormSet(CommonForm):

    def _construct_form(self, i, **kwargs):
        if self.context:
            kwargs.update(self.context)
        return super()._construct_form(i, **kwargs)


class CommonFormSet(BaseFormSet, CommonBaseFormSet):

    def __init__(self, data=None, files=None, context=None, *args, **kwargs):
        self.context = context
        super().__init__(data, files, *args, **kwargs)


class CommonModelForm(CommonForm, ModelForm):

    def __init__(self, data=None, files=None, context=None, inline_context=None, inline_kwargs=None, *args, **kwargs):
        inline_kwargs = inline_kwargs or {}
        self.context = context
        super().__init__(data, files, *args, **kwargs)
        self.init_inlines(data, files, inline_context, **inline_kwargs)

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
        return super().save(commit=commit)

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

    def save(self, commit=True, _ignore_log=None, _current_user=None, _reason=None, _force_default=None):
        """
        Surcharge de la méthode de sauvegarde pour passer les paramètres spécifiques aux entités
        :param _ignore_log: Ignorer l'historique de modification ?
        :param _current_user: Utilisateur à l'origine de la modification
        :param _reason: Raison de la modification
        :param _force_default: Force la sauvegarde en place ?
        """
        self._ignore_log = _ignore_log or self._ignore_log
        self._current_user = _current_user or self._current_user
        self._reason = _reason or self._reason
        self._force_default = _force_default or self._force_default
        for form in self.forms:
            instance = form.instance
            instance._from_admin = self._from_admin
            instance._ignore_log = self._ignore_log
            instance._current_user = self._current_user
            instance._reason = self._reason
            instance._force_default = self._force_default or self._from_admin
            if hasattr(self, 'fk'):
                pk_value = getattr(self.instance, self.fk.remote_field.field_name)
                setattr(instance, self.fk.get_attname(), getattr(pk_value, 'pk', pk_value))
        super().save(commit=commit)
        for inline in self.inlines:
            inline.save(commit=commit, _ignore_log=self._ignore_log, _current_user=self._current_user,
                        _reason=self._reason, _force_default=self._force_default)

    def clean(self):
        for inline in self.inlines:
            inline.clean()
        return super().clean()


class CommonModelFormSet(CommonBaseModelFormSet, BaseModelFormSet):

    def __init__(self, data=None, files=None, context=None, inline_context=None, inline_kwargs=None, *args, **kwargs):
        inline_kwargs = inline_kwargs or {}
        self.context = context
        super().__init__(data, files, *args, **kwargs)
        self.init_inlines(data, files, inline_context, **inline_kwargs)

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

    def __init__(self, data=None, files=None, context=None, *args, **kwargs):
        self.context = context
        super().__init__(data, files, *args, **kwargs)
        if issubclass(self.model, PerishableEntity) and not self._from_admin:
            self.queryset = self.queryset.select_valid()


def get_formset(form, formset=CommonFormSet, **kwargs):
    return formset_factory(form, formset=formset, **kwargs)


def get_model_formset(model, form=CommonModelForm, formset=CommonModelFormSet, **kwargs):
    return modelformset_factory(model, form=form, formset=formset, **kwargs)


def get_inline_formset(base_model=None, base_form=None,
                       inline_models=None, inline_forms=None, inline_kwargs=None, formset=True, **kwargs):
    inline_models = inline_models if inline_models is list else [inline_models]
    inline_forms = inline_forms if inline_forms is list else [inline_forms]
    inline_kwargs = inline_kwargs if inline_kwargs is list else [inline_kwargs]
    base_kwargs = {
        'formfield_callback': None,
        'extra': 1,
        'can_delete': True,
        'can_order': False,
        'fields': None,
        'exclude': None,
        'max_num': None,
        'widgets': None,
        'validate_max': False,
        'localized_fields': None,
        'labels': None,
        'help_texts': None,
        'error_messages': None,
    }
    inlines = []
    fks = []
    for inline_model, inline_form, inline_args in zip(inline_models, inline_forms, inline_kwargs):
        fk = get_foreign_key(base_model, inline_model)
        fks.append(fk)
        ikwargs = base_kwargs.copy()
        if fk.unique:
            ikwargs['max_num'] = 1
        if all(inline_kwargs):
            ikwargs.update(inline_args)
        inline = get_model_formset(inline_model, form=inline_form, formset=CommonInlineFormSet, **ikwargs)
        inline.fk = fk
        inlines.append(inline)
    if formset:
        formset = get_model_formset(base_model, form=base_form, **kwargs)
        formset._inlines = inlines
        return formset
    base_form._inlines = inlines
    return base_form


class JsonField(forms.CharField):
    """
    Champ de formulaire spécifique pour le JsonField
    """

    default_error_messages = {
        'invalid': _("'%(value)s' value must be valid JSON."),
    }

    def __init__(self, **kwargs):
        kwargs.setdefault('widget', forms.Textarea)
        super().__init__(**kwargs)

    def to_python(self, value):
        if value in self.empty_values:
            return None
        try:
            return json_decode(value)
        except ValueError:
            raise forms.ValidationError(
                self.error_messages['invalid'],
                code='invalid',
                params={'value': value},
            )

    def prepare_value(self, value):
        return json_encode(value, sort_keys=True)
