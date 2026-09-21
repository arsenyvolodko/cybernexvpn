"""Что скачать и как подключиться на каждой платформе.

Предлагаем только **INCY** (LLC ITDEV): она есть в российском App Store, в
Google Play и десктопными сборками — одна программа на всех платформах.
Happ не предлагаем: его нет в российском App Store, а у старых версий (до 4.0)
панель отдаёт base64-подписку без профилей «Авто» (они живут только в
XRAY_JSON). Уже установленный Happ при этом продолжает работать.

Ссылки проверены 21.09.2026: магазины — Apple lookup API (`id6756943388`,
есть в ru и us) и Google Play (`llc.itdev.incy`); десктоп — релизы
`INCY-DEV/incy-platforms`, `latest/download/...` отдаёт 200 и переживёт
следующий релиз. Сборки под Windows ARM у INCY нет.
"""

from dataclasses import dataclass
from enum import StrEnum


class Platform(StrEnum):
    IOS = "ios"
    ANDROID = "android"
    MACOS = "macos"
    WINDOWS = "windows"


@dataclass(frozen=True)
class DownloadLink:
    text: str
    url: str


@dataclass(frozen=True)
class PlatformGuide:
    title: str
    downloads: list[DownloadLink]
    install_hint: str

CATALOG: dict[Platform, PlatformGuide] = {
    Platform.IOS: PlatformGuide(
        title="iPhone / iPad 📱",
        downloads=[
            DownloadLink(
                "Скачать INCY в App Store",
                "https://apps.apple.com/ru/app/incy/id6756943388",
            ),
        ],
        install_hint="Поставь INCY из App Store — она есть и в российском магазине.",
    ),
    Platform.ANDROID: PlatformGuide(
        title="Android 🤖",
        downloads=[
            DownloadLink(
                "Скачать INCY в Google Play",
                "https://play.google.com/store/apps/details?id=llc.itdev.incy",
            ),
        ],
        install_hint="Поставь INCY из Google Play и запусти её.",
    ),
    Platform.MACOS: PlatformGuide(
        title="Mac 💻",
        downloads=[
            DownloadLink(
                "Скачать INCY (Apple M1 и новее)",
                "https://github.com/INCY-DEV/incy-platforms/releases/latest/download/incy-macos-arm64.dmg",
            ),
            DownloadLink(
                "Скачать INCY (Intel)",
                "https://github.com/INCY-DEV/incy-platforms/releases/latest/download/incy-macos-intel.dmg",
            ),
        ],
        install_hint=(
            "Скачай .dmg под свой процессор и перетащи INCY в «Программы». "
            "Какой процессор — смотри в меню Apple → «Об этом Mac»: «Apple M…» или «Intel»."
        ),
    ),
    Platform.WINDOWS: PlatformGuide(
        title="Windows 🖥",
        downloads=[
            DownloadLink(
                "Скачать INCY",
                "https://github.com/INCY-DEV/incy-platforms/releases/latest/download/incy-windows-setup.exe",
            ),
        ],
        install_hint=(
            "Скачай установщик и запусти его. Если Windows предупредит о неизвестном "
            "издателе — «Подробнее» → «Выполнить в любом случае»."
        ),
    ),
}


def get_guide(platform: Platform) -> PlatformGuide:
    return CATALOG[platform]

