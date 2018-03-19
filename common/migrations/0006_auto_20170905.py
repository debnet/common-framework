# -*- coding: utf-8 -*-
# Generated by Django 1.11.4 on 2017-09-05 12:15
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('common', '0005_auto_20170214'),
    ]

    operations = [
        migrations.AlterField(
            model_name='history',
            name='object_uid',
            field=models.UUIDField(editable=False, verbose_name='UUID'),
        ),
        migrations.AlterField(
            model_name='historyfield',
            name='field_name',
            field=models.CharField(editable=False, max_length=100, verbose_name='nom du champ'),
        ),
        migrations.AlterField(
            model_name='metadata',
            name='deletion_date',
            field=models.DateTimeField(blank=True, null=True, verbose_name='date de suppression'),
        ),
        migrations.AlterField(
            model_name='metadata',
            name='key',
            field=models.CharField(max_length=100, verbose_name='clé'),
        ),
        migrations.AlterIndexTogether(
            name='metadata',
            index_together=set([('content_type', 'object_id', 'key', 'deletion_date')]),
        ),
    ]