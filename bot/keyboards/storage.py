from abc import ABC

from bot.keyboards.button import AutoNameButtonMeta, Button


class ButtonsTextStorage(ABC):
    MAIN_MENU = "В меню"
    BACK = "Назад"

    # главное меню
    MY_SUBSCRIPTION = "Моя подписка 🔑"
    REFERRAL = "Реферальная программа 🎁"
    FAQ_SUPPORT = "FAQ и поддержка 💬"

    # подключение: и первая кнопка меню, и кнопка внутри подписки — один сценарий
    CONNECT = "Подключиться ⚡"
    MY_DEVICES = "Мои устройства 📱"
    CHANGE_PLAN = "Сменить тариф 🔄"
    RENEW = "Продлить подписку 💳"
    PAY = "Перейти к оплате 💳"
    CHANGE_PLAN_FREE = "Перейти бесплатно"
    CHANGE_PLAN_PAY = "Доплатить и получить месяц"
    WEB_VERSION = "Веб-версия 🌐"

    # устройства
    ADD_DEVICE = "Добавить устройство ➕"
    DELETE_DEVICE = "Удалить устройство 🗑"
    DELETE_DEVICE_CONFIRM = "Да, удалить"

    # подключение
    DOWNLOADED = "Скачал ✅"
    ADD_SUBSCRIPTION = "Добавить подписку ⚡"
    COPY_KEY = "Скопировать ключ 📋"

    # рефералка и поддержка
    SHARE_REFERRAL = "Поделиться ссылкой 📤"
    SUPPORT = "Написать в поддержку ✍️"


class ButtonsStorage(metaclass=AutoNameButtonMeta):
    _texts = ButtonsTextStorage

    MAIN_MENU = Button()
    BACK = Button()

    MY_SUBSCRIPTION = Button()
    REFERRAL = Button()
    FAQ_SUPPORT = Button()

    # Зелёная: главное действие бота, ради него сюда и приходят.
    CONNECT = Button(style="success")
    MY_DEVICES = Button()
    CHANGE_PLAN = Button()
    # Оплата и продление — тоже деньги в кассу, их тоже выделяем.
    RENEW = Button(style="success")
    PAY = Button(style="success")
    CHANGE_PLAN_FREE = Button()
    CHANGE_PLAN_PAY = Button()
    WEB_VERSION = Button()

    ADD_DEVICE = Button()
    # Красные: действие необратимое, пусть отличается от соседей визуально.
    DELETE_DEVICE = Button(style="danger")
    DELETE_DEVICE_CONFIRM = Button(style="danger")

    DOWNLOADED = Button()
    ADD_SUBSCRIPTION = Button()
    COPY_KEY = Button()

    SHARE_REFERRAL = Button()
    SUPPORT = Button()


ALL_CALLBACKS: frozenset[str] = frozenset(
    value.callback for value in vars(ButtonsStorage).values() if isinstance(value, Button)
)
