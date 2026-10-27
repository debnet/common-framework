# coding: utf-8
"""Utilitaires Django publics, conservés dans ce module pour compatibilité d'import.

Les sections regroupent décorateurs, vues HTTP, évaluation de code de confiance,
introspection ORM, conversions, fichiers, sérialisation, permissions et quotas.
Les imports de modèles dans les fonctions évitent les cycles d'initialisation.
"""

import abc
import ast
import collections
import hashlib
import inspect
import json
import logging
import mimetypes
import os
import re
import socket
import sys
import threading
from collections.abc import MutableMapping
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from functools import lru_cache, partial, wraps
from importlib import import_module
from itertools import chain, product
from json import JSONDecoder
from uuid import uuid4

from django.apps import apps
from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, FieldDoesNotExist, ValidationError
from django.core.files import temp
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import TemporaryUploadedFile
from django.core.files.uploadhandler import TemporaryFileUploadHandler
from django.core.mail import EmailMultiAlternatives
from django.db.models import ForeignKey, OneToOneField
from django.db.models.deletion import Collector
from django.db.models.fields.files import FieldFile
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.template import Context, Template
from django.template.loader import render_to_string
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from rest_framework.renderers import JSONRenderer
from rest_framework.utils.encoders import JSONEncoder

# Logging
logger = logging.getLogger(__name__)


# Décorateurs, cache et exécution de tâches
# ---------------------------------------


class singleton:
    """
    Décorateur pour définir une classe singleton
    """

    def __init__(self, _class):
        self._class = _class
        self.instance = None

    def __call__(self, *args, **kwargs):
        if self.instance is None:
            self.instance = self._class(*args, **kwargs)
        return self.instance


def timed_cache(**dkwargs):
    """
    Mémorise les résultats avec un lru_cache vidé à intervalles réguliers.

    L'expiration concerne tout le cache de la fonction, pas chaque entrée.
    Le premier appel et le premier appel après expiration le vident avant
    exécution. Les exceptions de la fonction ne sont pas mémorisées.

    :param dkwargs: Arguments de timedelta, plus maxsize et typed pour lru_cache
    :return: Décorateur conservant la signature introspectable via wraps
    """

    def _wrapper(func):
        maxsize = dkwargs.pop("maxsize", None)
        typed = dkwargs.pop("typed", False)
        update_delta = timedelta(**dkwargs)
        next_update = datetime.utcnow() - update_delta
        func = lru_cache(maxsize=maxsize, typed=typed)(func)

        @wraps(func)
        def _wrapped(*args, **kwargs):
            nonlocal next_update
            utcnow = datetime.utcnow()
            if utcnow >= next_update:
                func.cache_clear()
                next_update = utcnow + update_delta
            return func(*args, **kwargs)

        return _wrapped

    return _wrapper


def timeit(name, log=logger.info):
    """Journalise le début, la fin et la durée d'un appel synchrone.

    Les exceptions sont journalisées puis propagées sans modification. Ce
    décorateur ne mesure pas l'exécution différée d'une coroutine/générateur.

    :param name: Nom lisible de l'opération dans les messages
    :param log: Fonction de journalisation acceptant un message
    :return: Décorateur renvoyant le résultat de la fonction originale
    """

    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            ts = datetime.now()
            log(_("[{}] démarré...").format(name))
            try:
                result = func(*args, **kwargs)
            except Exception as error:
                log(_("[{}] en échec : {}").format(name, error))
                raise
            te = datetime.now()
            log(_("[{}] terminé en {} !").format(name, te - ts))
            return result

        return wrapped

    return decorator


def synchronized(lock=None):
    """Sérialise les appels synchrones avec un verrou de threads.

    Sans lock fourni, chaque fonction décorée reçoit son propre Lock. Fournir
    un verrou commun permet de sérialiser plusieurs fonctions ; utiliser un
    RLock si les appels doivent être réentrants. Le verrou est libéré même
    après exception. Ce n'est ni un verrou de processus ni un verrou asyncio.
    """

    def decorator(func):
        func.__lock__ = lock or threading.Lock()

        @wraps(func)
        def wrapped(*args, **kwargs):
            with func.__lock__:
                return func(*args, **kwargs)

        return wrapped

    return decorator


@singleton
class CeleryFake:
    """
    Mock Celery pour les tâches asynchrones
    """

    def task(self, *dargs, **dkwargs):
        """Imite @app.task(...) et ses entrées apply/apply_async sans broker.

        Toutes les entrées exécutent immédiatement la fonction et renvoient
        son résultat brut, pas un AsyncResult ; leurs erreurs sont propagées.
        Les options de publication et de retry sont ignorées.
        """

        def decorator(func):
            @wraps(func)
            def wrapped(*args, **kwargs):
                return func(*args, **kwargs)

            wrapped.apply = lambda args=None, kwargs=None, **options: func(
                *(args or []), **(kwargs or {})
            )
            wrapped.apply_async = wrapped.apply
            return wrapped

        return decorator


def get_current_app():
    """
    Choisit l'application Celery ou le substitut synchrone CeleryFake.

    Celery n'est chargé que si CELERY_ENABLE est vrai. Les modules qui décorent
    leurs tâches à l'import conservent ensuite ce choix : modifier le setting
    à chaud ne reconfigure pas leurs fonctions déjà décorées.

    :return: current_app si activé et installé, sinon le singleton CeleryFake
    """
    try:
        _assert(getattr(settings, "CELERY_ENABLE", False))
        from celery import current_app

        return current_app
    except (AssertionError, ImportError):
        return CeleryFake()


# Dates et fuseaux horaires
# -----------------------

# Regex de date au format DMY
DMY_DATE_REGEX = re.compile(
    r"^(\d{2})[^\d]?(\d{2})[^\d]?(\d{2,4})([^\d]?(\d{2})[^\d]?(\d{2})[^\d]?(\d{2}))?$"
)


def parsedate(
    input_date,
    start_day=False,
    end_day=False,
    date_only=False,
    utc=False,
    dmy=False,
    **kwargs,
):
    """
    Permet de parser une date quelconque (chaîne, date ou datetime) en un datetime standardisée avec time zone
    :param input_date: Date quelconque
    :param start_day: Ajoute 00:00:00.000000 à une date sans heure (exclusif avec end_day)
    :param end_day: Ajoute 23:59:59.999999 à une date sans heure (exclusif avec start_day)
    :param date_only: Retourne uniquement la date sans l'heure
    :param utc: Retourne la date uniquement en UTC
    :param dmy: Essaye de parser une date au format DMY
    :return: Aware datetime ou date
    """
    _date = input_date
    if not _date:
        return None
    if isinstance(_date, date) and not isinstance(_date, datetime):
        if date_only:
            return _date
        if not start_day and not end_day:
            start_day = True
    elif not isinstance(_date, datetime):
        if dmy:
            match = DMY_DATE_REGEX.match(_date)
            if match:
                date_format = "{0}/{1}/{2}" if date_only else "{0}/{1}/{2} {4}:{5}:{6}"
                _date = date_format.format(*[(group or 0) for group in match.groups()])
            kwargs["dayfirst"] = True
        try:
            from dateutil import parser

            _date = parser.parse(_date, **kwargs)
        except (ImportError, ValueError, OverflowError):
            return None
    if date_only:
        return _date.date()
    if start_day ^ end_day:
        _time = time.min if start_day else time.max
        _date = datetime.combine(_date, _time)
    try:
        import pytz

        use_tz = getattr(settings, "USE_TZ", None)
        timezone = getattr(settings, "TIME_ZONE", None)
        if utc or not use_tz:
            timezone = pytz.utc
        elif timezone:
            timezone = pytz.timezone(timezone)
        if not timezone:
            return _date
        if _date.tzinfo:
            return _date.astimezone(timezone)
        return timezone.localize(_date)
    except ImportError:
        return _date


# Vues HTTP, transferts et rendu de templates
# ------------------------------------------


class TemporaryFile(TemporaryUploadedFile):
    """
    Fichier temporaire avec conservation du nom d'origine
    """

    def __init__(
        self, name, content_type, size, charset, content_type_extra=None, folder=None
    ):
        """Crée un fichier temporaire en conservant son nom et la destination de clôture.

        folder n'est utilisé qu'à close() pour une éventuelle copie vers le
        stockage MEDIA_ROOT. L'appelant doit s'assurer que cette cible est autorisée.
        """
        file = temp.NamedTemporaryFile(suffix="." + name)
        super(TemporaryUploadedFile, self).__init__(
            file, name, content_type, size, charset, content_type_extra
        )
        self.folder = folder

    def close(self):
        if self.folder is not None:
            file_name = "{}.{}".format(now().strftime("%Y%m%d%H%M%S"), self._get_name())
            try:
                to = os.path.join(self.folder, file_name)
                FileSystemStorage(location=settings.MEDIA_ROOT).save(to, self.file)
            except (IOError, OSError):
                logger.error(
                    _("Erreur lors de la sauvegarde du fichier : {}").format(file_name),
                    exc_info=True,
                )
        return super().close()


class TemporaryFileHandler(TemporaryFileUploadHandler):
    """
    Gestionnaire d'upload de fichier temporaire avec conservation du nom d'origine
    """

    def __init__(self, folder=None, *args, **kwargs):
        super(TemporaryFileUploadHandler, self).__init__(*args, **kwargs)
        self.folder = folder

    def new_file(self, file_name, *args, **kwargs):
        super(TemporaryFileUploadHandler, self).new_file(file_name, *args, **kwargs)
        self.file = TemporaryFile(
            self.file_name,
            self.content_type,
            0,
            self.charset,
            self.content_type_extra,
            folder=self.folder,
        )


