# coding: utf-8
import logging
from collections import namedtuple

from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

# Message de log personnalisé
LogEntry = namedtuple("LogEntry", ["date", "level", "color", "message"])


class Logger(object):
    """
    Classe de gestion des messages d'alerte
    """

    KEY_DEBUG = "_DEBUG"
    KEY_INFO = "_INFO"
    KEY_WARNING = "_WARNING"
    KEY_ERROR = "_ERROR"
    KEY_CRITICAL = "_CRITICAL"
    # Clés de contextes
    CONTEXT_KEYS = {
        logging.DEBUG: KEY_DEBUG,
        logging.INFO: KEY_INFO,
        logging.WARNING: KEY_WARNING,
        logging.ERROR: KEY_ERROR,
        logging.CRITICAL: KEY_CRITICAL,
    }
    # Couleurs des niveaux d'alerte dans le logger
    COLORS = {
        logging.DEBUG: "blue",
        logging.INFO: "green",
        logging.WARNING: "orange",
        logging.ERROR: "red",
        logging.CRITICAL: "purple",
    }

    def __init__(self, name=None, keep_messages=False):
        self.logger = logging.getLogger(name or __name__)
        self.entries = []
        self.keep_messages = keep_messages

    def _log(self, level, message, _context=None, *args, **kwargs):
        # Si le message est une liste, les fragments sont journalisés à la suite
        if isinstance(message, list):
            messages = []
            for msg in message:
                messages.append(self._log(level, msg, _context, *args, **kwargs))
            return messages

        # Formatage du message d'erreur
        message = str(message)
        try:
            message = message.format(*args, **kwargs)
        except (IndexError, KeyError):
            self.logger.warning(_("Le message n'est pas correctement formaté."))

        # Conservation du message si demandé
        if self.keep_messages:
            logentry = LogEntry(
                date=now(), level=logging.getLevelName(level), color=Logger.COLORS[level], message=message
            )
            self.entries.append(logentry)

        # Ajout du message dans le contexte cible si demandé
        if _context and isinstance(_context, dict):
            key = Logger.CONTEXT_KEYS.get(level)
            section = _context[key] = _context.get(key, [])
            if message not in section:
                section.append(message)
                self.logger.log(level, message)
        else:
            self.logger.log(level, message)
        return message

    @property
    def messages(self):
        return [logentry.message for logentry in self.entries]

    def debug(self, message, *args, **kwargs):
        return self._log(logging.DEBUG, message, *args, **kwargs)

    def info(self, message, *args, **kwargs):
        return self._log(logging.INFO, message, *args, **kwargs)

    def warn(self, message, *args, **kwargs):
        self.warning(message, *args, **kwargs)

    def warning(self, message, *args, **kwargs):
        return self._log(logging.WARNING, message, *args, **kwargs)

    def error(self, message, *args, **kwargs):
        return self._log(logging.ERROR, message, *args, **kwargs)

    def critical(self, message, *args, **kwargs):
        return self._log(logging.CRITICAL, message, *args, **kwargs)

    def context_debug(self, context, message, *args, **kwargs):
        return self._log(logging.DEBUG, message, *args, _context=context, **kwargs)

    def context_info(self, context, message, *args, **kwargs):
        return self._log(logging.INFO, message, *args, _context=context, **kwargs)

    def context_warning(self, context, message, *args, **kwargs):
        return self._log(logging.WARNING, message, *args, _context=context, **kwargs)

    def context_error(self, context, message, *args, **kwargs):
        return self._log(logging.ERROR, message, *args, _context=context, **kwargs)

    def context_critical(self, context, message, *args, **kwargs):
        return self._log(logging.CRITICAL, message, *args, _context=context, **kwargs)


class InternalError(Exception):
    """
    Classe d'exception interne
    """

    def __init__(self, message, *args, **kwargs):
        self.message = str(message)
        self.args = args
        self.kwargs = kwargs

    def __str__(self):
        return self.message

    def __repr__(self):
        return self.message
