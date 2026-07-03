from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Any

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import User


CODE_RE = re.compile(r"(?<!\d)(\d[\d\s-]{2,14}\d)(?!\d)")
CODE_CONTEXT_RE = re.compile(r"\b(code|verification|verify|otp|passcode|парол|код|подтверж)\b", re.IGNORECASE)
TELEGRAM_CODE_PEERS = (777000, "Telegram")
VERIFICATION_CODE_PEERS = ("@VerificationCodes", "VerificationCodes")
VERIFICATION_CODE_DIALOG_NAMES = ("Verification Codes", "VerificationCodes")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodeRequest:
    phone_code_hash: str | None
    delivery_type: str
    next_type: str | None
    timeout: int | None
    code_length: int | None
    already_authorized: bool = False
    user: User | None = None


def client_for(
    session_path: Path,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
) -> TelegramClient:
    return TelegramClient(
        str(session_path),
        api_id,
        api_hash,
        device_model=runtime.get("device") or "Desktop",
        system_version=runtime.get("sdk") or "Windows 11 x64",
        app_version=runtime.get("app_version") or "6.9.3 x64",
        lang_code=runtime.get("lang_code") or "en",
        system_lang_code=runtime.get("system_lang_code") or "en-US",
        connection_retries=2,
        request_retries=2,
        retry_delay=1,
        timeout=10,
    )


async def send_code(
    session_path: Path,
    phone: str,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
) -> CodeRequest:
    client = client_for(session_path, api_id, api_hash, runtime)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            logger.info("Telegram session is already authorized: phone=%s", phone)
            return CodeRequest(
                phone_code_hash=None,
                delivery_type="Authorized",
                next_type=None,
                timeout=None,
                code_length=None,
                already_authorized=True,
                user=me,
            )

        sent = await client.send_code_request(phone)
        logger.info(
            "Telegram login code requested: phone=%s delivery=%s next=%s timeout=%s length=%s",
            phone,
            type(sent.type).__name__,
            type(sent.next_type).__name__ if sent.next_type else None,
            sent.timeout,
            getattr(sent.type, "length", None),
        )
        return CodeRequest(
            phone_code_hash=sent.phone_code_hash,
            delivery_type=type(sent.type).__name__,
            next_type=type(sent.next_type).__name__ if sent.next_type else None,
            timeout=sent.timeout,
            code_length=getattr(sent.type, "length", None),
        )
    finally:
        await client.disconnect()


async def sign_in_code(
    session_path: Path,
    phone: str,
    code: str,
    phone_code_hash: str,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
) -> User:
    client = client_for(session_path, api_id, api_hash, runtime)
    await client.connect()
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        me = await client.get_me()
        if me is None:
            raise RuntimeError("Login succeeded but account info is empty")
        return me
    finally:
        await client.disconnect()


async def sign_in_password(
    session_path: Path,
    password: str,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
) -> User:
    client = client_for(session_path, api_id, api_hash, runtime)
    await client.connect()
    try:
        await client.sign_in(password=password)
        me = await client.get_me()
        if me is None:
            raise RuntimeError("Login succeeded but account info is empty")
        return me
    finally:
        await client.disconnect()


async def inspect_session(
    session_path: Path,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
) -> tuple[str, User | None, str | None]:
    client = client_for(session_path, api_id, api_hash, runtime)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return "unauthorized", None, None
        me = await client.get_me()
        if me is None:
            return "empty", None, None
        return "active", me, None
    except SessionPasswordNeededError:
        return "twofa_required", None, None
    except Exception as exc:
        return "error", None, str(exc)
    finally:
        await client.disconnect()


def user_fields(user: User | None) -> dict[str, Any]:
    if user is None:
        return {
            "phone": None,
            "telegram_user_id": None,
            "username": None,
            "first_name": None,
            "last_name": None,
        }
    return {
        "phone": user.phone,
        "telegram_user_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }


def extract_verification_codes(text: str, *, require_context: bool = True) -> list[str]:
    candidates: list[str] = []
    for match in CODE_RE.finditer(text or ""):
        code = re.sub(r"\D+", "", match.group(1))
        if 4 <= len(code) <= 8:
            candidates.append(code)
    if not candidates:
        return []
    if not require_context or CODE_CONTEXT_RE.search(text or ""):
        return candidates
    return []


def _dialog_name_key(value: str | None) -> str:
    return re.sub(r"[\W_]+", "", value or "", flags=re.UNICODE).casefold()


def _entity_key(entity) -> tuple[str, int | str]:
    return type(entity).__name__, getattr(entity, "id", repr(entity))


async def _resolve_code_entities(
    client: TelegramClient,
    *,
    peers: tuple,
    dialog_names: tuple[str, ...] = (),
) -> list:
    entities: list = []
    seen: set[tuple[str, int | str]] = set()

    def add_entity(entity) -> None:
        key = _entity_key(entity)
        if key in seen:
            return
        seen.add(key)
        entities.append(entity)

    for peer in peers:
        try:
            add_entity(await client.get_entity(peer))
        except Exception as exc:
            logger.info("Cannot resolve code peer %s: %s", peer, exc)

    target_names = {_dialog_name_key(name) for name in dialog_names}
    if target_names:
        async for dialog in client.iter_dialogs(limit=100):
            entity = dialog.entity
            names = {
                _dialog_name_key(dialog.name),
                _dialog_name_key(getattr(entity, "username", None)),
            }
            if names & target_names:
                add_entity(entity)

    return entities


async def _get_latest_code_from_peers(
    session_path: Path,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
    *,
    peers: tuple,
    dialog_names: tuple[str, ...] = (),
    limit: int = 15,
    require_context: bool = True,
) -> str | None:
    client = client_for(session_path, api_id, api_hash, runtime)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("session is not authorized")

        latest_code: str | None = None
        latest_date = None
        entities = await _resolve_code_entities(client, peers=peers, dialog_names=dialog_names)

        for entity in entities:
            try:
                async for message in client.iter_messages(entity, limit=limit):
                    text = message.message or ""
                    codes = extract_verification_codes(text, require_context=require_context)
                    if codes:
                        if latest_date is None or (message.date and message.date > latest_date):
                            latest_code = codes[0]
                            latest_date = message.date
                        break
            except Exception as exc:
                logger.info("Cannot read verification codes from peer %s: %s", entity, exc)
                continue
        return latest_code
    finally:
        await client.disconnect()


async def get_latest_telegram_code(
    session_path: Path,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
    *,
    limit: int = 15,
) -> str | None:
    return await _get_latest_code_from_peers(
        session_path,
        api_id,
        api_hash,
        runtime,
        peers=TELEGRAM_CODE_PEERS,
        limit=limit,
    )


async def get_latest_verification_code(
    session_path: Path,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
    *,
    limit: int = 30,
) -> str | None:
    return await _get_latest_code_from_peers(
        session_path,
        api_id,
        api_hash,
        runtime,
        peers=VERIFICATION_CODE_PEERS,
        dialog_names=VERIFICATION_CODE_DIALOG_NAMES,
        limit=limit,
        require_context=False,
    )
