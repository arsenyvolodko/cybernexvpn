from cybernexvpn.base_model import BaseModel


class ClientRequest(BaseModel):
    pass


class CreateClientRequest(ClientRequest):
    ip: str
    public_key: str


class CreateClientsRequest(ClientRequest):
    clients: list[CreateClientRequest]


class DeleteClientRequest(ClientRequest):
    public_key: str


class DeleteClientsRequest(ClientRequest):
    clients: list[DeleteClientRequest]
