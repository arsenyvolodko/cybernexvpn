from datetime import timedelta

import factory
from django.utils.timezone import now
from factory.django import DjangoModelFactory

from nexvpn.models import Plan, Subscription


class PlanFactory(DjangoModelFactory):
    device_limit = 3
    price_month = 400
    name = factory.LazyAttribute(lambda o: f"{o.device_limit} устр.")

    class Meta:
        model = Plan
        django_get_or_create = ("device_limit",)


class SubscriptionFactory(DjangoModelFactory):
    user = factory.SubFactory("nexvpn.api.tests.factories.nex_user_factories.NexUserFactory")
    plan = factory.SubFactory(PlanFactory)
    expires_at = factory.LazyFunction(lambda: now() + timedelta(days=30))

    class Meta:
        model = Subscription