def temporary_upload(folder=None):
    """
    Décorateur permettant d'indiquer que la vue utilisera l'import de fichier temporaire dans son traitement
    :param folder: Nom ou chemin du repertoire cible de la sauvegarde du fichier
    :return: Méthode décorée
    """

    def decorateur(function):
        @wraps(function)
        @csrf_exempt
        def wrapped(request, *args, **kwargs):
            request.upload_handlers = [TemporaryFileHandler(folder=folder)]
            return csrf_protect(function)(request, *args, **kwargs)

        return wrapped

    return decorateur


class DownloadFile(
    collections.namedtuple(
        "DownloadFile", ["file", "name", "delete", "mimetype", "charset"]
    )
):
    """
    Objet permettant de définir un fichier à télécharger
    file : fichier ou chemin du fichier,
    name : nom du fichier à télécharger,
    delete : supprimer le fichier après le téléchargement,
    mimetype : type mime du fichier à télécharger,
    charset : encodage du fichier à télécharger
    """

    def __new__(cls, file, name, delete, mimetype=None, charset=None):
        return super(DownloadFile, cls).__new__(
            cls, file, name, delete, mimetype, charset
        )


def download_file(function):
    """
    Décorateur permettant de proposer le téléchargement d'un fichier à partir d'une fonction
    La fonction à décorer doit retourner une instance de DownloadFile
    :param function: Méthode à décorer
    :return: Méthode décorée
    """

    def wrapper(*args, **kwargs):
        file = function(*args, **kwargs)
        if isinstance(file, DownloadFile):
            from wsgiref.util import FileWrapper

            file, name, delete, mimetype, charset = file
            if isinstance(file, str):
                from django.core.files import File

                file = File(open(file, "rb"))
            file_wrapper = FileWrapper(file)
            if not mimetype:
                mimetype, charset = mimetypes.guess_type(name)
            mimetype, charset = (
                mimetype or "application/octet-stream",
                charset or settings.DEFAULT_CHARSET,
            )
            response = HttpResponse(
                file_wrapper, content_type=mimetype, charset=charset
            )
            response["Content-Disposition"] = "attachment; filename={0}".format(name)
            response["Content-Type"] = "{mimetype}; charset={charset}".format(
                mimetype=mimetype, charset=charset
            )
            file.close()
            if delete:
                os.unlink(file.name)
            return response
        else:
            return file

    return wrapper


def render_to(template=None, content_type=None):
    """Transforme le dictionnaire renvoyé par une vue en réponse HTML Django.

    La clé spéciale TEMPLATE, si présente, est retirée du dictionnaire et
    remplace le template du décorateur. Toute autre valeur de retour (dont
    HttpResponse) est transmise sans modification. Le rendu reçoit la requête
    et les context processors Django habituels.

    Fournir un template explicite : l'inférence historique sans template utilise
    func_name, attribut hérité de Python 2 qui n'est pas disponible partout.

    :param template: Chemin de template, sauf surcharge par la clé TEMPLATE
    :param content_type: Type MIME facultatif transmis à django.shortcuts.render
    :return: Décorateur de vue synchrone
    """

    def renderer(function):
        @wraps(function)
        def wrapper(request, *args, **kwargs):
            output = function(request, *args, **kwargs)
            if not isinstance(output, dict):
                return output
            tmpl = output.pop("TEMPLATE", template)
            if tmpl is None:
                template_dir = os.path.join(*function.__module__.split(".")[:-1])
                tmpl = os.path.join(template_dir, function.func_name + ".html")
            # Explicit version check to avoid swallowing other exceptions
            return render(request, tmpl, output, content_type=content_type)

        return wrapper

    return renderer


FORMAT_TYPES = {
    "application/json": lambda response: json_encode(response),
    "text/json": lambda response: json_encode(response),
}

try:
    import yaml

    FORMAT_TYPES.update(
        {
            "application/yaml": yaml.dump,
            "text/yaml": yaml.dump,
        }
    )
except ImportError:
    pass


def ajax_request(func):
    """
    If view returned serializable dict, returns response in a format requested
    by HTTP_ACCEPT header. Defaults to JSON if none requested or match.

    Currently supports JSON or YAML (if installed), but can easily be extended.

    example:

        @ajax_request
        def my_view(request):
            news = News.objects.all()
            news_titles = [entry.title for entry in news]
            return {'news_titles': news_titles}
    """

    @wraps(func)
    def wrapper(request, *args, **kwargs):
        for accepted_type in request.META.get("HTTP_ACCEPT", "").split(","):
            if accepted_type in FORMAT_TYPES.keys():
                format_type = accepted_type
                break
        else:
            format_type = "application/json"
        response = func(request, *args, **kwargs)
        if not isinstance(response, HttpResponse):
            if hasattr(settings, "FORMAT_TYPES"):
                format_type_handler = settings.FORMAT_TYPES[format_type]
                if hasattr(format_type_handler, "__call__"):
                    data = format_type_handler(response)
                elif isinstance(format_type_handler, str):
                    mod_name, func_name = format_type_handler.rsplit(".", 1)
                    module = __import__(mod_name, fromlist=[func_name])
                    function = getattr(module, func_name)
                    data = function(response)
            else:
                data = FORMAT_TYPES[format_type](response)
            response = HttpResponse(data, content_type=format_type)
            response["Content-Length"] = len(data)
        return response

    return wrapper


# Liste des built-ins considérés comme "sûrs"
# Évaluation de code de confiance et surcharge temporaire des settings
# -------------------------------------------------------------------

SAFE_GLOBALS = dict(
    __builtins__=dict(
        abs=abs,
        all=all,
        any=any,
        ascii=ascii,
        bin=bin,
        bool=bool,
        bytearray=bytearray,
        bytes=bytes,
        # callable=callable,
        chr=chr,
        # classmethod=classmethod,
        # compile=compile,
        complex=complex,
        delattr=delattr,
        dict=dict,
        # dir=dir,
        divmod=divmod,
        enumerate=enumerate,
        # eval=eval,
        # exec=exec,
        filter=filter,
        float=float,
        format=format,
        frozenset=frozenset,
        getattr=getattr,
        # globals=globals,
        hasattr=hasattr,
        hash=hash,
        help=help,
        hex=hex,
        id=id,
        # input=input,
        int=int,
        # isinstance=isinstance,
        # issubclass=issubclass,
        iter=iter,
        len=len,
        list=list,
        # locals=locals,
        map=map,
        max=max,
        # memoryview=memoryview,
        min=min,
        next=next,
        # object=object,
        oct=oct,
        # open=open,
        ord=ord,
        pow=pow,
        # print=print,
        # property=property,
        range=range,
        repr=repr,
        reversed=reversed,
        round=round,
        set=set,
        setattr=setattr,
        slice=slice,
        sorted=sorted,
        # staticmethod=staticmethod,
        str=str,
        sum=sum,
        # super=super,
        tuple=tuple,
        # type=type,
        # vars=vars,
        zip=zip,
        # __import__=__import__,
    )
)


def evaluate(expression, _globals=None, _locals=None, default=False):
    """
    Évalue une expression Python de confiance et renvoie son résultat.

    Par défaut les contextes sont copiés depuis l'appelant ; SAFE_GLOBALS
    remplace certains builtins sauf si default=True. Cette restriction n'est
    PAS une sandbox : introspection et objets injectés peuvent donner accès
    à des opérations arbitraires. Ne jamais utiliser avec une entrée non fiable.
    Les exceptions d'évaluation sont propagées.
    :param expression: Expression
    :param _globals: Contexte global
    :param _locals: Contexte local
    :param default: Comportement par défaut ?
    :return: Résultat de l'évaluation
    """
    if _globals is None:
        _globals = inspect.currentframe().f_back.f_globals.copy()
    if _locals is None:
        _locals = inspect.currentframe().f_back.f_locals.copy()
    if not default:
        _globals.update(SAFE_GLOBALS)
    return eval(expression, _globals, _locals)


def execute(statement, _globals=None, _locals=None, default=False):
    """
    Exécute du code Python de confiance dans les contextes fournis.

    Les règles de contexte et les limites de sécurité sont celles de evaluate.
    Un dictionnaire _locals explicite reçoit les affectations ; les contextes
    implicites sont des copies de ceux de l'appelant. Aucun résultat n'est
    renvoyé et les exceptions ne sont pas absorbées. Ne pas exposer au public.
    :param statement: Statement
    :param _globals: Contexte global
    :param _locals: Contexte local
    :param default: Comportement par défaut ?
    :return: Rien
    """
    if _globals is None:
        _globals = inspect.currentframe().f_back.f_globals.copy()
    if _locals is None:
        _locals = inspect.currentframe().f_back.f_locals.copy()
    if not default:
        _globals.update(SAFE_GLOBALS)
    exec(statement, _globals, _locals)


@contextmanager
def patch_settings(**kwargs):
    """
    Remplace temporairement des settings Django, puis restaure leur état.

    Le finally restaure aussi les attributs initialement absents et les valeurs
    None, même si le bloc lève une exception. Les contextes imbriqués sont
    possibles, mais les settings restent globaux au processus : ne pas employer
    pour isoler des requêtes concurrentes. override_settings convient mieux
    aux tests qui ont besoin des signaux de reconfiguration de Django.
    :param kwargs: Valeurs à changer
    :return: Rien
    """
    missing = object()
    old_settings = {}
    try:
        for key, new_value in kwargs.items():
            old_settings[key] = getattr(settings, key, missing)
            setattr(settings, key, new_value)
        yield
    finally:
        for key, old_value in old_settings.items():
            if old_value is missing:
                if hasattr(settings, key):
                    delattr(settings, key)
            else:
                setattr(settings, key, old_value)


