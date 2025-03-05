from cybernexvpn.base_model import BaseModel
from nexvpn.api_clients.tg_bot_api_client.enums import SubscriptionUpdateStatusEnum


class UpdateSubscriptionClient(BaseModel):
    name: str
    subscription_update: SubscriptionUpdateStatusEnum


class UserSubscriptionUpdates(BaseModel):
    user: int
    renewed: list[str]
    stopped_due_to_lack_of_funds: list[str]
    stopped_due_to_offed_auto_renew: list[str]
    deleted: list[str]


class SubscriptionUpdates(BaseModel):
    is_reminder: bool
    updates: list[UserSubscriptionUpdates]
