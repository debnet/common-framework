# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import common.fields


class Migration(migrations.Migration):

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auth", "0006_require_contenttypes_0002"),
    ]

    operations = [
        migrations.CreateModel(
            name="Global",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("object_id", models.PositiveIntegerField(verbose_name="identifiant", editable=False)),
                ("object_uid", models.UUIDField(unique=True, verbose_name="UUID", editable=False)),
                (
                    "content_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="contenttypes.ContentType",
                        verbose_name="type d'entité",
                        editable=False,
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "globales",
                "verbose_name": "globale",
            },
        ),
        migrations.CreateModel(
            name="GroupMetaData",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("data", common.fields.JsonField(null=True, verbose_name="données", blank=True)),
                (
                    "group",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="metadata",
                        verbose_name="groupe",
                        to="auth.Group",
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "métadonnées de groupes",
                "verbose_name": "métadonnées de groupe",
            },
        ),
        migrations.CreateModel(
            name="History",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("creation_date", models.DateTimeField(auto_now_add=True, verbose_name="date")),
                (
                    "restoration_date",
                    models.DateTimeField(null=True, editable=False, verbose_name="dernière restauration", blank=True),
                ),
                ("restored", models.BooleanField(null=True, editable=False, verbose_name="restauré")),
                ("data", common.fields.JsonField(null=True, editable=False, verbose_name="données", blank=True)),
                ("data_size", models.PositiveIntegerField(verbose_name="taille données", editable=False)),
                (
                    "status",
                    models.CharField(
                        max_length=1,
                        choices=[
                            ("C", "Création"),
                            ("U", "Modification"),
                            ("D", "Suppression"),
                            ("R", "Restauration"),
                            ("M", "Many-to-many"),
                        ],
                        verbose_name="statut",
                        editable=False,
                    ),
                ),
                ("object_id", models.PositiveIntegerField(verbose_name="identifiant", editable=False)),
                ("object_uid", models.UUIDField(db_index=True, verbose_name="UUID", editable=False)),
                ("object_str", models.TextField(verbose_name="entité", editable=False)),
                ("reason", models.TextField(null=True, editable=False, verbose_name="motif", blank=True)),
                ("admin", models.BooleanField(default=False, editable=False, verbose_name="admin")),
                (
                    "content_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        null=True,
                        verbose_name="type d'entité",
                        to="contenttypes.ContentType",
                        blank=True,
                        editable=False,
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.SET_NULL,
                        null=True,
                        verbose_name="utilisateur",
                        to=settings.AUTH_USER_MODEL,
                        blank=True,
                        editable=False,
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "historiques",
                "ordering": ["-creation_date"],
                "verbose_name": "historique",
            },
        ),
        migrations.CreateModel(
            name="HistoryField",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("creation_date", models.DateTimeField(auto_now_add=True, verbose_name="date")),
                (
                    "restoration_date",
                    models.DateTimeField(null=True, editable=False, verbose_name="dernière restauration", blank=True),
                ),
                ("restored", models.BooleanField(null=True, editable=False, verbose_name="restauré")),
                ("data", common.fields.JsonField(null=True, editable=False, verbose_name="données", blank=True)),
                ("data_size", models.PositiveIntegerField(verbose_name="taille données", editable=False)),
                (
                    "field_name",
                    models.CharField(db_index=True, max_length=100, verbose_name="nom du champ", editable=False),
                ),
                ("old_value", models.TextField(null=True, editable=False, verbose_name="ancienne valeur", blank=True)),
                ("new_value", models.TextField(null=True, editable=False, verbose_name="nouvelle valeur", blank=True)),
                (
                    "status_m2m",
                    models.CharField(
                        null=True,
                        max_length=1,
                        verbose_name="statut M2M",
                        editable=False,
                        choices=[("C", "Purge"), ("A", "Ajout"), ("R", "Suppression")],
                        blank=True,
                    ),
                ),
                (
                    "history",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="common.History",
                        verbose_name="historique",
                        editable=False,
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "champs modifiés",
                "ordering": ["-creation_date"],
                "verbose_name": "champ modifié",
            },
        ),
        migrations.CreateModel(
            name="MetaData",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("object_id", models.PositiveIntegerField(verbose_name="identifiant")),
                ("key", models.CharField(max_length=100, verbose_name="clé")),
                ("value", common.fields.JsonField(null=True, verbose_name="valeur", blank=True)),
                ("creation_date", models.DateTimeField(auto_now_add=True, verbose_name="date de création")),
                ("modification_date", models.DateTimeField(auto_now=True, verbose_name="date de modification")),
                (
                    "deletion_date",
                    models.DateTimeField(null=True, db_index=True, verbose_name="date de suppression", blank=True),
                ),
                (
                    "content_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="contenttypes.ContentType",
                        verbose_name="type d'entité",
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "métadonnées",
                "verbose_name": "métadonnée",
            },
        ),
        migrations.CreateModel(
            name="UserMetaData",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("data", common.fields.JsonField(null=True, verbose_name="données", blank=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="metadata",
                        verbose_name="utilisateur",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "métadonnées d'utilisateurs",
                "verbose_name": "métadonnées d'utilisateur",
            },
        ),
        migrations.CreateModel(
            name="Webhook",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(null=True, max_length=100, verbose_name="nom", blank=True)),
                ("url", models.URLField(verbose_name="url")),
                (
                    "format",
                    models.CharField(
                        default="json",
                        max_length=4,
                        choices=[("json", "JSON"), ("xml", "XML"), ("yaml", "YAML")],
                        verbose_name="format",
                    ),
                ),
                (
                    "authorization",
                    models.CharField(
                        null=True,
                        max_length=6,
                        choices=[
                            ("Basic", "Basic"),
                            ("Digest", "Digest"),
                            ("Token", "Token"),
                            ("Bearer", "Bearer"),
                            ("JWT", "JWT"),
                        ],
                        verbose_name="authentification",
                        blank=True,
                    ),
                ),
                ("token", models.CharField(null=True, max_length=100, verbose_name="token", blank=True)),
                ("is_create", models.BooleanField(default=True, verbose_name="création")),
                ("is_update", models.BooleanField(default=True, verbose_name="modification")),
                ("is_delete", models.BooleanField(default=True, verbose_name="suppression")),
                ("is_restore", models.BooleanField(default=True, verbose_name="restauration")),
                ("is_m2m", models.BooleanField(default=True, verbose_name="many-to-many")),
                ("types", models.ManyToManyField(to="contenttypes.ContentType", verbose_name="types", blank=True)),
            ],
            options={
                "verbose_name_plural": "web hooks",
                "verbose_name": "web hook",
            },
        ),
        migrations.AlterUniqueTogether(
            name="metadata",
            unique_together=set([("content_type", "object_id", "key")]),
        ),
        migrations.AlterIndexTogether(
            name="history",
            index_together=set([("content_type", "object_id")]),
        ),
        migrations.AlterUniqueTogether(
            name="global",
            unique_together=set([("content_type", "object_id")]),
        ),
    ]