# Collections, objets de données et combinaisons
# ---------------------------------------------


def recursive_dict_product(
    input_dict,
    all_keys=None,
    long_keys=False,
    separator="_",
    ignore="*",
    auto_id="id",
    prefix="",
):
    """
    Retourne le produit de combinaisons d'un dictionnaire (avec listes et dictionnaires imbriqués) en renommant les clés
    Les listes de dictionnaires produisent un produit cartésien, pas un zip.
    auto_id ajoute des identifiants aux dictionnaires d'entrée : l'argument
    peut donc être modifié ; fournir une copie si cet effet n'est pas souhaité.
    :param input_dict: Dictionnaire à mettre à plat
    :param all_keys: (Facultatif) L'ensemble des clés au pluriel et leur équivalent au singulier pour la transformation
    :param long_keys: Utilise des clés longues (avec l'historique de la hiérarchie)
    :param separator: Séparateur entre les sections et les clés
    :param ignore: Préfixe indiquant que la transformation de cette clé doit être ignorée
    :param auto_id: Suffixe des identifiants uniques ajouté à chaque section
    :param prefix: Préfixe des clés (utile pendant la récursion)
    :return: (Générateur) Combinaisons du dictionnaire
    """
    result = {}
    nested = {}
    dicts = []
    all_keys = all_keys or {}

    # Ajout des identifiants uniques
    if (
        auto_id
        and prefix is not None
        and ((auto_id not in input_dict) or not input_dict[auto_id])
    ):
        input_dict[auto_id] = short_identifier()

    # Récupère les clés mises à plat
    for key, value in input_dict.items():
        current_key = all_keys.get(key, key)
        result_key = (prefix + separator + current_key).lstrip(separator)
        if ignore and key.startswith(ignore):
            result_key = key[1:]
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            # Les dictionnaire imbriqués dans des listes sont à traiter récursivement
            nested_key = result_key if long_keys else current_key
            nested_key = nested_key.rstrip("s") if current_key == key else nested_key
            nested[nested_key] = value
            continue
        elif isinstance(value, dict):
            # Les dictionnaires imbriqués dans des dictionnaires sont récupérés immédiatement par récursivité
            for result in recursive_dict_product(
                value, all_keys, long_keys, separator, ignore, auto_id, result_key
            ):
                dicts.append(result)
            continue
        result[result_key] = value

    # Retourne le résultat s'il n'y a pas de clés imbriquées
    if not nested:
        # Ajoute les dictionnaires imbriqués
        for d in dicts:
            result.update(d)
        # Retourne le résultat de l'itératon
        yield result
        return

    # Crée les différentes combinaisons des structures imbriquées
    for nested_combos in product(*nested.values()):
        results = [result]
        for nested_key, nested_value in zip(nested, nested_combos):
            # Fusionne les données imbriquées avec les résultats
            if isinstance(nested_value, dict):
                results = [
                    dict(r, **result)
                    for result in recursive_dict_product(
                        nested_value,
                        all_keys,
                        long_keys,
                        separator,
                        ignore,
                        auto_id,
                        nested_key,
                    )
                    for r in results
                ]
        for result in results:
            # Ajoute les dictionnaires imbriqués
            for d in dicts:
                result.update(d)
            # Retourne le résultat de l'itération
            yield result


# Introspection des modèles et chargement de relations
# ---------------------------------------------------


def get_choices_fields(*included_apps):
    """
    Permet de recuperer les choices fields existant dans les modèles
    :param included_apps: liste des applications sur lesquelles on récupère les choices fields
    :return: tuple contenant les choices fields triés par application
    """
    from django.apps import apps

    results = {}
    choices_fields = []
    included_apps = included_apps or [app.label for app in apps.get_app_configs()]

    for model in apps.get_models():
        app_label = model._meta.app_label
        if app_label in included_apps:
            for field in model._meta.fields:
                if field.choices and field.choices not in choices_fields:
                    choices_fields.append(field.choices)
                    choice_value = " ".join(
                        [app_label, model._meta.model_name, field.name]
                    )
                    choice_libelle = "{} ({})".format(
                        field.verbose_name, model._meta.verbose_name
                    )
                    if app_label in results:
                        results[app_label].append(
                            (
                                choice_value,
                                choice_libelle,
                            )
                        )
                    else:
                        results[app_label] = [
                            (
                                choice_value,
                                choice_libelle,
                            )
                        ]

    def ordered_choices(result):
        for valeur, libelle in sorted(result, key=lambda x: x[1]):
            yield valeur, libelle

    def choices_by_application():
        for app_label, choices in sorted(results.items()):
            yield str(apps.get_app_config(app_label).verbose_name), tuple(
                ordered_choices(choices)
            )

    return tuple(choices_by_application())


def prefetch_metadata(model, lookup=None, name=None):
    """
    Permet de récupérer les métadonnées valides d'un modèle
    (principalement utilisé dans la récursivité de `get_prefetch()`)
    :param model: Modèle
    :param lookup: Lookup préfixe (facultatif)
    :param name: Nom de l'attribut (force l'évaluation, facultatif)
    :return: Liste de Prefetch
    """
    from django.db.models import Prefetch

    from common.models import MetaData

    for field in model._meta.private_fields:
        if field.related_model is MetaData:
            lookup = (
                field.name if lookup is None else "{}__{}".format(lookup, field.name)
            )
            return [
                Prefetch(lookup, queryset=MetaData.objects.select_valid(), to_attr=name)
            ]
    return []


def get_prefetchs(
    parent,
    depth=1,
    height=1,
    foreign_keys=False,
    one_to_one=True,
    one_to_many=False,
    many_to_many=False,
    reverse_many_to_many=False,
    metadata=False,
    excludes=None,
    nullables=False,
    _model=None,
    _prefetch="",
    _level=1,
):
    """
    Permet de récupérer récursivement tous les prefetch related d'un modèle
    :param parent: Modèle parent
    :param depth: Profondeur de récupération
    :param height: Hauteur de récupération
    :param foreign_keys: Récupère les relations de type foreign-key ?
    :param one_to_one: Récupère les relations de type one-to-one ?
    :param one_to_many: Récupère les relations de type one-to-many ? (peut-être très coûteux selon les données)
    :param many_to_many: Récupère les relations de type many-to-many ?
    :param reverse_many_to_many: Récupère les relations inverses des champs de type many-to-many ?
    :param metadata: Récupère uniquement les prefetchs des métadonnées ?
    :param excludes: Champs ou types à exclure
    :param nullables: Remonter par les clés étrangères nulles ?
    :param _model: Modèle courant (pour la récursivité, nul par défaut)
    :param _prefetch: Nom du prefetch courant (pour la récursivité, vide par défaut)
    :param _level: Profondeur actuelle (pour la récursivité, 1 par défaut)
    :return: Liste des prefetch related associés
    """
    excludes = excludes or []
    results = set(prefetch_metadata(parent) if metadata and not _model else [])
    if _level > depth:
        return results
    model = _model or parent
    for field in model._meta.related_objects + model._meta.many_to_many:
        if field.name in excludes or (field.related_model in excludes):
            continue
        if (
            (field.one_to_one and one_to_one)
            or (field.one_to_many and one_to_many)
            or (field.many_to_many and many_to_many and field.related_model == parent)
            or (
                field.many_to_many
                and reverse_many_to_many
                and field.related_model != parent
            )
        ):
            accessor_name = (
                field.get_accessor_name() if field.auto_created else field.name
            )
            if not accessor_name:
                continue
            recursive_prefetch = (
                accessor_name
                if model == parent
                else "__".join((_prefetch, accessor_name))
            )
            prefetchs = None
            if model == parent or _level < depth:
                prefetchs = get_prefetchs(
                    parent,
                    depth=depth,
                    one_to_one=one_to_one,
                    one_to_many=one_to_many,
                    many_to_many=many_to_many,
                    metadata=metadata,
                    excludes=excludes,
                    _model=field.related_model,
                    _prefetch=recursive_prefetch,
                    _level=_level + 1,
                )
                results.update(prefetchs)
            if height and not field.many_to_many:
                for related in get_related(
                    field.related_model,
                    excludes=excludes,
                    foreign_keys=foreign_keys,
                    one_to_one=one_to_one,
                    nullables=nullables,
                    height=height,
                ):
                    results.add("__".join((recursive_prefetch, related)))
            if metadata:
                results.update(prefetch_metadata(parent, lookup=recursive_prefetch))
            elif not prefetchs:
                results.add(recursive_prefetch)
    return results


