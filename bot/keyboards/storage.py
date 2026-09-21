from abc import ABC

from bot.keyboards.button import AutoNameButtonMeta, Button


class ButtonsTextStorage(ABC):
    MAIN_MENU = "В меню"
    BACK = "Назад"

    # главное меню. Эмодзи у этих кнопок — анимированные иконки (`icon` в
    # ButtonsStorage), поэтому в самом тексте их нет.
    MY_SUBSCRIPTION = "Моя подписка"
    REFERRAL = "Реферальная программа"
    FAQ_SUPPORT = "FAQ и поддержка"

    # подключение: и первая кнопка меню, и кнопка внутри подписки — один сценарий
    CONNECT = "Подключиться"
    MY_DEVICES = "Мои устройства 📱"
    CHANGE_PLAN = "Сменить тариф 🔄"
    RENEW = "Продлить подписку 💳"
    PAY = "Перейти к оплате 💳"
    CHANGE_PLAN_FREE = "Перейти бесплатно"
    WEB_VERSION = "Веб-версия 🌐"

    # устройства
    ADD_DEVICE = "Добавить устройство ➕"
    DELETE_DEVICE = "Удалить устройство 🗑"
    DELETE_DEVICE_CONFIRM = "Да, удалить"

    # подключение
    DOWNLOADED = "Скачал ✅"
    ADD_SUBSCRIPTION = "Подключить ⚡"
    COPY_KEY = "Скопировать ключ 📋"
    CONNECT_OK = "Хорошо 👌"

    # рефералка и поддержка
    SHARE_REFERRAL = "Поделиться ссылкой 📤"
    SUPPORT = "Написать в поддержку ✍️"


class ButtonsStorage(metaclass=AutoNameButtonMeta):
    _texts = ButtonsTextStorage

    MAIN_MENU = Button()
    BACK = Button()

    # Иконки — кастомные анимированные эмодзи владельца (21.09.2026).
    MY_SUBSCRIPTION = Button(icon="5278573677900752088")  # ключик
    REFERRAL = Button(icon="5203996991054432397")  # подарок
    FAQ_SUPPORT = Button(icon="5443038326535759644")

    # Зелёная: главное действие бота, ради него сюда и приходят.
    CONNECT = Button(style="success", icon="5411590687663608498")  # молния
    MY_DEVICES = Button()
    CHANGE_PLAN = Button()
    # Оплата и продление — тоже деньги в кассу, их тоже выделяем.
    RENEW = Button(style="success")
    PAY = Button(style="success")
    CHANGE_PLAN_FREE = Button()
    WEB_VERSION = Button()

    ADD_DEVICE = Button()
    # Красные: действие необратимое, пусть отличается от соседей визуально.
    DELETE_DEVICE = Button(style="danger")
    DELETE_DEVICE_CONFIRM = Button(style="danger")

    DOWNLOADED = Button()
    # Зелёная, как «Подключиться» в меню: это то же главное действие, только
    # на шаг ближе — им сценарий подключения и заканчивается.
    ADD_SUBSCRIPTION = Button(style="success")
    COPY_KEY = Button()
    # Обычная, не зелёная: зелёным в боте выделены действия, которые чего-то
    # стоят или ведут дальше по сценарию, а это просто «прочитал».
    CONNECT_OK = Button()

    SHARE_REFERRAL = Button()
    SUPPORT = Button()


ALL_CALLBACKS: frozenset[str] = frozenset(
    value.callback for value in vars(ButtonsStorage).values() if isinstance(value, Button)
)
