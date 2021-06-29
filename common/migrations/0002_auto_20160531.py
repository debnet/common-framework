# -*- coding: utf-8 -*-
# Generated by Django 1.9.6 on 2016-05-03 16:17
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0001_initial"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="webhook",
            options={"verbose_name": "webhook", "verbose_name_plural": "webhooks"},
        ),
        migrations.AddField(
            model_name="webhook",
            name="method",
            field=models.CharField(
                choices=[("post", "POST"), ("put", "PUT"), ("patch", "PATCH")],
                default="post",
                max_length=5,
                verbose_name="method",
            ),
        ),
        migrations.AddField(
            model_name="webhook",
            name="timeout",
            field=models.PositiveSmallIntegerField(default=30, verbose_name="délai d'attente"),
        ),
        migrations.AddField(
            model_name="webhook",
            name="retries",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="tentatives"),
        ),
        migrations.AddField(
            model_name="webhook",
            name="delay",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="délai entre tentatives"),
        ),
        migrations.AlterField(
            model_name="webhook",
            name="token",
            field=models.TextField(blank=True, null=True, verbose_name="token"),
        ),
    ]
