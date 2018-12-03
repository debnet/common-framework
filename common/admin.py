# coding: utf-8
from django.contrib import admin, messages
from django.contrib.admin import options
from django.contrib.admin.actions import delete_selected as django_delete_selected
from django.contrib.admin.sites import all_sites
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html
from django.utils.text import camel_case_to_spaces, capfirst
from django.utils.translation import ugettext_lazy as _

from common.fields import JsonField, PickleField
from common.forms import CommonInlineFormSet
from common.models import (
    CommonModel, Entity, Global, GroupMetaData, History, HistoryField,
    MetaData, PerishableEntity, ServiceUsage, UserMetaData, Webhook)
from common.utils import get_pk_field


def delete_selected(modeladmin, request, queryset):
    """
    Action de suppression dans l'administration
    """
    queryset._from_admin = True
    queryset._current_user = request.user
    return django_delete_selected(modeladmin, request, queryset)


delete_selected.short_description = django_delete_selected.short_description


class CommonAdmin(admin.ModelAdmin):
    """
    Configuraton de l'administration par défaut
    """

    def get_model_perms(self, request):
        return {
            'add': self.has_add_permission(request),
            'change': self.has_change_permission(request),
            'delete': self.has_delete_permission(request),
            'view': self.has_view_permission(request),
        }

    def has_view_permission(self, request, obj=None):
        opts = self.opts
        code = '{}.view_{}'.format(opts.app_label, opts.model_name)
        return request.user.has_perm(code, obj=obj)

    def has_change_permission(self, request, obj=None):
        change_perm = super().has_change_permission(request, obj=obj)
        if change_perm:
            return change_perm
        view_perm = self.has_view_permission(request, obj=obj)
        if view_perm and obj:
            return change_perm
        self.list_editable = ()
        return view_perm

    def metadata_url(self, obj):
        count = obj.metadata.count()
        if count:
            type = ContentType.objects.get_for_model(obj)
            url = reverse('admin:common_metadata_changelist') + '?object_id={}&content_type={}'.format(obj.pk, type.pk)
            return format_html('<a href="{url}">{label}</a>', url=url, label=count)
        return count
    metadata_url.short_description = _("méta")

    def get_list_display(self, request):
        pk_field = get_pk_field(self.model).name
        list_display = (pk_field, ) + tuple(super().get_list_display(request))
        if not issubclass(self.model, CommonModel):
            return list_display
        return list_display + ('metadata_url', )

    def get_queryset(self, request):
        if not issubclass(self.model, CommonModel):
            return super().get_queryset(request)
        return super().get_queryset(request).prefetch_related('metadata')


class EntityAdmin(CommonAdmin):
    """
    Configuration de l'administration par défaut
    """
    actions = [delete_selected]
    ordering = ['-modification_date', ]
    date_hierarchy = 'creation_date'

    def save_model(self, request, obj, form, change):
        obj._from_admin = True
        obj._force_default = True
        obj.save(_current_user=request.user)

    def delete_model(self, request, obj):
        obj._from_admin = True
        obj._force_default = True
        obj.delete(_current_user=request.user)

    def get_list_filter(self, request):
        list_filter = list(super().get_list_filter(request))
        list_filter += ['creation_date'] if 'creation_date' not in list_filter else []
        list_filter += ['modification_date'] if 'modification_date' not in list_filter else []
        return tuple(list_filter)


class PerishableValidFilter(admin.SimpleListFilter):
    """
    Filtre spécifique aux entités périssables permettant d'isoler les enregistrements valides et invalides
    """
    title = _("validité")
    parameter_name = 'valid'

    def lookups(self, request, model_admin):
        return (
            ('1', _('Valides uniquement')),
            ('0', _('Non valides uniquement')),
        )

    def queryset(self, request, queryset):
        if self.value() == '1':
            return queryset.select_valid(valid=True)
        elif self.value() == '0':
            return queryset.select_valid(valid=False)
        return queryset


