"""Клиент REST API Remnawave.

Схемы сняты с живой панели 09.08.2026, важные отличия от апстрима:

- пользователь адресуется **числовым `id`**, плоского `uuid` у него нет
  (есть `shortUuid` и `vlessUuid`, но это не идентификаторы для API);
- `PATCH /api/users` принимает идентификатор **в теле**: `id` или `username`.
  Мы всегда ходим по `username` (= `tg_<telegram_id>`) — это делает вызовы
  идемпотентными и не требует, чтобы Django помнил внутренний id панели;
- за Cloudflare/nginx панель требует заголовок `X-Forwarded-Proto: https`,
  иначе ProxyCheckMiddleware отвечает ошибкой;
- ответ всегда завёрнут в `{"response": ...}`.

Панель ничего не знает про деньги: сюда уходят только `expireAt`,
`hwidDeviceLimit` и `status`.
"""

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

USER_NOT_FOUND_CODE = "A025"


class RemnawaveError(Exception):
    """Панель ответила ошибкой или недоступна."""


class RemnawaveNotFound(RemnawaveError):
    """Запрошенного объекта в панели нет."""


def to_panel_datetime(value: datetime) -> str:
    """Панель принимает строгий ISO-8601; отдаём UTC с суффиксом Z."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RemnawaveClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: int | None = None):
        self._base_url = (base_url or settings.PANEL_API_URL).rstrip("/")
        self._token = token or settings.PANEL_API_TOKEN
        self._timeout = timeout or settings.PANEL_API_TIMEOUT

    def _request(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Forwarded-Proto": "https",
        }
        try:
            response = httpx.request(method, url, json=json, headers=headers, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise RemnawaveError(f"{method} {path}: панель недоступна: {exc}") from exc

        if response.status_code >= 400:
            body = self._safe_json(response)
            if body.get("errorCode") == USER_NOT_FOUND_CODE or response.status_code == 404:
                raise RemnawaveNotFound(f"{method} {path}: {body.get('message', response.text)}")
            raise RemnawaveError(f"{method} {path}: {response.status_code} {response.text[:500]}")

        return self._safe_json(response).get("response")

    @staticmethod
    def _safe_json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    # --- пользователи -------------------------------------------------

    def get_user(self, username: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", f"/api/users/by-username/{username}")
        except RemnawaveNotFound:
            return None

    def create_user(
        self,
        username: str,
        expire_at: datetime,
        hwid_device_limit: int,
        telegram_id: int | None = None,
        email: str | None = None,
        description: str | None = None,
        squad_uuids: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "username": username,
            "expireAt": to_panel_datetime(expire_at),
            "hwidDeviceLimit": hwid_device_limit,
            "status": "ACTIVE",
        }
        if telegram_id is not None:
            payload["telegramId"] = telegram_id
        if email:
            payload["email"] = email
        if description:
            payload["description"] = description
        if squad_uuids:
            payload["activeInternalSquads"] = squad_uuids
        return self._request("POST", "/api/users", payload)

    def update_user(
        self,
        username: str,
        expire_at: datetime | None = None,
        hwid_device_limit: int | None = None,
        status: str | None = None,
        email: str | None = None,
        squad_uuids: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"username": username}
        if expire_at is not None:
            payload["expireAt"] = to_panel_datetime(expire_at)
        if hwid_device_limit is not None:
            payload["hwidDeviceLimit"] = hwid_device_limit
        if status is not None:
            payload["status"] = status
        if email is not None:
            payload["email"] = email
        if squad_uuids is not None:
            payload["activeInternalSquads"] = squad_uuids
        return self._request("PATCH", "/api/users", payload)

    def delete_user(self, user_id: int) -> None:
        self._request("DELETE", f"/api/users/{user_id}")

    def disable_user(self, user_id: int) -> dict[str, Any]:
        return self._request("POST", f"/api/users/{user_id}/actions/disable")

    def enable_user(self, user_id: int) -> dict[str, Any]:
        return self._request("POST", f"/api/users/{user_id}/actions/enable")

    # --- устройства (HWID) --------------------------------------------

    def get_devices(self, user_id: int) -> list[dict[str, Any]]:
        response = self._request("GET", f"/api/hwid/devices/{user_id}") or {}
        return response.get("devices", [])

    def delete_device(self, user_id: int, hwid: str) -> None:
        self._request("POST", "/api/hwid/devices/delete", {"userId": user_id, "hwid": hwid})

    # --- телеметрия ----------------------------------------------------

    def iter_users(self, page_size: int = 250) -> list[dict[str, Any]]:
        """Все пользователи панели со счётчиками трафика.

        Нужна целиком, а не по одному: снимок делается разом по всем, иначе
        триста запросов на каждый прогон.
        """
        users: list[dict[str, Any]] = []
        start = 0
        while True:
            response = self._request("GET", f"/api/users?size={page_size}&start={start}") or {}
            batch = response.get("users", [])
            users.extend(batch)
            # total панель отдаёт не всегда, поэтому останавливаемся по длине.
            if len(batch) < page_size:
                return users
            start += page_size

    def list_nodes(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/api/nodes")
        if isinstance(response, list):
            return response
        return (response or {}).get("nodes", [])

    # --- squad'ы -------------------------------------------------------

    def list_internal_squads(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/api/internal-squads") or {}
        return response.get("internalSquads", [])

    def default_squad_uuids(self) -> list[str]:
        """Тариф — это устройства и срок, а не набор серверов: squad один на всех."""
        return [squad["uuid"] for squad in self.list_internal_squads()]
