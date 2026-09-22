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
    MY_DEVICES = "Мои устройства"
    CHANGE_PLAN = "Сменить тариф"
    RENEW = "Продлить подписку"  # эмодзи — анимированная иконка
    PAY = "Перейти к оплате"
    CHANGE_PLAN_FREE = "Перейти бесплатно"
    WEB_VERSION = "Веб-версия"

    # устройства
    ADD_DEVICE = "Добавить устройство"
    DELETE_DEVICE = "Удалить устройство"
    DELETE_DEVICE_CONFIRM = "Да, удалить"

    # подключение
    DOWNLOADED = "Скачал"
    ADD_SUBSCRIPTION = "Подключить ⚡"
    COPY_KEY = "Скопировать ключ"
    CONNECT_OK = "Хорошо"

    # промокоды и скидки
    PROMO = "Промокоды и скидки"
    ENTER_PROMO = "Уже есть промокод"

    # рефералка и поддержка
    SHARE_REFERRAL = "Поделиться ссылкой"  # эмодзи — анимированная иконка
    COPY_REFERRAL = "Скопировать ссылку"  # эмодзи — анимированная иконка
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
    MY_DEVICES = Button(icon="5819062970998590994")
    CHANGE_PLAN = Button(icon="6012661228910939253")
    # Оплата и продление — тоже деньги в кассу, их тоже выделяем.
    RENEW = Button(style="success", icon="5267300544094948794")
    PAY = Button(style="success", icon="5267300544094948794")
    CHANGE_PLAN_FREE = Button()
    WEB_VERSION = Button(icon="5447410659077661506")

    ADD_DEVICE = Button(icon="5393194986252542669")
    # Красные: действие необратимое, пусть отличается от соседей визуально.
    DELETE_DEVICE = Button(style="danger", icon="5445267414562389170")
    DELETE_DEVICE_CONFIRM = Button(style="danger")

    DOWNLOADED = Button(icon="5206607081334906820")
    # Зелёная, как «Подключиться» в меню: это то же главное действие, только
    # на шаг ближе — им сценарий подключения и заканчивается.
    ADD_SUBSCRIPTION = Button(style="success")
    COPY_KEY = Button(icon="5341492148468465410")
    # Обычная, не зелёная: зелёным в боте выделены действия, которые чего-то
    # стоят или ведут дальше по сценарию, а это просто «прочитал».
    CONNECT_OK = Button(icon="5287478236027040039")

    SHARE_REFERRAL = Button(icon="5190859184312167965")
    PROMO = Button(icon="5240228673738527951")
    ENTER_PROMO = Button()
    COPY_REFERRAL = Button(icon="5413422358071372326")
    SUPPORT = Button()


ALL_CALLBACKS: frozenset[str] = frozenset(
    value.callback for value in vars(ButtonsStorage).values() if isinstance(value, Button)
)
