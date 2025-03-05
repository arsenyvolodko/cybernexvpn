import enum
from typing import Any

from pydantic import Field, HttpUrl

from cybernexvpn.base_model import BaseModel


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

