from cybernexvpn.base_model import BaseModel


class WgClientRequest(BaseModel):
    pass


class CreateWgClientRequest(WgClientRequest):
    ip: str
    public_key: str


class CreateClientsRequestWg(WgClientRequest):
    clients: list[CreateWgClientRequest]


class DeleteWgClientRequest(WgClientRequest):
    public_key: str


class DeleteClientsRequestWg(WgClientRequest):
    clients: list[DeleteWgClientRequest]
