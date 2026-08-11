import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from django.conf import settings

from bot import texts
from bot.handlers.common import render
from bot.keyboards import ButtonsStorage, keyboards
from bot.services import get_trial_plan, register_referral
from nexvpn.models import NexUser

logger = logging.getLogger(__name__)

router = Router(name="menu")


@router.message(CommandStart())
async def handle_start(
    message: Message, command: CommandObject, user: NexUser, user_created: bool
) -> None:
    """Новичку — приветствие с одной кнопкой, остальным просто меню.

    Реферальная ссылка выглядит как `t.me/бот?start=<id пригласившего>`, id
    приезжает сюда в аргументе команды. Засчитываем только новичкам: у того,
    кто уже пользовался ботом, приглашение всё равно не примут.
    """
    if not user_created:
        await message.answer(texts.MAIN_MENU, reply_markup=keyboards.main_menu())
        return

    invited = bool(command.args) and await register_referral(user, command.args.strip())

    plan = await get_trial_plan()
    text = texts.WELCOME.format(
        trial=texts.plural_days(settings.TRIAL_DAYS),
        plan=plan.name if plan else texts.plural_devices(settings.TRIAL_PLAN_DEVICES),
    )
    if invited:
        text += texts.WELCOME_REFERRAL
    await message.answer(text, reply_markup=keyboards.welcome())


@router.message(Command("menu"))
async def handle_menu_command(message: Message) -> None:
    await message.answer(texts.MAIN_MENU, reply_markup=keyboards.main_menu())


@router.callback_query(F.data == ButtonsStorage.MAIN_MENU.callback)
async def handle_main_menu(call: CallbackQuery) -> None:
    await call.answer()
    await render(call, texts.MAIN_MENU, keyboards.main_menu())


# Регистрируется последним: ловит всё, что не разобрали хендлеры выше.
legacy_router = Router(name="legacy")


@legacy_router.callback_query()
async def handle_outdated_button(call: CallbackQuery) -> None:
    """Любая кнопка, которую не разобрал никто выше.

    Прежде всего это кнопки старого бота: их callback'и остались в чатах у
    сотен людей и будут жить там вечно. Часть имён к тому же совпадает с
    нашими (`add_device_callback`, `delete_device_callback`), поэтому делить
    «наши» и «чужие» по списку callback'ов бессмысленно — человеку в любом
    случае нужно одно и то же: понятный текст и рабочее меню.

    Отдельным сообщением, а не правкой старого: разметка того сообщения не
    наша, и редактировать его смысла нет.
    """
    logger.info("Неразобранная кнопка от %s: %s", call.from_user.id, call.data)
    await call.answer()
    await render(call, texts.USE_NEW_MENU, keyboard=None)
    await call.message.answer(texts.MAIN_MENU, reply_markup=keyboards.main_menu())


@legacy_router.message()
async def handle_anything_else(message: Message) -> None:
    """Любое сообщение, которого мы не ждали: текст, старая команда, стикер.

    Молчание тут — худший из вариантов: человек не понимает, дошло до бота
    хоть что-нибудь. Показываем меню, из него дальше видно, что делать.
    """
    await message.answer(texts.MAIN_MENU, reply_markup=keyboards.main_menu())
