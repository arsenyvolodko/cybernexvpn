"""Подключение: тип устройства → установка → добавление подписки.

Две точки входа — кнопка в главном меню и «Добавить устройство» в списке
устройств. Обе ведут в один сценарий, чтобы поведение не разъезжалось.
"""

import logging
from urllib.parse import quote

from aiogram import F, Router
from aiogram.types import CallbackQuery
from django.conf import settings

from bot import texts
from bot.apps_catalog import Platform, get_guide
from bot.handlers.common import render
from bot.keyboards import ButtonsStorage, keyboards
from bot.keyboards.factories import ConnectCallback
from bot.device_watch import start_watching
from bot.services import create_connection_watch, get_subscription_view, list_devices
from nexvpn.models import NexUser

logger = logging.getLogger(__name__)

router = Router(name="connect")


def build_connect_url(platform: Platform, subscription_url: str) -> str:
    """Ссылка, по которой приложение откроется с уже добавленной подпиской.

    Telegram пускает в inline-кнопки только http(s), поэтому `happ://add/...`
    туда не положить — нужна промежуточная страница на своём домене, которая
    покажет кнопки с этими схемами. Какое приложение у человека, мы не знаем,
    поэтому страница предлагает и Happ, и INCY.

    Пока страница не выкачена, ведём на штатную страницу подписки Remnawave:
    там тоже есть кнопки приложений, просто на одно касание больше.
    """
    bridge = settings.CONNECT_BRIDGE_URL
    if not bridge:
        return subscription_url
    # Платформу передаём, чтобы страница показала нужное приложение первым:
    # на iPhone это INCY, на Windows Happ вообще единственный вариант.
    return f"{bridge}?sub={quote(subscription_url, safe='')}&platform={platform.value}"


async def connect_screen(user: NexUser):
    """Что показать по «Подключиться»: текст и клавиатура.

    Вынесено из хендлера, потому что тот же экран открывает кнопка из рассылки,
    а показывает она его иначе — отдельным сообщением, не трогая объявление.
    Разъехаться этим двум путям нельзя: человек должен видеть одно и то же.
    """
    view = await get_subscription_view(user)

    if not view.exists:
        return texts.SUBSCRIPTION_NONE, keyboards.only_back()
    if not view.is_active:
        return texts.CONNECT_NEEDS_SUBSCRIPTION, keyboards.only_back()
    if not view.can_add_device:
        # Панель всё равно откажет новому устройству — честнее сказать заранее.
        return texts.CONNECT_NO_SLOTS.format(limit=view.device_limit), keyboards.only_back()
    return texts.CONNECT_CHOOSE_PLATFORM, keyboards.platforms()


@router.callback_query(F.data.in_({ButtonsStorage.CONNECT.callback, ButtonsStorage.ADD_DEVICE.callback}))
async def handle_connect(call: CallbackQuery, user: NexUser) -> None:
    await call.answer()
    text, keyboard = await connect_screen(user)
    await render(call, text, keyboard)


@router.callback_query(ConnectCallback.filter(F.step == "download"))
async def handle_download(call: CallbackQuery, callback_data: ConnectCallback) -> None:
    await call.answer()
    platform = Platform(callback_data.platform)
    guide = get_guide(platform)
    await render(
        call,
        texts.CONNECT_DOWNLOAD.format(platform=guide.title, install_hint=guide.install_hint),
        keyboards.platform_download(platform),
    )


@router.callback_query(ConnectCallback.filter(F.step == "connect"))
async def handle_add_subscription(call: CallbackQuery, callback_data: ConnectCallback, user: NexUser) -> None:
    await call.answer()
    platform = Platform(callback_data.platform)
    view = await get_subscription_view(user)

    if not view.web_url:
        # Подписка есть, но в панель ещё не доехала — ссылки на подписку пока нет.
        await render(call, texts.CONNECT_NOT_READY, keyboards.only_back())
        return

    # Снимок устройств ДО нажатия: успехом считается только появление нового.
    before = await list_devices(user)
    known_hwids = {device.hwid for device in before or []}

    guide = get_guide(platform)
    screen = f"connect:{platform.value}:{call.message.message_id}"
    await render(
        call,
        texts.CONNECT_ADD_SUBSCRIPTION.format(connect_hint=guide.connect_hint),
        keyboards.platform_connect(
            platform, build_connect_url(platform, view.web_url), view.web_url
        ),
        screen=screen,
    )
    # Запись в БД видна и вебхуку панели, и фоновой задаче — кто первым
    # заметит подключение, тот и сообщит.
    await create_connection_watch(user, call.message.chat.id, call.message.message_id, known_hwids)
    start_watching(
        call.bot, call.message.chat.id, call.message.message_id, user, known_hwids, screen
    )
