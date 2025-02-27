import enum
from typing import Any, Self

from pydantic import ConfigDict, Field, HttpUrl, SecretStr, field_serializer, model_validator
from pydantic import BaseModel as PydanticBaseModel


class Method(str, enum.Enum):
    POST = "POST"
    DELETE = "DELETE"


class BaseModel(PydanticBaseModel):
    class Config:
        arbitrary_types_allowed = True


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
