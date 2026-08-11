"""Что скачать и как подключиться на каждой платформе.

Ссылки не выдуманы: магазины взяты из официального `app-config.json` страницы
подписки Remnawave, десктопные — из релизов Happ на GitHub (проверено, что
`latest/download/...` отдаёт 200 и переживёт следующий релиз).

**Happ нет в российском App Store** (проверено 10.08.2026 через Apple lookup
API: `id6504287215` есть в us/de и отсутствует в ru; «Happ Proxy Utility Plus»
`id6746188973`, на который ссылается конфиг Remnawave, снят вообще отовсюду).
Поэтому для российского аккаунта — **INCY** (`id6756943388`, LLC ITDEV), она в
ru-магазине есть. Тот же INCY мы уже видели в HWID-логах панели.

Схемы у обеих программ одинаковой формы — `happ://add/<url>` и `incy://add/<url>`,
поэтому боту не нужно знать, какую из них человек поставил: развилку показывает
страница-мостик.
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
    connect_hint: str


_APPLE_CONNECT = (
    "Обрати внимание на список серверов в приложении. Они все разные и по-разному работают в зависимости от подключения (wifi/сотовые данные), операторов, регионов и тд. Подбери тот, который будет лучше всего работать именно у тебя)"
)

CATALOG: dict[Platform, PlatformGuide] = {
    Platform.IOS: PlatformGuide(
        title="iPhone / iPad 📱",
        downloads=[
            DownloadLink(
                "INCY — если App Store российский",
                "https://apps.apple.com/ru/app/incy/id6756943388",
            ),
            DownloadLink(
                "Happ — если менял регион",
                "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215",
            ),
        ],
        install_hint=(
            "Если ты не меня регион в AppStore, то скачай приложение INCY.\n"
            "Если регион изменен, то приложение Happ.\n"
        ),
        connect_hint=_APPLE_CONNECT,
    ),
    Platform.ANDROID: PlatformGuide(
        title="Android 🤖",
        downloads=[
            DownloadLink(
                "Google Play",
                "https://play.google.com/store/apps/details?id=com.happproxy",
            ),
        ],
        install_hint="Поставь Happ из Google Play и запусти его.",
        connect_hint="Обрати внимание на список серверов в приложении. Они все разные и по-разному работают в зависимости от подключения (wifi/сотовые данные), операторов, регионов и тд. Подбери тот, который будет лучше всего работать именно у тебя)"
    ),
    Platform.MACOS: PlatformGuide(
        title="Mac 💻",
        downloads=[
            # Первым — .dmg: он не зависит от региона App Store и работает у всех.
            DownloadLink(
                "Скачать Happ (.dmg)",
                "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/Happ.macOS.universal.dmg",
            ),
            DownloadLink(
                "Happ в App Store (менял регион)",
                "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215",
            ),
        ],
        install_hint=(
            "Проще всего скачать .dmg и перетащить Happ в «Программы» — этот способ "
            "не зависит от региона App Store. Если аккаунт не российский, можно "
            "поставить из магазина."
        ),
        connect_hint=_APPLE_CONNECT,
    ),
    Platform.WINDOWS: PlatformGuide(
        title="Windows 🖥",
        downloads=[
            DownloadLink(
                "Скачать (обычный компьютер)",
                "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
            ),
            DownloadLink(
                "Скачать (процессор ARM)",
                "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.arm64.exe",
            ),
        ],
        install_hint=(
            "Скачай установщик и запусти его. Если Windows предупредит о неизвестном "
            "издателе — «Подробнее» → «Выполнить в любом случае»."
        ),
        connect_hint="Открой Happ и нажми кнопку подключения. Сервер выбирается в списке.",
    ),
}


def get_guide(platform: Platform) -> PlatformGuide:
    return CATALOG[platform]


# Схемы приложений. Обе одинаковой формы, страница-мостик показывает обе кнопки.
URL_SCHEMES = {"happ": "happ://add/", "incy": "incy://add/"}
