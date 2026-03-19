from cybernexvpn.base_model import BaseModel


class TelemtClientRequest(BaseModel):
    pass


class CreateTelemtClientRequest(TelemtClientRequest):
    telegram_id: str
    secret: str


class CreateTelemtClientsRequest(TelemtClientRequest):
    clients: list[CreateTelemtClientRequest]


class DeleteTelemtClientRequest(TelemtClientRequest):
    telegram_id: str


class DeleteTelemtClientsRequest(TelemtClientRequest):
    clients: list[DeleteTelemtClientRequest]
