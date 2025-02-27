from nexvpn.api.exceptions.base_client_error import BaseClientError
from nexvpn.api.exceptions.enums.error_message_enum import ErrorMessageEnum


class NotEnoughMoneyError(BaseClientError):

    def __init__(self, message: str = ErrorMessageEnum.NOT_ENOUGH_MONEY_ERROR_MESSAGE.value) -> None:
        self.message = message
        super().__init__(message)
