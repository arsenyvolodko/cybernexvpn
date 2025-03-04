import enum
from typing import Any

from pydantic import Field, HttpUrl

from cybernexvpn.base_model import BaseModel
from nexvpn.enums import SubscriptionUpdateStatusEnum


class Method(str, enum.Enum):
    POST = "POST"
    DELETE = "DELETE"


class Request(BaseModel):
    url: HttpUrl | str
    method: Method = Method.POST
    headers: dict[str, Any] = Field(default_factory=dict)

    params: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    json_: dict[str, Any] | None = Field(None, alias="json")


class ConfigSchema(BaseModel):
    url: HttpUrl | str
    api_key: str
    timeout: int = 10


class CreateClientRequest(BaseModel):
    ip: str


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
