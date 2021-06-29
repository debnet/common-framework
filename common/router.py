# coding: utf-8
# https://github.com/ambitioninc/django-dynamic-db-router
import sys
import threading
from copy import deepcopy
from functools import wraps

from django.db import DEFAULT_DB_ALIAS, connections

from common.utils import short_identifier

# Local thread for sharing database override
# TODO: verify local thread is not shared between asynchronous tasks or different users
local_thread = threading.local()


class DatabaseOverrideRouter(object):
    """
    Database router which route read and write queries through user-defined database connections
    """

    def db_for_read(self, model, **hints):
        # Disabled during tests
        if sys.argv[1:2] == ["test"]:
            return DEFAULT_DB_ALIAS
        return getattr(local_thread, "db_read_override", [DEFAULT_DB_ALIAS])[-1]

    def db_for_write(self, model, **hints):
        # Disabled during tests
        if sys.argv[1:2] == ["test"]:
            return DEFAULT_DB_ALIAS
        return getattr(local_thread, "db_write_override", [DEFAULT_DB_ALIAS])[-1]

    def allow_relation(self, *args, **kwargs):
        return True

    def allow_syncdb(self, *args, **kwargs):
        return None

    def allow_migrate(self, *args, **kwargs):
        return None


class database_override:
    """
    A decorator and context manager to do queries on a given database.

    :type using: str or dict, optional
    :param using: The database to run queries on.
        A string will route through the matching database in ``django.conf.settings.DATABASES``.
        A dictionary will set up a connection with the given configuration and route queries to it.
        If None, the ``'default'`` database connection will be used.

    :type read: bool, optional
    :param read: Controls whether database reads will route through the provided database connection.
        If ``False``, reads will route through the ``'default'`` database connection. Defaults to ``True``.

    :type write: bool, optional
    :param write: Controls whether database writes will route to the provided database connection.
        If ``False``, writes will route through the ``'default'`` database connection. Defaults to ``False``.

    :type options: dict, optional
    :param options: Custom options to apply on the given or default database connection.
        Could be defined through the ``using`` parameter as dictionary for new database connection,
        but can alter existing connection options if ``using`` is either a string or None.

    Usage as a context manager:

    .. code-block:: python
        from my_django_app.utils import tricky_query
        with override_database('database_1'):
            results = tricky_query()

    Usage as a decorator:

    .. code-block:: python
        from my_django_app.models import Account
        @override_database('database_2')
        def lowest_id_account():
            Account.objects.order_by('-id')[0]

    Used with a configuration dictionary:

    .. code-block:: python
        db_config = {'ENGINE': 'django.db.backends.sqlite3', 'NAME': 'path/to/mydatabase.db'}
        with override_database(db_config):
            # Run queries
    """

    def __init__(self, using=None, read=True, write=False, **options):
        self.read = read
        self.write = write
        self.database_alias = None

        if not using and options:
            using = deepcopy(connections.databases.get(DEFAULT_DB_ALIAS))
        if isinstance(using, str):
            self.using = using
        elif isinstance(using, dict):
            if options:
                database_options = using["OPTIONS"] = using.get("OPTIONS", {})
                database_options.update(**options)
            self.database_alias = short_identifier()
            connections.databases[self.database_alias] = using
            self.using = self.database_alias

    def __enter__(self):
        if not hasattr(local_thread, "db_read_override"):
            local_thread.db_read_override = [DEFAULT_DB_ALIAS]
        if not hasattr(local_thread, "db_write_override"):
            local_thread.db_write_override = [DEFAULT_DB_ALIAS]
        read_db = self.using if self.read else local_thread.db_read_override[-1]
        write_db = self.using if self.write else local_thread.db_write_override[-1]
        local_thread.db_read_override.append(read_db)
        local_thread.db_write_override.append(write_db)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        local_thread.db_read_override.pop()
        local_thread.db_write_override.pop()
        if self.database_alias:
            connections[self.database_alias].close()
            del connections.databases[self.database_alias]

    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with self:
                return func(*args, **kwargs)

        return wrapper
