# coding: utf-8
import logging

from django.db.migrations.operations.base import Operation
from django.utils.translation import gettext_lazy as _


logger = logging.getLogger(__name__)


class CreateFunctionUnaccent(Operation):
    """
    Création de la fonction f_unaccent dans la base de données PostgreSQL afin de pouvoir créer des index avec unaccent
    """
    reversible = True

    def state_forwards(self, app_label, state):
        return

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        # Applicable uniquement sur une base de données PostgreSQL
        if schema_editor.connection.vendor != 'postgresql':
            logger.error(_("L'opération ne peut s'exécuter que sur PostgreSQL."))
            return

        # Vérification de l'existence de l'extension "unaccent"
        cursor = schema_editor.connection.cursor()
        cursor.execute("SELECT COUNT(extname) FROM pg_extension WHERE extname = 'unaccent';")
        if not cursor.fetchall()[0][0]:
            logger.error(_("L'extension 'unaccent' n'est pas installée sur la base de données."))
            return

        query = "CREATE OR REPLACE FUNCTION F_UNACCENT(text) " \
                "RETURNS text AS " \
                "$func$ " \
                "SELECT public.unaccent('public.unaccent', UPPER($1)) " \
                "$func$ LANGUAGE sql IMMUTABLE;"
        schema_editor.execute(query)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        # Applicable uniquement sur une base de données PostgreSQL
        if schema_editor.connection.vendor != 'postgresql':
            logger.error(_("L'opération ne peut s'exécuter que sur PostgreSQL."))
            return

        schema_editor.execute("DROP FUNCTION IF EXISTS F_UNACCENT(text);")


class CreateIndexUnaccent(Operation):
    """
    Création d'index unaccent sur un ensemble des champs d'un modèle
    """
    reversible = True

    def __init__(self, model_name, fields):
        self.model_name = model_name
        self.fields = fields

    def state_forwards(self, app_label, state):
        return

    def database_forwards(self, app_label, schema_editor, from_state, to_state):

        # Applicable uniquement sur une base de données PostgreSQL
        if schema_editor.connection.vendor != 'postgresql':
            logger.error(_("L'opération ne peut s'exécuter que sur PostgreSQL."))
            return

        # Vérification de l'extension "unaccent"
        cursor = schema_editor.connection.cursor()
        cursor.execute("SELECT COUNT(extname) FROM pg_extension WHERE extname = 'unaccent';")
        if not cursor.fetchall()[0][0]:
            logger.error(_("L'extension 'unaccent' n'est pas installée sur la base de données."))
            return

        # Vérification que la fonction "f_unaccent" est bien créée
        cursor.execute("SELECT COUNT(proname) FROM pg_proc WHERE proname = 'f_unaccent';")
        if not cursor.fetchall()[0][0]:
            CreateFunctionUnaccent().database_forwards(app_label, schema_editor, from_state, to_state)

        # Template de la requête de création d'index
        query = "CREATE INDEX IF NOT EXISTS {index_name}_{method} ON {db_table} USING {method} ({fields});"

        # Template de création des méthodes d'index sur les champs
        method_sql = "UPPER(F_UNACCENT({field})) {operator_class}, F_UNACCENT({field}) {operator_class}"

        # Récupération du modèle
        model = to_state.apps.get_model(app_label, self.model_name)

        # Contrôle que l'extension "pg_trgm" est installée
        pg_trgm_installed = True
        cursor.execute("SELECT COUNT(extname) FROM pg_extension WHERE extname = 'pg_trgm';")
        if not cursor.fetchall()[0][0]:
            logger.warning(_("L'extension 'pg_trgm' n'est pas installée sur la base de données, "
                             "l'index de type GIN ne sera donc pas créé et seul l'index de type BTREE sera créé."))
            pg_trgm_installed = False

        for fields in self.fields:
            # Création du nom de l'index
            index_name = schema_editor._create_index_name(model._meta.db_table, fields, suffix='_unaccent')

            # Ajout de la classe d'opérateur pour la méthode BTREE
            fields_btree = ", ".join(
                [method_sql.format(field=field, operator_class='varchar_pattern_ops') for field in fields]
            )
            # Création de l'index BTREE
            schema_editor.execute(
                query.format(index_name=index_name, db_table=model._meta.db_table, fields=fields_btree, method='btree')
            )

            if pg_trgm_installed:
                # Ajout de la classe d'opérateur pour la méthode GIN
                fields_gin = ", ".join(
                    [method_sql.format(field=field, operator_class='gin_trgm_ops') for field in fields]
                )
                # Création de l'index GIN
                schema_editor.execute(
                    query.format(index_name=index_name, db_table=model._meta.db_table, fields=fields_gin, method='gin')
                )

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        # Applicable uniquement sur une base de données PostgreSQL
        if schema_editor.connection.vendor != 'postgresql':
            logger.error(_("L'opération ne peut s'exécuter que sur PostgreSQL."))
            return

        # Template de requête de suppression d'index
        query = "DROP INDEX IF EXISTS {index_name}_{method};"

        # Récupération du modèle
        model = to_state.apps.get_model(app_label, self.model_name)

        for fields in self.fields:
            # Création du nom de l'index
            index_name = schema_editor._create_index_name(model._meta.db_table, fields, suffix='_unaccent')

            # Suppression des index de la base de données
            schema_editor.execute(query.format(index_name=index_name, method='gin'))
            schema_editor.execute(query.format(index_name=index_name, method='btree'))
