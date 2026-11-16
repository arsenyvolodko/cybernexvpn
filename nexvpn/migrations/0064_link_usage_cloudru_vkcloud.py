from django.db import migrations, models


class Migration(migrations.Migration):
    """Новые российские входы Cloud.ru (ru3) и VK Cloud (vk1) в выборе значений поля entry; схема БД не меняется."""

    dependencies = [("nexvpn", "0063_node_certificate")]

    operations = [
        migrations.AlterField(
            model_name="linkusageday",
            name="entry",
            field=models.CharField(
                choices=[
                    ("atlex", "Atlex (Россия)"), ("timeweb", "Timeweb (Россия)"),
                    ("eurobyte_a", "EuroByte (Россия)"), ("eurobyte_b", "EuroByte 2 (Россия)"),
                    ("cloudru", "Cloud.ru (Россия)"), ("vkcloud", "VK Cloud (Россия)"),
                    ("direct", "Напрямую, без прослойки"),
                ],
                max_length=16, verbose_name="вход",
            ),
        ),
    ]