class PerishableEntityAdmin(EntityAdmin):
    """
    Configuration de l'administration des entités périssables par défaut
    """

    def save_model(self, request, obj, form, change):
        obj._force_default = True
        super().save_model(request, obj, form, change)

    def get_list_display(self, request):
        list_display = list(super().get_list_display(request))
        list_display += ['start_date'] if 'start_date' not in list_display else []
        list_display += ['end_date'] if 'end_date' not in list_display else []
        list_display += ['valid']
        return tuple(list_display)

    def get_list_filter(self, request):
        list_filter = list(super().get_list_filter(request))
        list_filter += ['start_date'] if 'start_date' not in list_filter else []
        list_filter += ['end_date'] if 'end_date' not in list_filter else []
        list_filter += [PerishableValidFilter]
        return tuple(list_filter)

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj=obj)
        dates_fields = ('start_date', 'end_date', )
        for title, fieldset in fieldsets:
            fields = fieldset.get('fields', ())
            for field in dates_fields:
                if field in fields:
                    fields.remove(field)
        fieldsets = list(fieldsets) + [
            (_("Dates d'effet"), {
                'fields': dates_fields
            }),
        ]
        return fieldsets


class EntityAdminInlineMixin(object):
    """
    Mixin pour la gestion des historiques dans les inlines de l'administration
    """
    formset = CommonInlineFormSet

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset._current_user = request.user
        formset._from_admin = True
        formset._force_default = True
        return formset

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj=obj)
        if not issubclass(self.model, PerishableEntity):
            return fieldsets
        dates_fields = ('start_date', 'end_date', )
        for title, fieldset in fieldsets:
            fields = fieldset.get('fields', ())
            for field in dates_fields:
                if field in fields:
                    fields.remove(field)
        fieldsets = list(fieldsets) + [
            (_("Dates d'effet"), {
                'fields': dates_fields
            }),
        ]
        return fieldsets


class EntityTabularInline(EntityAdminInlineMixin, options.TabularInline):
    """
    Surcharge du TabularInline pour la gestion des historiques
    """
    pass


class EntityStackedInline(EntityAdminInlineMixin, options.StackedInline):
    """
    Surcharge du StackedInline pour la gestion des historiques
    """
    pass


@admin.register(Global)
class GlobalAdmin(admin.ModelAdmin):
    """
    Configuration de l'administration pour les globales
    """
    readonly_fields = ('content_type', 'object_id', 'object_uid', )
    list_display = ('id', 'entity_url', 'content_type', 'object_id', 'object_uid', )
    list_display_links = ('id', )
    list_filter = ('content_type', )
    search_fields = ('object_uid', )

    def entity_url(self, obj):
        try:
            pattern = 'admin:{app_label}_{model}_change'.format(
                app_label=obj.content_type.app_label, model=obj.content_type.model)
            url = reverse(pattern, args=(obj.object_id, ))
            return format_html('<a href="{url}">{label}</a>', url=url, label=str(obj.entity))
        except Exception:
            return str(obj.entity or '')
    entity_url.admin_order_field = 'entity'
    entity_url.short_description = _("Entité")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('entity', 'content_type')


@admin.register(MetaData)
class MetaDataAdmin(admin.ModelAdmin):
    """
    Configuration de l'administration pour les métadonnées
    """
    date_hierarchy = 'modification_date'
    readonly_fields = ('object_id', 'content_type', )
    list_display = ('id', 'entity_url', 'content_type', 'key', 'value', 'creation_date', 'modification_date',
                    'deletion_date', )
    list_display_links = ('id', )
    list_filter = ('key', 'content_type', 'creation_date', 'modification_date', 'deletion_date', )
    list_select_related = True
    ordering = ('-modification_date', )
    search_fields = ('key', )

    def entity_url(self, obj):
        pattern = 'admin:{app_label}_{model}_change'.format(
            app_label=obj.content_type.app_label, model=obj.content_type.model)
        url = reverse(pattern, args=(obj.object_id, ))
        return format_html('<a href="{url}">{label}</a>', url=url, label=str(obj.entity))
    entity_url.short_description = _("Entité")
    entity_url.admin_order_field = 'entity'

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('entity')