def get_related(
    model,
    dest=None,
    excludes=None,
    foreign_keys=True,
    one_to_one=False,
    nullables=False,
    height=1,
    _related="",
    _models=None,
    _level=0,
):
    """
    Permet de récupérer récursivement toutes les relations directes d'un modèle
    :param model: Modèle d'origine
    :param dest: Modèle de destination (facultatif)
    :param excludes: Champs ou types à exclure
    :param foreign_keys: Récupère les relations de type foreign-key ?
    :param one_to_one: Récupère les relations de type one-to-one ?
    :param nullables: Remonter par les clés étrangères nulles ?
    :param height: Hauteur de récupération
    :param _related: Nom du chemin de relation courant (pour la récursivité, vide par défaut)
    :param _models: Liste des modèles traversés (pour la récursivité, vide par défaut)
    :param _level: Profondeur actuelle (pour la récursivité, 0 par défaut)
    :return: Liste des relations directes associées
    """
    excludes = excludes or []
    results = set()
    if (not dest and _level > height) or (_models and model in _models):
        return results
    models = (_models or []) + [model]
    if _related and dest == model or (dest is None and _related):
        results.add(_related)
    # Clés étrangères
    if foreign_keys:
        for field in model._meta.fields:
            if (
                not isinstance(field, (ForeignKey, OneToOneField))
                or field.name in excludes
                or (field.remote_field and field.related_model in excludes)
                or (not nullables and field.null)
            ):
                continue
            related_path = "__".join((_related, field.name)) if _related else field.name
            results.update(
                get_related(
                    field.related_model,
                    dest=dest,
                    excludes=excludes,
                    height=height,
                    nullables=nullables,
                    _related=related_path,
                    _models=models,
                    _level=_level + 1,
                )
            )
    # Relations de type one-to-one
    if one_to_one:
        for field in model._meta.related_objects:
            if field.one_to_one:
                field_name = field.get_accessor_name()
                if field_name in excludes:
                    continue
                related_path = (
                    "__".join((_related, field_name)) if _related else field_name
                )
                results.update(
                    get_related(
                        field.related_model,
                        dest=dest,
                        excludes=excludes,
                        height=height,
                        nullables=nullables,
                        _related=related_path,
                        _models=models,
                        _level=_level + 1,
                    )
                )
    return results


def prefetch_generics(weak_queryset):
    """
    Précharge récursivement les cibles des GenericForeignKey d'un QuerySet.

    Évalue le QuerySet, regroupe ses identifiants par ContentType puis alimente
    les relations des instances. Le chemin historique convertit les PK en int
    et n'inclut pas de détection de cycles : ne convient pas aux PK arbitraires
    ni aux graphes génériques cycliques. Les erreurs ORM sont propagées.
    :param weak_queryset: QuerySet d'origine
    :return: QuerySet avec prefetch
    """
    from django.contrib.contenttypes.fields import GenericForeignKey
    from django.contrib.contenttypes.models import ContentType

    weak_queryset = weak_queryset.select_related()

    gfks = {}
    for name, gfk in weak_queryset.model.__dict__.items():
        if not isinstance(gfk, GenericForeignKey):
            continue
        gfks[name] = gfk

    data = {}
    for weak_model in weak_queryset:
        for gfk_name, gfk_field in gfks.items():
            related_content_type_id = getattr(
                weak_model,
                gfk_field.model._meta.get_field(gfk_field.ct_field).get_attname(),
            )
            if not related_content_type_id:
                continue
            related_content_type = ContentType.objects.get_for_id(
                related_content_type_id
            )
            related_object_id = int(getattr(weak_model, gfk_field.fk_field))

            if related_content_type not in data.keys():
                data[related_content_type] = []
            data[related_content_type].append(related_object_id)

    for content_type, object_ids in data.items():
        model_class = content_type.model_class()
        models = prefetch_generics(model_class.objects.filter(pk__in=object_ids))
        for model in models:
            for weak_model in weak_queryset:
                for gfk_name, gfk_field in gfks.items():
                    related_content_type_id = getattr(
                        weak_model,
                        gfk_field.model._meta.get_field(
                            gfk_field.ct_field
                        ).get_attname(),
                    )
                    if not related_content_type_id:
                        continue
                    related_content_type = ContentType.objects.get_for_id(
                        related_content_type_id
                    )
                    related_object_id = int(getattr(weak_model, gfk_field.fk_field))

                    if related_object_id != model.pk:
                        continue
                    if related_content_type != content_type:
                        continue
                    setattr(weak_model, gfk_name, model)
    return weak_queryset


def get_field_by_path(model, path):
    """
    Permet de récupérer un champ de modèle depuis un modèle d'origine en suivant un chemin
    :param model: Modèle d'origine
    :param path: Chemin vers le champ ciblé
    :return: Champ
    """
    field_name, *inner_path = path.replace("__", ".").split(".")
    try:
        field = model._meta.get_field(field_name)
    except FieldDoesNotExist:
        return None
    if inner_path:
        if field.related_model:
            return get_field_by_path(field.related_model, ".".join(inner_path))
        return field
    return field


# Conversions scalaires et normalisation des données
# -------------------------------------------------


def str_to_bool(value):
    """
    Permet de renvoyer le booleen correspondant à la valeur entrée en paramètre
    :param value: valeur à analyser
    :return: le booleen correspondant ou None si aucune correspondance
    """
    if isinstance(value, bool):
        return value
    if not value or not isinstance(value, str):
        return bool(value)
    # Valeurs considérées comme vraies ou fausses (déclarées dans la fonction à cause du moteur i18n)
    TRUE_VALUES = {"true", "yes", "y", "1", _("vrai"), _("oui"), _("o"), _("v")}
    FALSE_VALUES = {"false", "no", "n", "0", _("faux"), _("non"), _("n"), _("f")}
    if value is True or value is False:
        return value
    if value is None or str(value).lower() not in TRUE_VALUES | FALSE_VALUES:
        return None
    return str(value).lower() in TRUE_VALUES


def str_to_num(value, force_int=False):
    """
    Permet de renvoyer le nombre correspondant à la valeur chaîne entrée en paramètre
    Les valeurs falsy et les nombres déjà convertis sont conservés. Le parsing
    essaie literal_eval puis int/float ; les entrées non numériques ne sont pas
    toutes strictement rejetées par le chemin historique literal_eval.
    :param value: valeur à analyser
    :param force_int: Forcer le résultat en entier
    :return: valeur numérique correspondante ou None sinon
    """
    if not value or isinstance(value, (int, float)):
        return value
    try:
        result = ast.literal_eval(str(value))
        if isinstance(result, (int, float)):
            value = result
    except (SyntaxError, ValueError):
        try:
            cast = float if "." in str(value) else int
            value = cast(str(value))
        except ValueError:
            return None
    return int(value) if force_int else value


str_to_int = partial(str_to_num, force_int=True)


def get_first(*values, condition=None, default=None):
    """
    Permet de renvoyer le premier élément qui valide la condition parmi l'ensemble des valeurs
    :param values: Liste d'éléments
    :param condition: Fonction de filtrage conditionnel (non nul par défaut)
    :param default: Valeur par défaut si non trouvé
    :return: Premier élément qui valide la condition
    """
    condition = condition or (lambda e: e is not None)
    return next(filter(condition, values), default)


def _assert(condition, message=None):
    """
    Remplace le mot-clé assert dans le cas où Python est exécuté avec optimisation
    :param condition: Condition à évaluer
    :param message: Message de l'exception
    :return: Rien
    :raise: AssertionError
    """
    if condition:
        return
    if message:
        raise AssertionError(message)
    raise AssertionError()


def decimal(value=None, precision=None, rounding=ROUND_HALF_EVEN, context=None):
    """
    Permet de gérer la précision et l'arrondi des nombres décimaux
    :param value: Valeur
    :param precision: Précision
    :param rounding: Méthode d'arrondi
    :param context: Contexte
    :return: Nombre décimal
    """
    if value is None or value == "":
        return Decimal()
    _value = value

    if isinstance(value, str):
        _value = Decimal(value, context=context)
    elif isinstance(value, (int, float)):
        _value = Decimal(repr(value), context=context)
    if precision is None:
        return _value

    if isinstance(precision, int):
        precision = Decimal("0." + "0" * (precision - 1) + "1")
    try:
        return Decimal(_value.quantize(precision, rounding=rounding), context=context)
    except InvalidOperation:
        return _value


