# Generated by Django 5.1.6 on 2025-03-04 11:49

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nexvpn", "0008_remove_payment_user"),
    ]

    operations = [
        migrations.CreateModel(
            name="PromoCode",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("value", models.IntegerField()),
                ("name", models.CharField(max_length=31, unique=True)),
                ("active", models.BooleanField(default=True)),
                ("public_access", models.BooleanField(default=False)),
            ],
        ),
        migrations.CreateModel(
            name="AllowedUserPromoCode",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="nexvpn.nexuser"
                    ),
                ),
                (
                    "promo_code",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="nexvpn.promocode",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "promo_code"),
                        name="unique_allowed_user_promo_code",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="UsedPromoCode",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "promo_code",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="nexvpn.promocode",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="nexvpn.nexuser"
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "promo_code"),
                        name="unique_used_user_promo_code",
                    )
                ],
            },
        ),
    ]
