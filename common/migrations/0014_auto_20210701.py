# Generated by Django 3.2.5 on 2021-07-01 13:08

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0013_auto_20210620"),
    ]

    operations = [
        migrations.AlterField(
            model_name="history",
            name="creation_date",
            field=models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="date"),
        ),
        migrations.AlterField(
            model_name="historyfield",
            name="creation_date",
            field=models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="date"),
        ),
    ]