def restore(modeladmin, request, queryset, rollback=False):
    """
    Action d'annulation des modifications
    :param modeladmin: Classe d'administration
    :param request: Requête HTTP
    :param queryset: Ensemble des entités sélectionnées
    :return: Rien
    """
    fail, success = 0, 0
    errors = []
    for history in queryset.order_by('creation_date'):
        try:
            result = history.restore(current_user=request.user, from_admin=True, rollback=rollback)
            if result:
                success += 1
            else:
                fail += 1
        except Exception as e:
            errors.append((history.pk, e))
    if success > 0:
        messages.success(request, _("{} élément(s) ont été restaurés avec succès !").format(success))
    if fail > 0:
        messages.warning(
            request,
            _("{} élément(s) n'ont pas pu être restaurés car leurs relations sont manquantes !").format(fail))
    for id, error in errors:
        messages.error(request, _("L'élément #{} n'a pu être restauré pour la raison suivante : {}").format(id, error))


restore.short_description = _("Annuler les modifications")


def rollback(modeladmin, request, queryset):
    """
    Action de reversion dans l'administration
    """
    return restore(modeladmin, request, queryset, rollback=True)


rollback.short_description = _("Restaurer les données d'origine")


@admin.register(History)
class HistoryAdmin(admin.ModelAdmin):
    """
    Configuration de l'administration pour les entrées d'historique
    """
    date_hierarchy = 'creation_date'
    readonly_fields = ('user', 'status', 'object_str', 'content_type', 'object_id', 'object_uid', 'admin', 'reason',
                       'data', )
    list_display = ('id', 'creation_date', 'user', 'status', 'entity_url', 'content_type', 'object_id', 'data_size',
                    'restoration_date', 'restored', 'admin', 'has_reason', 'fields_count', )
    list_display_links = ('id', )
    list_filter = ('creation_date', 'user', 'status', 'content_type', 'restoration_date', 'restored', 'admin', )
    list_select_related = True
    ordering = ('-creation_date', )
    search_fields = ('object_str', 'content_type', )
    actions = [restore, rollback]

    def entity_url(self, obj):
        if obj.status != History.DELETE:
            try:
                pattern = 'admin:{app_label}_{model}_change'.format(
                    app_label=obj.content_type.app_label, model=obj.content_type.model)
                url = reverse(pattern, args=(obj.object_id, ))
                return format_html('<a href="{url}">{label}</a>', url=url, label=obj.object_str)
            except Exception:
                pass
        return format_html(obj.object_str)
    entity_url.admin_order_field = 'entity_str'
    entity_url.short_description = _("Entité")

    def fields_count(self, obj):
        if obj.fields_count:
            pattern = 'admin:{app_label}_{model}_changelist'.format(
                app_label=HistoryField._meta.app_label, model=HistoryField._meta.model_name)
            url = reverse(pattern) + '?history={}'.format(obj.pk)
            return format_html('<a href="{url}">{label}</a>', url=url, label=obj.fields_count)
        return obj.fields_count
    fields_count.admin_order_field = 'fields_count'
    fields_count.short_description = _("Champs modifiés")

    def has_reason(self, obj):
        return bool(obj.reason)
    has_reason.boolean = True
    has_reason.admin_order_field = 'reason'
    has_reason.short_description = _("Motif")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('content_type', 'user').prefetch_related('entity')\
            .annotate(fields_count=Count('fields')).order_by('-creation_date')