def decimal_to_str(value):
    """
    Formate un nombre sans notation scientifique ni zéros fractionnaires finaux.

    Les chaînes sont conservées telles quelles, None devient une chaîne vide.
    Les zéros entiers sont préservés (Decimal('100') donne '100') ; cette règle
    sert notamment à normaliser les clés décimales de idict sans collision.
    :param value: Valeur
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


# Découverte du routage Django et représentation des paramètres d'URL
# -----------------------------------------------------------------

# Regex permettant d'extraire les paramètres d'une URL
REGEX_URL_PARAMS = re.compile(r"\(\?P<([\w_]+)>[^\)]+\)")


def recursive_get_urls(
    module=None,
    namespaces=None,
    attributes=None,
    model=None,
    _namespace=None,
    _current="/",
):
    """
    Récupère les URLs d'un module
    Parcourt récursivement les include et namespaces et produit des couples
    (nom_de_vue_qualifié, chemin_avec_paramètres). Les paramètres de regex sont
    représentés par des marqueurs :nom: destinés notamment au helper JavaScript.
    Ce n'est pas un contrôle d'accès : publier ce résultat révèle le routage.
    :param module: Module à explorer
    :param namespaces: Liste des namespaces à récupérer
    :param attributes: Liste des propriétés à vérifier dans le module
    :param model: Modèle dont on souhaite retrouver les URLs
    :param _namespace: Namespace courant pour la récursion
    :param _current: Fragment d'URL courante pour la récursion
    :return: Générateur
    """
    namespaces = namespaces or []
    attributes = attributes or ["urlpatterns", "api_urlpatterns"]

    try:
        if not module:
            module = import_module(settings.ROOT_URLCONF)
        patterns = module

        patterns = (
            list(chain(*(getattr(module, attribute, []) for attribute in attributes)))
            or patterns
        )
        if patterns and isinstance(patterns[-1], str):
            patterns, *_ = patterns
    except (TypeError, ValueError):
        patterns = []

    for pattern in patterns:
        try:
            namespace = _namespace or getattr(pattern, "namespace", None)
            if namespaces and namespace not in namespaces:
                continue
            url = _current + pattern.pattern.regex.pattern.strip("^$\\Z").replace(
                "\\", ""
            )
            url = re.sub(REGEX_URL_PARAMS, r":\1:", url).replace("?", "")
            url = url.replace("(.+)", ":pk:")
            if getattr(pattern.pattern, "name", None):
                key = (
                    "{}:{}".format(namespace, pattern.pattern.name)
                    if namespace
                    else pattern.name
                )
                current_model = getattr(
                    getattr(pattern.callback, "cls", None), "model", None
                )
                if not model or model is current_model:
                    yield key, url
            elif getattr(pattern, "namespace", None) and pattern.urlconf_module:
                yield from recursive_get_urls(
                    pattern.urlconf_module,
                    namespaces=namespaces,
                    attributes=attributes,
                    model=model,
                    _namespace=_namespace or pattern.namespace,
                    _current=url,
                )
        except AttributeError:
            continue


# Dictionnaires, objets de données et représentation mémoire
# ---------------------------------------------------------


class CustomDict(MutableMapping):
    """
    Surcouche du dictionnaire pour transformer les clés en entrée/sortie
    """

    def __init__(self, *args, **kwargs):
        self._dict = {}
        self.update(dict(*args, **kwargs))

    def __getitem__(self, key):
        return self._dict[self._transform(key)]

    def __setitem__(self, key, value):
        self._dict[self._transform(key)] = value

    def __delitem__(self, key):
        del self._dict[self._transform(key)]

    def __iter__(self):
        return iter(self._dict)

    def __len__(self):
        return len(self._dict)

    def __repr__(self):
        return repr(self._dict)

    def __str__(self):
        return str(self._dict)

    def __getattr__(self, item):
        try:
            return self.__getattribute__(item)
        except AttributeError:
            return self[item]

    @abc.abstractmethod
    def _transform(self, key):
        return key


class idict(CustomDict):
    """
    Dictionnaire qui transforme les clés en chaînes de caractères
    """

    def _transform(self, key):
        """Normalise récursivement les clés composées et les Decimal en clés stables."""
        if isinstance(key, (list, tuple)):
            return tuple(self._transform(k) for k in key)
        if isinstance(key, Decimal):
            return decimal_to_str(key)
        return str(key)


def sort_dict(idict):
    """
    Tri l'ensemble des valeurs d'un dictionnaire par les clés
    :param idict: Dictionnaire
    :return: Dictionnaire trié
    """
    return json_decode(
        json_encode(idict, sort_keys=True), object_pairs_hook=collections.OrderedDict
    )


def merge_dict(mdict, *idicts, **kwargs):
    """
    Permet de fusionner un ou plusieurs dictionnaires imbriqués sur un autre
    :param mdict: Dictionnaire sur lequel fusionner les données
    :param idicts: Liste des dictionnaires à fusionner
    :param kwargs: Données supplémentaires à fusionner
    :return: Dictionnaire sur lequel les données ont été fusionnées
    """
    mdict = mdict if mdict is not None else {}
    for idict in idicts:
        for key, value in idict.items():
            if (
                key in mdict
                and isinstance(mdict[key], dict)
                and isinstance(idict[key], dict)
            ):
                merge_dict(mdict[key], idict[key])
            else:
                mdict[key] = idict[key]
    if kwargs:
        merge_dict(mdict, kwargs)
    return mdict


class Null(object):
    """
    Objet nul absolu
    """

    _instances = {}

    def __new__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Null, cls).__new__(cls, *args, **kwargs)
        return cls._instances[cls]

    def __init__(self, *args, **kwargs):
        pass

    def __repr__(self):
        return "null"

    def __str__(self):
        return ""

    def __eq__(self, other):
        return id(self) == id(other) or other is None

    def __iter__(self):
        return iter([])

    def __len__(self):
        return 0

    # Null est faux dans un contexte booléen
    __nonzero__ = __bool__ = lambda self: False

    # Null se retourne lui-même en toutes circonstances
    nullify = lambda self, *args, **kwargs: self  # noqa

    __call__ = nullify
    __getattr__ = __setattr__ = __delattr__ = nullify
    __cmp__ = __ne__ = __lt__ = __gt__ = __le__ = __ge__ = nullify
    __pos__ = __neg__ = __abs__ = __invert__ = nullify
    __add__ = __sub__ = __mul__ = __mod__ = __pow__ = nullify
    __floordiv__ = __div__ = __truediv__ = __divmod__ = nullify
    __lshift__ = __rshift__ = __and__ = __or__ = __xor__ = nullify
    __radd__ = __rsub__ = __rmul__ = __rmod__ = __rpow__ = nullify
    __rfloordiv__ = __rdiv__ = __rtruediv__ = __rdivmod__ = nullify
    __rlshift__ = __rrshift__ = __rand__ = __ror__ = __rxor__ = nullify
    __iadd__ = __isub__ = __imul__ = __imod__ = __ipow__ = nullify
    __ifloordiv__ = __idiv__ = __itruediv__ = __idivmod__ = nullify
    __ilshift__ = __irshift__ = __iand__ = __ior__ = __ixor__ = nullify
    __getitem__ = __setitem__ = __delitem__ = nullify
    __getslice__ = __setslice__ = __delslice__ = nullify
    __reversed__ = nullify
    __contains__ = __missing__ = nullify
    __enter__ = __exit__ = nullify


# Valeur nulle absolue
null = Null()


def to_tuple(data):
    """
    Transforme un dictionnaire ou une liste de dictionnaires en une série de tuples imbriqués
    :param data: Dictionnaire ou liste de dictionnaires
    :return: Tuple
    """
    if isinstance(data, (list, set)):
        return tuple(to_tuple(element) for element in data)
    if isinstance(data, dict):
        return tuple(
            {key: to_tuple(value) for key, value in sorted(data.items())}.items()
        )
    return data


def to_namedtuple(data, name="DictTuple"):
    """
    Transforme un dictionnaire ou une liste de dictionnaires en une série de tuples nommés imbriqués
    :param data: Dictionnaire ou liste de dictionnaires
    :param name: Nom du tuple
    :return: Tuple nommé
    """
    if isinstance(data, dict):
        subdata = {key: to_namedtuple(value, name=name) for key, value in data.items()}
        return collections.namedtuple(name, subdata.keys())(**subdata)
    if isinstance(data, (list, set)):
        return to_tuple(data)
    return data


def to_object(data, name="Context", default=None):
    """
    Transforme un dictionnaire en objet ou une liste de dictionnaire en liste d'objets
    :param data: Dictionnaire ou liste de dictionnaires
    :param name: Nom de l'objet
    :param default: Valeur par défaut des attributs
    :return: Objet ou liste d'objets
    """

    def _getattr(s, k):
        try:
            object.__getattribute__(s, k)
        except AttributeError:
            return s.get(k, default)

    if isinstance(data, list):
        return [to_object(ctx, name) for ctx in data]
    elif isinstance(data, dict):
        attrs = dict(__getattr__=lambda s, k: _getattr(s, k))
        subdata = {}
        for key, value in data.items():
            if isinstance(value, (list, dict)):
                subdata[key] = to_object(value, name)
                continue
            subdata[key] = value
        return type(name, (dict,), attrs)(subdata)
    return data


def is_namedtuple(obj):
    """
    Vérifie qu'un objet est un tuple nommé
    :param obj: Objet à vérifier
    :return: Vrai si tuple nommé sinon faux
    """
    _type = type(obj)
    bases = _type.__bases__
    if len(bases) != 1 or bases[0] is not tuple:
        return False
    fields = getattr(_type, "_fields", None)
    if not isinstance(fields, tuple):
        return False
    return all(isinstance(i, str) for i in fields)


def to_dict(data):
    """
    Transforme un tuple nommé ou une série de tuple nommés en dictionnaire
    :param data: Tuple nommé ou ensemble de tuple nommés
    :return: Dictionnaire
    """
    if isinstance(data, dict):
        return {key: to_dict(value) for key, value in data.items()}
    if isinstance(data, list):
        return [to_dict(value) for value in data]
    if is_namedtuple(data):
        return {key: to_dict(value) for key, value in data._asdict().items()}
    if isinstance(data, tuple):
        return tuple(to_dict(value) for value in data)
    return data


dict_to_tuple = to_tuple
dict_to_namedtuple = to_namedtuple
dict_to_object = to_object
namedtuple_to_dict = to_dict


def get_size(obj, _seen=None):
    """
    Calcule la taille en octets d'un objet Python quelconque
    Estimation récursive par getsizeof, avec détection des objets déjà visités.
    Peut consommer un itérable/générateur et ne mesure pas tous les buffers
    natifs d'extensions ; ce n'est pas un outil de comptabilité mémoire exact.
    :param obj: Objet
    :param _seen: Liste des objets déjà calculés (utilisé uniquement par la récursivité)
    :return: Taille en octets de l'objet
    """
    size = sys.getsizeof(obj)
    if _seen is None:
        _seen = set()
    obj_id = id(obj)
    if obj_id in _seen:
        return 0
    _seen.add(obj_id)
    if hasattr(obj, "__dict__"):
        for cls in obj.__class__.__mro__:
            if "__dict__" in cls.__dict__:
                d = cls.__dict__["__dict__"]
                if inspect.isgetsetdescriptor(d) or inspect.ismemberdescriptor(d):
                    size += get_size(obj.__dict__, _seen)
                break
    if isinstance(obj, dict):
        size += sum((get_size(v, _seen) for v in obj.values()))
        size += sum((get_size(k, _seen) for k in obj.keys()))
    elif hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes, bytearray)):
        size += sum((get_size(i, _seen) for i in obj))
    return size


# Inspection de fichiers, empreintes et extraction d'archives
# ---------------------------------------------------------


def file_is_text(file):
    """Estime si les 1 024 premiers octets ressemblent à du texte.

    Cette heuristique ne valide ni l'encodage ni l'intégrité du fichier. Les
    erreurs d'ouverture sont propagées ; le fichier est ouvert en mode binaire.
    """
    textchars = bytearray([7, 8, 9, 10, 12, 13, 27]) + bytearray(range(0x20, 0x100))
    is_plaintext = lambda _bytes: not bool(_bytes.translate(None, textchars))  # noqa
    with open(file, "rb") as f:
        return is_plaintext(f.read(1024))


def seek_end(file, count=1):
    """Lit count octets depuis la fin d'un fichier binaire.

    Renvoie b'' sur OSError, notamment si le fichier est trop court pour le
    seek demandé. Ne confondre ni cette valeur avec une preuve d'absence du
    fichier, ni la stabilité des octets avec une fin de transfert garantie.
    """
    try:
        with open(file, "rb") as f:
            f.seek(-count, 2)
            result = f.read()
        return result
    except OSError:
        return b""


def hash_file(path, func=hashlib.sha1, chunks=None):
    """Calcule l'empreinte hexadécimale d'un fichier existant, sinon renvoie None.

    func est une fabrique hashlib ; chunks borne les lectures en octets, sinon
    tout le fichier est chargé. SHA-1 est conservé pour compatibilité, pas pour
    l'authenticité cryptographique : fournir sha256 pour les nouveaux usages.
    Les erreurs d'ouverture/lecture d'un fichier existant sont propagées.
    """
    if not os.path.exists(path):
        return
    digest = func()
    with open(path, "rb") as file:
        if chunks:
            while chunk := file.read(chunks):
                digest.update(chunk)
        else:
            digest.update(file.read())
    return digest.hexdigest()


def _archive_member_path(directory, name):
    """Valide un nom d'archive et renvoie sa destination réelle sous directory.

    Refuse les chemins absolus Windows/POSIX, les segments '..' et les chemins
    qui s'échappent par un symlink préexistant. Lève ValueError avant extraction.
    Le répertoire cible doit rester non modifiable par un tiers pendant celle-ci.
    """
    import ntpath

    normalized = name.replace("\\", "/")
    if (
        ntpath.splitdrive(normalized)[0]
        or normalized.startswith("/")
        or ".." in normalized.split("/")
    ):
        raise ValueError("Unsafe archive member: {}".format(name))
    destination = os.path.realpath(directory)
    target = os.path.realpath(os.path.join(destination, normalized))
    if os.path.commonpath((destination, target)) != destination:
        raise ValueError("Archive member escapes destination: {}".format(name))
    return target


def process_file(file_path, sleep=5, extract_directory=None):
    """
    Vérifie qu'un fichier quelconque est complet et lisible
    Si le fichier est une archive, elle sera décompressée dans le dossier selectionné
    La stabilisation repose sur les derniers octets, sans délai maximal : elle
    ne prouve pas que le producteur a définitivement terminé son écriture.
    Les chemins et liens d'archives sont validés avant extraction, mais aucun
    plafond de volume décompressé n'est appliqué. Utiliser une destination privée
    et des quotas pour les fichiers non fiables. Les erreurs sont propagées.
    :param file_path: Chemin vers le fichier
    :param sleep: Temps d'attente entre deux vérifications de la complétude du fichier
    :param extract_directory: (Facultatif) Répertoire d'extraction sinon répertoire courant du fichier
    :return: Chemin d'origine pour un fichier ordinaire, None après extraction
    """
    import time

    file_base = os.path.abspath(file_path)
    extract_directory = os.path.abspath(extract_directory or os.path.dirname(file_base))
    # Boucle tant que la copie n'est pas terminée
    while True:
        try:
            chunk = None
            while chunk is None or chunk != seek_end(file_path, count=100):
                chunk = seek_end(file_path, count=100)
                time.sleep(sleep)
            break
        except PermissionError:
            time.sleep(sleep)
    # Extraction des fichiers en fonction du type d'archive
    filename, extension = os.path.splitext(file_path)
    if extension:
        extension = extension.lower()
    try:
        if extension == ".zip":
            import stat
            from zipfile import ZipFile

            with ZipFile(file_path) as zip:
                for member in zip.infolist():
                    _archive_member_path(extract_directory, member.filename)
                    if stat.S_ISLNK(member.external_attr >> 16):
                        raise ValueError(
                            "Archive links are not allowed: {}".format(member.filename)
                        )
                zip.extractall(path=extract_directory)
            return
        elif extension in [".tar", ".gz", ".bz2", ".xz", ".tgz", ".tbz2", ".txz"]:
            import tarfile

            with tarfile.open(file_path, "r:*") as tar:
                members = tar.getmembers()
                for member in members:
                    _archive_member_path(extract_directory, member.name)
                    if not (member.isfile() or member.isdir()):
                        raise ValueError(
                            "Archive links and special files are not allowed: {}".format(
                                member.name
                            )
                        )
                # Le filtre natif est disponible sur les versions Python récentes.
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(
                        path=extract_directory, members=members, filter="data"
                    )
                else:
                    tar.extractall(path=extract_directory, members=members)
            return
    except Exception:
        logger.error(
            _("Erreur lors du désarchivage : {}").format(file_base), exc_info=True
        )
        raise
    return file_path


# Encodages, identifiants et sérialisation JSON
# -------------------------------------------


def base64_encode(data):
    """
    Encode une chaîne en base64
    :param data: Chaîne à encoder
    :return: Chaîne encodée en base64
    """
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    return urlsafe_base64_encode(force_bytes(data))


def base64_decode(data):
    """
    Décode du Base64 URL-safe vers une chaîne Unicode via force_str.

    Le padding manquant est géré par Django ; les erreurs de décodage sont
    propagées. Utiliser directement base64 pour un résultat binaire brut.
    :param data: Chaîne base64 à décoder
    :return: Chaîne décodée
    """
    from django.utils.encoding import force_str
    from django.utils.http import urlsafe_base64_decode

    return force_str(urlsafe_base64_decode(data))


def short_identifier():
    """
    Encode la composante time d'un UUID4 avec un alphabet Base62.

    L'identifiant est compact mais n'offre pas une garantie d'unicité ni les
    propriétés d'un jeton d'authentification. Utiliser une contrainte en base
    pour les identifiants persistants et secrets pour les jetons de sécurité.
    """
    alphabet = tuple("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    base = len(alphabet)
    num = uuid4().time
    digits = []
    while num > 0:
        num, rem = divmod(num, base)
        digits.append(alphabet[rem])
    return "".join(reversed(digits))


class JsonEncoder(JSONEncoder):
    """
    Encodeur JSON spécifique
    """

    encoding = {}  # type : callable

    def __init__(self, *args, **kwargs):
        if "sort_keys" not in kwargs:
            kwargs["sort_keys"] = True
        super().__init__(*args, **kwargs)

    def default(self, obj):
        """Applique les extensions Common puis le fallback de l'encodeur DRF.

        encoding permet d'enregistrer des conversions par type. null devient
        None, les FieldFile donnent leur nom et les bytes du Base64. Les autres
        types non pris en charge entraînent TypeError via le parent.
        """
        for type, func in self.encoding.items():
            if isinstance(obj, type):
                return func(obj)
        if obj is null:
            return None
        if isinstance(obj, FieldFile):
            return obj.name
        if isinstance(obj, bytes):
            return base64_encode(obj)
        return super().default(obj)


# Surcharge de l'encodeur JSON de DRF
JSONRenderer.encoder_class = JsonEncoder


class JsonDecoder(JSONDecoder):
    """
    Décodeur JSON spécifique
    """

    def __init__(self, *args, **kwargs):
        if "parse_float" not in kwargs:
            kwargs["parse_float"] = decimal
        super().__init__(*args, **kwargs)


# JSON serialization
def json_encode(data, cls=None, **options):
    """Sérialise en JSON avec JsonEncoder, ou une classe explicitement fournie.

    options est transmis à json.dumps ; les erreurs de type et références
    circulaires restent visibles. Le résultat est une chaîne, pas des octets.
    """
    return json.dumps(data, cls=cls or JsonEncoder, **options)


# JSON deserialization
def json_decode(data, content_encoding="utf-8", cls=None, **options):
    """Décode une chaîne/des bytes JSON, avec des floats en Decimal par défaut.

    content_encoding ne sert qu'aux bytes ; cls/options sont transmis à
    json.loads. Les erreurs de décodage, JSON et types ne sont pas absorbées.
    """
    if isinstance(data, bytes):
        data = data.decode(content_encoding)
    return json.loads(data, cls=cls or JsonDecoder, **options)


# Administration SQL et introspection des modèles/permissions
# ---------------------------------------------------------


def abort_sql(name, kill=False, using=None, timeout=None, state="active"):
    """
    Permet d'interrompre une ou plusieurs connexions SQL d'une application nommée
    :param name: Nom de l'application (paramètre "application_name" du client)
    :param kill: Tue le processus si vrai ou essaye de stopper proprement la tâche si faux
    :param using: Alias de la base de données sur laquelle réaliser l'action
    :param timeout: Temps d'exécution maximal (en secondes) à partir duquel il faut supprimer les requêtes
    :param state: Etat des connexion à interrompre ('active' ou 'idle')
    Cette opération administrative affecte les sessions PostgreSQL correspondant
    au nom d'application et demande les privilèges PostgreSQL appropriés.
    Ne pas l'exposer à une entrée utilisateur sans autorisation dédiée.

    :return: Nombre de lignes retournées par PostgreSQL, pas nombre de succès ;
        objet AssertionError (non levé) si le moteur n'est pas PostgreSQL
    """
    from django.db import DEFAULT_DB_ALIAS, connections

    from common.fields import is_postgresql

    connection = connections[using or DEFAULT_DB_ALIAS]
    if not is_postgresql(connection):
        return AssertionError(
            _("Cette fonction ne peut être utilisée que sur PostgreSQL.")
        )
    with connection.cursor() as cursor:
        query = (
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = %s"
            if kill
            else "SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE application_name = %s"
        )
        params = [name]
        if timeout:
            query += " AND NOW() - query_start > interval '%s seconds'"
            params.append(timeout)
        if state:
            query += " AND state = %s"
            params.append(state)
        cursor.execute(query, params)
        return len(cursor.fetchall())


def get_current_user():
    """
    Recherche un HttpRequest nommé request dans les frames de la pile courante.

    Renvoie son utilisateur s'il a une PK, sinon None. C'est un fallback
    historique coûteux, non une propagation de contexte fiable pour Celery ou
    asyncio ; passer explicitement _current_user aux sauvegardes est préférable.
    """
    for frameinfo in inspect.stack():
        frame = frameinfo.frame
        if "request" not in frame.f_locals:
            continue
        request = frame.f_locals["request"]
        if not isinstance(request, HttpRequest):
            continue
        if not hasattr(request, "user"):
            continue
        return request.user if request.user.pk else None
    return None


def get_pk_field(model):
    """
    Récupère le champ qui fait office de clé primaire d'un modèle
    :param model: Modèle
    :return: Champ
    """
    meta = model._meta
    if meta.pk and not meta.parents:
        return meta.pk
    for parent, field in meta.parents.items():
        pk = get_pk_field(parent)
        if pk:
            return pk
    return None


def collect_deleted_data(object):
    """
    Collecte les objets supprimés et modifiés en conséquence de la suppresion d'un objet donné
    :param object: Objet supprimé
    :return: Objets supprimés, objets modifiés
    """
    models = apps.get_models()
    collector = Collector(using=object._state.db)
    collector.collect([object])
    deleted, changed = {}, {}
    for model, instances in collector.data.items():
        from_model = model._meta.auto_created
        if from_model and from_model in models:  # Potential many-to-many
            from_model.meta = from_model._meta  # For template
            from_field, to_field, *_ = model._meta.fields[1:]
            field = next(
                field
                for field in from_model.meta.many_to_many
                if to_field and field.related_model == to_field.related_model
            )
            for _instance in instances:
                instance, value = getattr(_instance, from_field.name), getattr(
                    _instance, to_field.name
                )
                if instance == object:
                    continue
                changed.setdefault(from_model, {}).setdefault(instance, {}).setdefault(
                    field, []
                ).append(value)
        else:
            if model not in models:
                continue
            model.meta = model._meta  # For template
            for instance in instances:
                if instance == object:
                    continue
                deleted.setdefault(model, []).append(instance)
    for model, fields in collector.field_updates.items():
        if model not in models:
            continue
        model.meta = model._meta  # For template
        for (from_field, value), instances in fields.items():
            for instance in instances:
                if instance in collector.data.get(model, ()):
                    continue
                changed.setdefault(model, {}).setdefault(instance, {}).setdefault(
                    from_field, value
                )
    return deleted, changed


def send_mail(
    mail_from=None,
    mail_to=None,
    mail_cc=None,
    mail_bcc=None,
    mail_reply_to=None,
    mail_subject=None,
    mail_text=None,
    mail_html=None,
    mail_data=None,
    files=None,
    fail_silently=False,
    force=False,
):
    """
    Permet d'envoyer un e-mail avec un ou plusieurs documents attachés
    :param mail_from: Adresse e-mail de l'émetteur
    :param mail_to: Adresse(s) e-mail de(s) récepteur(s)
    :param mail_cc: Adresse(s) e-mail de(s) récepteur(s) en copie carbone
    :param mail_bcc: Adresse(s) e-mail de(s) récepteur(s) en copie carbone invisible
    :param mail_reply_to: Adresse(s) e-mail de réponse
    :param mail_subject: Objet de l'e-mail
    :param mail_text: Nom du template ou corps en pur-texte de l'e-mail
    :param mail_html: Nom du template ou corps en HTML de l'e-mail
    :param mail_data: Données pour le contenu de l'e-mail (en texte comme en HTML)
    :param files: Liste de fichiers complémentaires à joindre dans l'e-mail (chemin absolu, mimetype)
    :param fail_silently: Ne lève pas d'exception en cas d'erreur lors de l'envoi de l'e-mail
    :param force: Force l'envoi de l'e-mail même si la configuration ne l'autorise pas
    :return: Etat d'envoi de l'e-mail
    """
    if not force and not settings.EMAIL_ENABLED:
        return None
    try:
        body_text = render_to_string(mail_text, mail_data or {})
    except:  # noqa
        body_text = Template(mail_text or "").render(Context(mail_data or {}))
    try:
        body_html = render_to_string(mail_html, mail_data or {})
    except:  # noqa
        body_html = Template(mail_html or "").render(Context(mail_data or {}))
    mail = EmailMultiAlternatives(
        from_email=mail_from,
        to=[mail_to] if isinstance(mail_to, str) else mail_to,
        cc=[mail_cc] if isinstance(mail_cc, str) else mail_cc,
        bcc=[mail_bcc] if isinstance(mail_bcc, str) else mail_bcc,
        reply_to=[mail_reply_to] if isinstance(mail_reply_to, str) else mail_reply_to,
        subject=mail_subject or "",
        body=body_text,
    )
    if mail_html:
        mail.attach_alternative(body_html, "text/html")
    for file, mimetype in files or []:
        mail.attach_file(file, mimetype=mimetype)
    return mail.send(fail_silently=fail_silently)


def merge_validation_errors(errors):
    """
    Fusionne plusieurs erreurs de validation en une seule
    :param errors: Liste d'erreurs de validation
    :return: Erreur de validation
    """
    data = {}
    for error in errors:
        if hasattr(error, "error_dict"):
            for field, messages in error.error_dict.items():
                if not isinstance(messages, ValidationError):
                    messages = ValidationError(messages)
                data.setdefault(field, []).extend(messages.error_list)
        else:
            data.setdefault(NON_FIELD_ERRORS, []).extend(error.error_list)
    return ValidationError(data)


@lru_cache(maxsize=None)
def get_all_models(*args, **kwargs):
    """
    Récupère la liste de tous les modèles chargés par nom de table
    Le résultat est mis en cache sans expiration ; appeler cache_clear() si le
    registre d'applications change, notamment avec des modèles de test isolés.
    :return: Mapping des modèles par nom de table
    """
    from django.apps import apps

    return {model._meta.db_table: model for model in apps.get_models(*args, **kwargs)}


@lru_cache(maxsize=None)
def get_all_permissions(model):
    """
    Récupère toutes les permissions d'un modèle
    :param model: Modèle
    :return: Mapping des permissions par code
    """
    from django.contrib.auth.models import Permission
    from django.contrib.contenttypes.models import ContentType

    content_type = getattr(
        model, "_content_type", None
    ) or ContentType.objects.get_for_model(model)
    return {
        permission.codename: permission
        for permission in Permission.objects.filter(content_type=content_type)
    }


def get_models_from_queryset(queryset):
    """
    Récupère tous les modèles utilisées par une requête
    Compile le SQL sans exécuter la requête, puis inspecte les alias et certains
    prefetchs. Dépend d'API internes Django et ne représente pas des permissions
    par instance. Les erreurs de compilation, dont EmptyResultSet, peuvent remonter.
    :param queryset: Requête
    :return: Ensemble des modèles
    """
    if not hasattr(queryset, "query"):
        return None
    str(queryset.query)  # Force query evaluation
    models_by_table = get_all_models()
    models = set()
    for operation in queryset.query.alias_map.values():
        model = models_by_table.get(operation.table_name)
        if not model:
            continue
        models.add(model)
    for prefetch in queryset._prefetch_related_lookups:
        if isinstance(prefetch, str):
            for related in queryset.model._meta.related_objects:
                if related.name != prefetch:
                    continue
                models.add(related.model)
                break
        else:
            models.update(get_models_from_queryset(prefetch.queryset))
    return models


def get_model_permissions(user, *models, prefix="view", bool_only=False):
    """
    Identifie les permissions de l'utilisateur sur un ou plusieurs modèles
    Utilise user.has_perm sur les codes de modèle, sans objet cible. Un mapping
    vide signifie qu'aucune permission n'a été évaluée ; bool_only le considère
    comme vrai, il ne faut donc pas l'interpréter comme une autorisation par objet.
    :param user: Instance de l'utilisateur
    :param models: Modèles
    :param prefix: Préfixe de permission
    :param bool_only: Retour uniquement vrai ou faux
    :return: Mapping des permissions par modèle
    """
    permissions = {}
    if not user or not models:
        return permissions
    for model in models:
        model_permissions = get_all_permissions(model)
        for permission_code, permission in model_permissions.items():
            if prefix and not permission_code.startswith(prefix):
                continue
            perm = f"{model._meta.app_label}.{permission_code}"
            permissions[permission_code] = user.has_perm(perm)
    if bool_only:
        if not permissions:
            return True
        return all(permissions.values())
    return permissions


# Réseau, identité HTTP et quotas de services
# -----------------------------------------

# Ordre des métadonnées de requêtes pour l'identification de l'adresse IP du client
REQUEST_META_ORDER = (
    "HTTP_X_FORWARDED_FOR",
    "X_FORWARDED_FOR",
    "HTTP_CLIENT_IP",
    "HTTP_X_REAL_IP",
    "HTTP_X_FORWARDED",
    "HTTP_X_CLUSTER_CLIENT_IP",
    "HTTP_FORWARDED_FOR",
    "HTTP_FORWARDED",
    "HTTP_VIA",
    "REMOTE_ADDR",
)

# Liste des préfixes d'adresses IP dites "privées"
PRIVATE_IP_PREFIXES = (
    "0.",  # externally non-routable
    "10.",  # class A private block
    "169.254.",  # link-local block
    "172.16.",
    "172.17.",
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",  # class B private blocks
    "192.0.2.",  # reserved for documentation and example code
    "192.168.",  # class C private block
    "255.255.255.",  # IPv4 broadcast address
    "2001:db8:",  # reserved for documentation and example code
    "fc00:",  # IPv6 private block
    "fe80:",  # link-local unicast
    "ff00:",  # IPv6 multicast
)

LOOPBACK_PREFIXES = (
    "127.",  # IPv4 loopback device
    "::1",  # IPv6 loopback device
)

NON_PUBLIC_IP_PREFIXES = PRIVATE_IP_PREFIXES + LOOPBACK_PREFIXES


def is_valid_ipv4(ip_str):
    """
    Vérifie la syntaxe d'une chaîne IPv4 avec inet_pton, sinon inet_aton.

    Le fallback exige quatre composantes pour éviter les formes IPv4 abrégées.
    Ne vérifie ni le routage ni le caractère public de l'adresse.
    """
    try:
        socket.inet_pton(socket.AF_INET, ip_str)
    except AttributeError:
        try:  # Fall-back on legacy API or False
            socket.inet_aton(ip_str)
        except (AttributeError, socket.error):
            return False
        return ip_str.count(".") == 3
    except socket.error:
        return False
    return True


def is_valid_ipv6(ip_str):
    """
    Vérifie la syntaxe d'une chaîne IPv6 avec inet_pton.

    Renvoie False sur erreur socket ; une valeur qui n'est pas une chaîne peut
    encore produire TypeError. Ne vérifie ni accessibilité ni portée réseau.
    """
    try:
        socket.inet_pton(socket.AF_INET6, ip_str)
    except socket.error:
        return False
    return True


def is_valid_ip(ip_str):
    """
    Vérifie qu'une adresse IP est valide
    """
    return is_valid_ipv4(ip_str) or is_valid_ipv6(ip_str)


def get_client_ip(request, real_ip_only=False, right_most_proxy=False):
    """
    Retourne l'adresse IP du client connecté autant que possible
    Les en-têtes proxy sont prioritaires selon REQUEST_META_ORDER. Ils ne sont
    fiables que si le proxy d'entrée les nettoie : ne pas utiliser seul pour une
    décision de sécurité. La classification privé/public utilise des préfixes
    historiques, pas un classement exhaustif de toutes les plages réservées.
    :param request: Requête HTTP Django
    :param real_ip_only: Exclure les adresses de type loopback
    :param right_most_proxy: Récupérer la dernière adresse IP parmi les adresses proxy traversées
    :return: Adresse IP v4 ou v6 du client connecté
    """
    best_matched_ip = None
    for key in REQUEST_META_ORDER:
        value = request.META.get(
            key, request.META.get(key.replace("_", "-"), "")
        ).strip()
        if value is not None and value != "":
            ips = [ip.strip().lower() for ip in value.split(",")]
            if right_most_proxy and len(ips) > 1:
                ips = reversed(ips)
            for ip_str in ips:
                if ip_str and is_valid_ip(ip_str):
                    if not ip_str.startswith(NON_PUBLIC_IP_PREFIXES):
                        return ip_str
                    if not real_ip_only:
                        loopback = LOOPBACK_PREFIXES
                        if best_matched_ip is None:
                            best_matched_ip = ip_str
                        elif best_matched_ip.startswith(
                            loopback
                        ) and not ip_str.startswith(loopback):
                            best_matched_ip = ip_str
    return best_matched_ip


def reserve_service_usage(request):
    """
    Réserve une unité du quota de service avant l'exécution d'une vue.

    Le service est identifié par ``resolver_match.view_name`` et l'utilisateur
    doit déjà être authentifié. Sous une transaction avec verrou de ligne,
    réinitialise le compteur expiré, vérifie la limite puis incrémente le
    compteur et les statistiques. Les appels admis sont comptés même si la
    vue échoue ensuite ; aucune réservation n'est remboursée automatiquement.

    Ne fait rien si SERVICE_USAGE est désactivé, si l'utilisateur est anonyme,
    si l'URL ne peut pas être résolue ou, avec SERVICE_USAGE_LIMIT_ONLY, si
    aucune ligne ServiceUsage n'existe. Une limite None signifie illimitée,
    une limite 0 interdit tout appel. Une même requête Django/DRF n'est comptée
    qu'une fois par utilisateur, service et base de données.

    Le middleware appelle cet utilitaire pour les vues Django ; les API DRF
    passent par ServiceUsagePermission, après leur propre authentification.
    Ce contrôle ne remplace pas les permissions d'accès à la ressource.

    SELECT FOR UPDATE doit être pris en charge par la base pour garantir la
    sérialisation des réservations concurrentes (pas de garantie sous SQLite).
    Sans transaction englobante, le verrou est libéré avant la vue. Avec
    ATOMIC_REQUESTS ou un atomic externe, verrou et compteur suivent la durée
    et le commit/rollback de cette transaction.

    :param request: Requête Django ou DRF, après authentification
    :raises django.core.exceptions.PermissionDenied: Quota atteint, sans incrément
    :return: None, que la réservation soit effectuée ou non nécessaire
    """
    # Imports locaux : common.models et common.settings dépendent de ces utilitaires.
    from django.core.exceptions import PermissionDenied
    from django.db import router, transaction
    from django.urls import Resolver404, resolve

    from common.models import ServiceUsage
    from common.settings import settings

    if not settings.SERVICE_USAGE:
        return
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return
    raw_request = getattr(request, "_request", request)
    match = getattr(raw_request, "resolver_match", None)
    if match is None:
        try:
            match = resolve(raw_request.path_info)
        except Resolver404:
            return
    name = match.view_name
    using = router.db_for_write(ServiceUsage)
    reservation = (using, user.pk, name)
    reserved = getattr(raw_request, "_common_service_usage_reserved", set())
    if reservation in reserved:
        return

    address = get_client_ip(raw_request) or ""
    defaults = dict(
        settings.SERVICE_USAGE_DATA.get(name) or settings.SERVICE_USAGE_DEFAULT or {}
    )
    defaults.setdefault("address", address)
    with transaction.atomic(using=using):
        usages = ServiceUsage.objects.using(using).select_for_update()
        if settings.SERVICE_USAGE_LIMIT_ONLY:
            usage = usages.filter(name=name, user=user).first()
            if usage is None:
                return
        else:
            usage, _created = usages.get_or_create(
                name=name, user=user, defaults=defaults
            )
        date = now()
        usage.reset_counter(date)
        if usage.limit is not None and usage.count >= usage.limit:
            if usage.reset_date:
                message = _(
                    "Le nombre maximal d'appels ({limit}) de ce service pour cet utilisateur "
                    "({user}) a été atteint et sera réinitialisé le {date:%d/%m/%Y %H:%M:%S}."
                ).format(limit=usage.limit, user=user, date=usage.reset_date)
            else:
                message = _(
                    "Le nombre maximal d'appels ({limit}) de ce service pour cet utilisateur "
                    "({user}) a été atteint et ne peut plus être utilisé."
                ).format(limit=usage.limit, user=user)
            raise PermissionDenied(message)
        usage.count += 1
        usage.address = address
        extra = usage.extra or {}
        addresses = extra.setdefault("addresses", {})
        address_data = addresses.setdefault(address, {})
        address_data.update(
            date=date, method=request.method, count=address_data.get("count", 0) + 1
        )
        if settings.SERVICE_USAGE_LOG_DATA:
            for method in ("GET", "POST"):
                for key, value in getattr(raw_request, method, {}).items():
                    if value:
                        data = extra.setdefault("data", {}).setdefault(key, {})
                        data.update(
                            date=date, method=method, count=data.get("count", 0) + 1
                        )
            for key in match.kwargs:
                params = extra.setdefault("params", {}).setdefault(key, {})
                params.update(
                    date=date, method=request.method, count=params.get("count", 0) + 1
                )
        usage.extra = extra
        usage.save(using=using)

    reserved.add(reservation)
    raw_request._common_service_usage_reserved = reserved


def web_to_raw_tsquery(text):
    """
    Convertit la syntaxe websearch étendue en expression tsquery brute.

    Réécrit AND/OR, les négations et les phrases citées, sans accéder à la base.
    Le résultat est destiné à SearchQuery(..., search_type='raw'), jamais à une
    concaténation SQL. PostgreSQL reste responsable de sa validation finale.
    :param text: Webseearch tsquery
    :return: Raw tsquery
    """
    text = re.sub(r"\bOR\b", "|", text)
    text = re.sub(r"\bAND\b", "&", text)
    text = re.sub(r"\B-\b", "!", text)
    text = re.sub(r"\b\+\b", "<->", text)
    patterns = chain(
        re.findall(r"(([-!]?)\'([^\']+)\')", text),
        re.findall(r"(([-!]?)\"([^\"]+)\")", text),
    )
    for outer, neg, inner in patterns:
        neg = "!" if neg else ""
        if " " not in inner:
            text = text.replace(outer, f"{neg}{inner}")
            continue
        value = " <-> ".join(inner.split())
        text = text.replace(outer, f"{neg}({value})")
    if not any(op in text for op in ("&", "|", "<->")):
        return "&".join(text.split())
    return text
