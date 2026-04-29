from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nexvpn', '0018_nexuser_token'),
    ]

    operations = [
        migrations.AddField(
            model_name='nexuser',
            name='email',
            field=models.EmailField(blank=True, max_length=254, null=True),
        ),
    ]