from enum import Enum


class ErrorMessageEnum(Enum):
    NO_FREE_ENDPOINTS_ERROR_MESSAGE = ("К сожалению, на сервере временно закончились свободные IP адреса((\n"
                                       "Попробуйте позже или выберите другой сервер.")

    SOMETHING_WENT_WRONG_ERROR_MESSAGE = "Что-то пошло не так((\nПожалуйста попробуйте еще раз."

    NOT_ENOUGH_MONEY_ERROR_MESSAGE = "На вашем счете недостаточно средств для выполнения этого действия."
    NOT_ENOUGH_MONEY_TO_ADD_CLIENT_ERROR_MESSAGE = "На вашем счете недостаточно средств для добавления устройства."
    NOT_ENOUGH_MONEY_TO_REACTIVATE_CLIENT_ERROR_MESSAGE = "На вашем счете недостаточно средств для возобновления подписки."

    CLIENT_IS_ALREADY_ACTIVE_ERROR_MESSAGE = "Данное устройство уже активно."