@admin.register(HistoryField)
class HistoryFieldAdmin(admin.ModelAdmin):
    """
    Configuration de l'administration pour les historiques des modifications de champs
    """
    date_hierarchy = 'creation_date'
    readonly_fields = ('history', 'field_name', 'old_value', 'new_value', 'status_m2m', 'editable', 'data', )
    list_display = ('id', 'creation_date', 'history_url', 'field', 'editable', 'old_inner_value', 'new_inner_value',
                    'data_size', 'status_m2m', 'restoration_date', 'restored', )
    list_display_links = ('id', )
    list_filter = ('creation_date', 'history__user', 'history__content_type', 'editable', 'restoration_date',
                   'restored', 'status_m2m', )
    list_select_related = True
    ordering = ('-creation_date', )
    search_fields = ('field_name', 'history__object_str', 'history__content_type', )
    actions = [restore]

    def field(self, obj):
        try:
            label = getattr(obj.field, 'verbose_name', '') or capfirst(camel_case_to_spaces(obj.field_name))
            return format_html('<span title="{code}">{label}</span>', label=capfirst(label), code=obj.field_name)
        except Exception:
            return obj.field_name
    field.short_description = _("Champ")

    def old_inner_value(self, obj):
        return self._get_inner_value(obj.old_inner_value)
    old_inner_value.admin_order_field = 'old_value'
    old_inner_value.short_description = _("Ancienne valeur")

    def new_inner_value(self, obj):
        return self._get_inner_value(obj.new_inner_value)
    new_inner_value.admin_order_field = 'new_value'
    new_inner_value.short_description = _("Nouvelle valeur")

    def _get_inner_value(self, value):
        if value is None:
            return None
        if hasattr(value, 'pk'):
            value = [value]
        if isinstance(value, list):
            values = []
            for item in value:
                try:
                    app_label, model_name = item._meta.app_label, item._meta.model_name
                    pattern = 'admin:{app_label}_{model_name}_change'.format(app_label=app_label, model_name=model_name)
                    url = reverse(pattern, args=(item.pk, ))
                    values.append('<a href="{url}">{label}</a>'.format(url=url, label=str(item)))
                except Exception:
                    values.append(str(item))
            return format_html("<br />".join(values))
        return value

    def history_url(self, obj):
        app_label, model_name = History._meta.app_label, History._meta.model_name
        pattern = 'admin:{app_label}_{model_name}_changelist'.format(app_label=app_label, model_name=model_name)
        url = reverse(pattern) + '?id={}'.format(obj.history_id)
        return format_html('<a href="{url}">{label}</a>', url=url, label=str(obj.history))
    history_url.admin_order_field = 'history'
    history_url.short_description = _("Historique")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('history__content_type').order_by('-creation_date')


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin):
    """
    Configuration de l'administration pour les web hooks
    """
    list_display = ('id', 'name', 'url', 'method', 'format', 'list_actions', )
    list_display_links = ('id', 'name', )
    list_filter = ('method', 'format', 'is_create', 'is_update', 'is_delete', 'is_restore', 'is_m2m', )
    ordering = ('name', )
    search_fields = ('name', )
    fieldsets = (
        (None, {
            'fields': ('name', 'url', 'format', ),
        }),
        (_("Réseau"), {
            'fields': ('method', 'timeout', 'retries', 'delay', ),
        }),
        (_("Authentification"), {
            'fields': ('authorization', 'token', ),
        }),
        (_("Types"), {
            'fields': ('types', 'is_create', 'is_update', 'is_delete', 'is_restore', 'is_m2m', ),
        }),
    )
    raw_id_fields = ('types', )
    autocomplete_lookup_fields = {
        'm2m': ('types', ),
    }

    def list_actions(self, obj):
        actions = []
        status = dict(History.LOG_STATUS)
        for status_code, attribute in Webhook.STATUS_FILTERS.items():
            if getattr(obj, attribute, False) and status_code in status:
                actions.append(str(status.get(status_code)))
        return ', '.join(actions)
    list_actions.short_description = _("Actions")


@admin.register(ContentType)
class ContentTypeAdmin(admin.ModelAdmin):
    list_display = ('id', 'app_label', 'model', 'name', )
    list_display_links = ('id', )
    list_filter = ('app_label', 'model', )
    ordering = ('app_label', 'model', )
    search_fields = ('model', )


@admin.register(UserMetaData)
class UserMetaDataAdmin(admin.ModelAdmin):
    list_display = ('user', )
    list_display_links = ('user', )
    list_filter = ('user__username', )
    ordering = ('user__username', )
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'user__email', )


@admin.register(GroupMetaData)
class GroupMetaDataAdmin(admin.ModelAdmin):
    list_display = ('group', )
    list_display_links = ('group', )
    list_filter = ('group__name', )
    ordering = ('group__name', )
    search_fields = ('group__name', )


@admin.register(ServiceUsage)
class ServiceUsageAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'count', 'address', 'date', 'limit', 'reset', 'reset_date', )
    list_filter = ('user', 'date', 'reset_date', )
    ordering = ('name', 'user', )
    search_fields = ('name', 'address', )
    raw_id_fields = ('user', )
    autocomplete_lookup_fields = {
        'fk': ('user', ),
    }


def create_admin(*args, **kwargs):
    """
    Permet de créer une administration générique pour un ou plusieurs modèles
    :param args: Modèle(s)
    :param kwargs: Paramètres complémentaires ou surcharges
    :return: Classe(s) d'administration par modèle
    """
    try:
        import grappelli
        from django.conf import settings
        assert 'grappelli' in settings.INSTALLED_APPS
    except (AssertionError, ImportError):
        grappelli = False

    admins = []
    for model in args:
        if not model:
            continue
        admin_superclass = PerishableEntityAdmin if issubclass(model, PerishableEntity) else \
            EntityAdmin if issubclass(model, Entity) else CommonAdmin
        fk_fields = tuple(
            field for field in model._meta.get_fields()
            if isinstance(field, (models.ForeignKey, models.OneToOneField)))
        m2m_fields = tuple(
            field for field in model._meta.get_fields()
            if isinstance(field, models.ManyToManyField))
        properties = dict(
            list_display=tuple(
                field.name for field in model._meta.concrete_fields
                if not field.primary_key and field.editable and not isinstance(field, (
                    models.TextField, JsonField, PickleField))),
            list_filter=tuple(
                field.name for field in model._meta.get_fields()
                if getattr(field, 'choices', None) or isinstance(field, (
                    models.BooleanField, models.NullBooleanField, models.DateField, models.DateTimeField))),
            search_fields=tuple(
                field.name for field in model._meta.get_fields()
                if not getattr(field, 'choices', None) and isinstance(field, (
                    models.CharField, models.TextField))),
            filter_horizontal=tuple(m2m.name for m2m in m2m_fields),
            list_select_related=tuple(fk.name for fk in fk_fields))
        if grappelli:
            fk_fields = tuple(field.name for field in fk_fields)
            m2m_fields = tuple(field.name for field in m2m_fields)
            properties.update(
                autocomplete_lookup_fields=dict(fk=fk_fields, m2m=m2m_fields),
                raw_id_fields=(fk_fields + m2m_fields))
        else:
            autocomplete_fields = []
            for field in fk_fields + m2m_fields:
                for site in all_sites:
                    related_admin = site._registry.get(field.related_model, None)
                    if related_admin and related_admin.search_fields:
                        autocomplete_fields.append(field.name)
                        break
            if autocomplete_fields:
                properties.update(autocomplete_fields=tuple(autocomplete_fields))
        properties.update(**kwargs)
        admins.append(admin.register(model)(
            type('{}GenericAdmin'.format(model._meta.object_name), (admin_superclass, ), properties)))
    return next(iter(admins), None) if len(admins) < 2 else admins
