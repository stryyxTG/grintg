from __future__ import annotations

from html import escape
from math import ceil
from pathlib import Path
import re
import secrets
import shutil
import time
import zipfile

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message, TelegramObject
from telethon.errors import SessionPasswordNeededError

from tglol.config import Config
from tglol.db import (
    add_account,
    count_accounts_by_stage,
    delete_account_row,
    delete_accounts_by_stage,
    get_account,
    list_accounts,
    list_accounts_by_scope,
    set_account_stage,
    update_account_status,
)
from tglol.desktop_profile import generated_account_json, random_desktop_runtime, utc_now_iso
from tglol.importer import download_document, import_zip
from tglol.json_utils import load_json, pick_api, runtime_from_json, write_json
from tglol.keyboards import (
    ACCOUNTS_PER_PAGE,
    account_detail_menu,
    accounts_menu,
    accounts_page_keyboard,
    add_account_menu,
    common_storage_sections_menu,
    confirm_check_account_menu,
    confirm_account_stage_menu,
    confirm_delete_account_menu,
    confirm_delete_common_stage_menu,
    digit_code_keyboard,
    registration_filter_menu,
    registration_service_menu,
)
from tglol.paths import unique_path
from tglol.registration import (
    is_registration_service,
    parse_reg_origin,
    parse_service_filter,
    service_filter_label,
    service_label,
    services_from_storage,
    services_label,
)
from tglol.states import AddByCode, AddByZip
from tglol.telegram_service import (
    get_latest_telegram_code,
    get_latest_verification_code,
    inspect_session,
    send_code,
    sign_in_code,
    sign_in_password,
    user_fields,
)

router = Router()


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        config: Config = data["config"]
        user = data.get("event_from_user")
        if not user:
            return None

        if user.id in config.admin_ids:
            return await handler(event, data)

        if isinstance(event, Message):
            await event.answer("Нет доступа.")
        elif isinstance(event, CallbackQuery):
            await event.answer("Нет доступа.", show_alert=True)
        return None


router.message.outer_middleware(AccessMiddleware())
router.callback_query.outer_middleware(AccessMiddleware())


def _pages(total: int) -> int:
    return max(1, ceil(total / ACCOUNTS_PER_PAGE))


def _text(value) -> str:
    return escape(str(value)) if value not in (None, "") else "-"


def _copyable(value) -> str:
    return f"<code>{escape(str(value))}</code>" if value not in (None, "") else "-"


def _username(value) -> str:
    if value in (None, ""):
        return "-"
    username = str(value)
    if not username.startswith("@"):
        username = f"@{username}"
    return f"<code>{escape(username)}</code>"


def _common_account_counts(config: Config) -> tuple[int, int]:
    nereg_count = count_accounts_by_stage(config, account_stage="nereg")
    reg_count = count_accounts_by_stage(config, account_stage="reg")
    return nereg_count, reg_count


def _stage_title(stage: str) -> str:
    return "РЕГ" if stage == "reg" else "НЕРЕГ"


def _origin_stage(origin: str) -> str | None:
    if origin == "common":
        return None
    if origin == "common_nereg":
        return "nereg"
    if parse_reg_origin(origin) is not None:
        return "reg"
    return None


def _origin_service_filters(origin: str) -> tuple[str | None, str | None]:
    return parse_reg_origin(origin) or (None, None)


def _origin_is_valid(origin: str) -> bool:
    return origin in {"common", "common_nereg"} or parse_reg_origin(origin) is not None


def _origin_for_account(account) -> str:
    if account.account_stage == "reg":
        return "common_reg"
    return "common_nereg"


def _stage_filter_title(
    stage: str,
    registration_service: str | None = None,
    excluded_service: str | None = None,
) -> str:
    title = _stage_title(stage)
    if stage == "reg":
        label = service_filter_label(registration_service, excluded_service)
        if label != "Все":
            title += f" {label}"
    return title


def _account_connection_params(account, config: Config) -> tuple[int, str, dict[str, str]]:
    data = None
    raw_path = account.json_original_path or account.json_effective_path
    if raw_path:
        path = Path(raw_path)
        if path.exists():
            data = load_json(path)
    api_id, api_hash = pick_api(data, config)
    runtime = runtime_from_json(data or {})
    return api_id, api_hash, runtime


def _account_file_paths(account) -> list[Path]:
    paths: list[Path] = []
    for raw in (account.session_path, account.json_original_path, account.json_effective_path):
        if raw:
            path = Path(raw)
            if path not in paths:
                paths.append(path)
    return paths


def _resolved_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _is_allowed_storage_file(config: Config, path: Path) -> bool:
    resolved = _resolved_path(path)
    allowed_roots = (config.data_dir, config.sessions_dir, config.json_dir, config.temp_dir)
    for root in allowed_roots:
        try:
            resolved.relative_to(_resolved_path(root))
            return path.exists() and path.is_file()
        except ValueError:
            continue
    return False


def _delete_local_account_files(config: Config, accounts: list) -> int:
    selected_ids = {account.id for account in accounts}
    remaining_paths = {
        str(_resolved_path(path))
        for account in list_accounts_by_scope(config)
        if account.id not in selected_ids
        for path in _account_file_paths(account)
    }
    removed = 0
    seen: set[str] = set()
    for account in accounts:
        for path in _account_file_paths(account):
            resolved = str(_resolved_path(path))
            if resolved in seen or resolved in remaining_paths:
                continue
            seen.add(resolved)
            if not _is_allowed_storage_file(config, path):
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    return removed


def _make_accounts_zip(config: Config, accounts: list, stage: str) -> tuple[Path, int]:
    zip_path = unique_path(config.temp_dir, f"accounts_{stage}_{secrets.token_hex(4)}.zip")
    files_count = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive_names: set[str] = set()
        for account in accounts:
            added_paths: set[str] = set()
            for path in _account_file_paths(account):
                resolved = str(_resolved_path(path))
                if resolved in added_paths or not path.exists() or not path.is_file():
                    continue
                added_paths.add(resolved)
                archive_name = path.name
                if archive_name in archive_names:
                    archive_name = f"{account.id}_{path.name}"
                archive_names.add(archive_name)
                archive.write(path, arcname=archive_name)
                files_count += 1
    return zip_path, files_count


def _normalize_login_code(raw: str) -> str:
    return "".join(ch for ch in raw if ch.isdigit())


def _normalize_login_phone(raw: str) -> str | None:
    raw = (raw or "").strip()
    digits = re.sub(r"\D+", "", raw)
    if raw.startswith("00") and len(digits) > 2:
        digits = digits[2:]
    if not 8 <= len(digits) <= 15:
        return None
    return f"+{digits}"


def _delivery_type_label(raw_type: str | None) -> str:
    labels = {
        "Authorized": "сессия уже авторизована",
        "SentCodeTypeApp": "в Telegram-приложение аккаунта",
        "SentCodeTypeSms": "SMS",
        "SentCodeTypeCall": "звонком",
        "SentCodeTypeFlashCall": "flash-call",
        "SentCodeTypeMissedCall": "пропущенным звонком",
        "SentCodeTypeEmailCode": "на email",
        "CodeTypeSms": "SMS",
        "CodeTypeCall": "звонком",
        "CodeTypeFlashCall": "flash-call",
        "CodeTypeMissedCall": "пропущенным звонком",
        "CodeTypeFragmentSms": "Fragment SMS",
    }
    return labels.get(raw_type or "", raw_type or "неизвестно")


def _code_request_text(request) -> str:
    lines = [
        "Telegram принял запрос кода.",
        f"Куда отправлен: {_delivery_type_label(request.delivery_type)}",
        f"Raw type: {request.delivery_type}",
    ]
    if request.code_length:
        lines.append(f"Длина кода: {request.code_length} цифр")
    if request.next_type:
        lines.append(f"Следующий способ: {_delivery_type_label(request.next_type)}")
    if request.timeout:
        lines.append(f"Повторный запрос будет доступен примерно через {request.timeout} сек.")
        lines.append("Не жми повторный запрос без необходимости: Telegram может сменить доставку на звонок/SMS.")
    if request.delivery_type == "SentCodeTypeApp":
        lines.append("")
        lines.append("Важно: это НЕ SMS. Код должен прийти в уже активную сессию этого аккаунта.")
        lines.append("Если активной сессии нет под рукой, бот не сможет сам вытащить этот первый код: Telegram не отдает его через API до входа.")
    elif request.delivery_type in {"SentCodeTypeSms", "CodeTypeSms", "CodeTypeFragmentSms"}:
        lines.append("")
        lines.append("Это SMS/Fragment-доставка. Код нужно смотреть не в Telegram-чате, а в SMS/Fragment.")
    elif request.delivery_type in {"SentCodeTypeCall", "SentCodeTypeMissedCall", "CodeTypeCall", "CodeTypeMissedCall"}:
        lines.append("")
        lines.append("Telegram сам выбрал доставку звонком. Это бывает и у уже существующих аккаунтов.")
        lines.append("Код нужно брать из звонка/последних цифр номера.")
    lines.append("")
    lines.append("Введите код кнопками или одним сообщением.")
    return "\n".join(lines)


def _code_entry_text(info_text: str, code: str) -> str:
    current = code if code else "-"
    return f"{info_text}\n\nТекущий ввод: <code>{current}</code>"


def _promote_login_session(config: Config, temp_session_path: Path, phone: str, login_id: str) -> Path:
    phone_digits = phone.lstrip("+")
    final_path = unique_path(config.sessions_dir, f"{phone_digits}_{login_id}.session")
    if temp_session_path.resolve() == final_path.resolve():
        return final_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    if not temp_session_path.exists():
        raise RuntimeError(f"Temporary session file not found: {temp_session_path}")
    shutil.move(str(temp_session_path), str(final_path))
    return final_path


async def _show_account_page(callback: CallbackQuery, config: Config, origin: str, ref_id: int, page: int) -> None:
    if not _origin_is_valid(origin):
        await callback.answer("Этот раздел больше недоступен.", show_alert=True)
        return

    account_stage = _origin_stage(origin)
    registration_service, excluded_service = _origin_service_filters(origin)

    total = count_accounts_by_stage(
        config,
        account_stage=account_stage,
        registration_service=registration_service,
        excluded_registration_service=excluded_service,
    )
    page = max(0, min(page, _pages(total) - 1))
    accounts = list_accounts(
        config,
        limit=ACCOUNTS_PER_PAGE,
        offset=page * ACCOUNTS_PER_PAGE,
        account_stage=account_stage,
        registration_service=registration_service,
        excluded_registration_service=excluded_service,
    )

    if account_stage:
        title = f"Хранилище\nРаздел: {_stage_filter_title(account_stage, registration_service, excluded_service)}"
    else:
        title = "Хранилище"

    if total == 0:
        text = f"{title}\n\nАккаунтов пока нет."
    else:
        text = f"{title}\n\nВсего: {total}\nСтраница: {page + 1}/{_pages(total)}"

    await callback.message.edit_text(
        text,
        reply_markup=accounts_page_keyboard(
            accounts,
            total=total,
            page=page,
            origin=origin,
            ref_id=ref_id,
        ),
    )
    await callback.answer()


def _account_detail_text(account) -> str:
    full_name = " ".join(
        part for part in (account.first_name, account.last_name)
        if part not in (None, "")
    ).strip()
    full_name = full_name or "-"

    if account.account_stage == "reg":
        stage = "РЕГ"
        services_line = services_label(account.registration_services, account.registration_service)
    else:
        stage = "НЕРЕГ"
        services_line = "не регался"

    if account.account_stage == "reg":
        title = f"<b>Аккаунт #{account.id}</b> · {stage} {escape(services_line)}"
    else:
        title = f"<b>Аккаунт #{account.id}</b> · {stage}"
    return (
        f"{title}\n"
        f"Статус: <code>{_text(account.status)}</code>\n\n"
        f"Имя: <b>{escape(full_name)}</b>\n"
        f"Номер: {_copyable(account.phone)}\n"
        f"Username: {_username(account.username)}\n"
        f"User ID: {_copyable(account.telegram_user_id)}\n\n"
        f"Сервисы: <code>{escape(services_line)}</code>\n\n"
        f"JSON: {_text(account.json_source)}\n"
        f"Источник: {_text(account.source_type)}"
    )


def _service_selection_text(selected_services: list[str]) -> str:
    selected = ", ".join(service_label(service) for service in selected_services) if selected_services else "не выбрано"
    return f"Выбери один или несколько сервисов, где аккаунт зареган:\n\nВыбрано: {selected}"


async def finalize_code_login(
    message: Message,
    state: FSMContext,
    config: Config,
    *,
    twofa: str | None,
    user,
) -> None:
    data = await state.get_data()
    session_path = _promote_login_session(
        config,
        Path(data["session_path"]),
        data["phone"],
        data["login_id"],
    )
    runtime = data["runtime"]
    fields = user_fields(user)
    json_path = unique_path(config.json_dir, session_path.with_suffix(".json").name)
    generated = generated_account_json(
        config,
        runtime=runtime,
        twofa=twofa,
        session_file=session_path.name,
        phone=fields["phone"],
        user_id=fields["telegram_user_id"],
        username=fields["username"],
        first_name=fields["first_name"],
        last_name=fields["last_name"],
    )
    write_json(json_path, generated)

    now = utc_now_iso()
    account_id = add_account(
        config,
        {
            "phone": fields["phone"],
            "telegram_user_id": fields["telegram_user_id"],
            "username": fields["username"],
            "first_name": fields["first_name"],
            "last_name": fields["last_name"],
            "session_path": str(session_path),
            "json_original_path": None,
            "json_effective_path": str(json_path),
            "json_source": "generated",
            "twofa_password": twofa,
            "source_type": "code",
            "status": "active",
            "created_by": data.get("admin_id") or (message.from_user.id if message.from_user else None),
            "created_at": now,
            "updated_at": now,
        },
    )
    await state.clear()
    await message.answer(
        (
            f"Аккаунт добавлен по коду.\n"
            f"ID: {account_id}\n"
            f"Телефон: {fields['phone'] or '-'}\n"
            f"Раздел: НЕРЕГ"
        ),
        reply_markup=accounts_menu(),
    )


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Аккаунты", reply_markup=accounts_menu())


@router.callback_query(F.data == "accounts:menu")
async def show_accounts_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Аккаунты", reply_markup=accounts_menu())
    await callback.answer()


@router.callback_query(F.data == "accounts:common_sections")
async def show_common_account_sections(callback: CallbackQuery, config: Config) -> None:
    nereg_count, reg_count = _common_account_counts(config)
    text = (
        "Хранилище\n\n"
        f"НЕРЕГ: {nereg_count}\n"
        f"РЕГ: {reg_count}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=common_storage_sections_menu(nereg_count=nereg_count, reg_count=reg_count),
    )
    await callback.answer()


@router.callback_query(F.data == "accounts:add")
async def show_add_account_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "Все новые аккаунты попадут в раздел НЕРЕГ.\n\nВыбери способ добавления аккаунта.",
        reply_markup=add_account_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "accounts:add:code")
async def add_by_code_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddByCode.waiting_phone)
    await callback.message.edit_text(
        "Отправь номер телефона.\n\nМожно с плюсом или без него, например:\n+15074486037\n15074486037"
    )
    await callback.answer()


@router.message(AddByCode.waiting_phone)
async def add_by_code_phone(message: Message, state: FSMContext, config: Config) -> None:
    phone = _normalize_login_phone(message.text or "")
    if not phone:
        await message.answer("Номер некорректный. Отправь номер с кодом страны, например: +15074486037")
        return

    runtime = random_desktop_runtime()
    admin_id = message.from_user.id if message.from_user else 0
    login_id = secrets.token_hex(4)
    phone_digits = phone.lstrip("+")
    session_path = unique_path(config.temp_dir, f"temp_session_{admin_id}_{phone_digits}_{login_id}.session")
    try:
        code_request = await send_code(
            session_path,
            phone,
            config.telegram_api_id,
            config.telegram_api_hash,
            runtime,
        )
    except Exception as exc:
        await state.clear()
        await message.answer(
            f"Не удалось отправить код Telegram: {exc}",
            reply_markup=add_account_menu(),
        )
        return

    await state.update_data(
        phone=phone,
        phone_code_hash=code_request.phone_code_hash,
        session_path=str(session_path),
        login_id=login_id,
        admin_id=admin_id,
        runtime=runtime,
        login_started_at=utc_now_iso(),
        code="",
    )
    if code_request.already_authorized and code_request.user:
        await finalize_code_login(message, state, config, twofa=None, user=code_request.user)
        return
    if code_request.already_authorized:
        await state.clear()
        await message.answer(
            "Сессия уже авторизована, но Telegram не вернул данные аккаунта.",
            reply_markup=add_account_menu(),
        )
        return

    info_text = _code_request_text(code_request)
    await state.update_data(code_info_text=info_text)
    await state.set_state(AddByCode.waiting_code)
    await message.answer(_code_entry_text(info_text, ""), reply_markup=digit_code_keyboard())


@router.message(AddByCode.waiting_code)
async def add_by_code_message_code(message: Message, state: FSMContext, config: Config) -> None:
    code = _normalize_login_code(message.text or "")
    await complete_code(message, state, config, code)


@router.callback_query(AddByCode.waiting_code, F.data.startswith("code:"))
async def add_by_code_digit(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    data = await state.get_data()
    code = data.get("code", "")
    info_text = data.get("code_info_text", "Код отправлен в Telegram. Введите код кнопками или одним сообщением.")

    if callback.data.startswith("code:digit:"):
        if len(code) >= 8:
            await callback.answer("Максимум 8 цифр.")
            return
        code += callback.data.rsplit(":", 1)[-1]
        await state.update_data(code=code)
        await callback.message.edit_text(_code_entry_text(info_text, code), reply_markup=digit_code_keyboard())
        await callback.answer()
        return

    if callback.data == "code:clear":
        if not code:
            await callback.answer("Код уже пустой.")
            return
        await state.update_data(code="")
        await callback.message.edit_text(_code_entry_text(info_text, ""), reply_markup=digit_code_keyboard())
        await callback.answer("Код очищен.")
        return

    if callback.data == "code:backspace":
        if not code:
            await callback.answer("Код уже пустой.")
            return
        code = code[:-1]
        await state.update_data(code=code)
        await callback.message.edit_text(_code_entry_text(info_text, code), reply_markup=digit_code_keyboard())
        await callback.answer()
        return

    if callback.data == "code:done":
        await callback.answer()
        await complete_code(callback.message, state, config, code)


async def complete_code(message: Message, state: FSMContext, config: Config, code: str) -> None:
    code = _normalize_login_code(code)
    if not code:
        await message.answer("Код пустой.")
        return
    if not 5 <= len(code) <= 8:
        await message.answer("Код должен быть длиной 5-8 цифр. Введи заново.")
        return

    data = await state.get_data()
    phone_code_hash = data.get("phone_code_hash")
    if not phone_code_hash:
        await message.answer("Не найден phone_code_hash. Запроси код ещё раз.", reply_markup=digit_code_keyboard())
        return
    try:
        user = await sign_in_code(
            Path(data["session_path"]),
            data["phone"],
            code,
            phone_code_hash,
            config.telegram_api_id,
            config.telegram_api_hash,
            data["runtime"],
        )
    except SessionPasswordNeededError:
        await state.update_data(code=code)
        await state.set_state(AddByCode.waiting_twofa)
        await message.answer("Нужен пароль 2FA. Отправь пароль.")
        return
    except Exception as exc:
        await state.update_data(code="")
        await message.answer(f"Вход не удался: {exc}\nВведи код заново.", reply_markup=digit_code_keyboard())
        return

    await finalize_code_login(message, state, config, twofa=None, user=user)


@router.message(AddByCode.waiting_twofa)
async def add_by_code_twofa(message: Message, state: FSMContext, config: Config) -> None:
    password = message.text or ""
    data = await state.get_data()
    try:
        user = await sign_in_password(
            Path(data["session_path"]),
            password,
            config.telegram_api_id,
            config.telegram_api_hash,
            data["runtime"],
        )
    except Exception as exc:
        await message.answer(f"Проверка 2FA не прошла: {exc}")
        return
    await finalize_code_login(message, state, config, twofa=password, user=user)


@router.callback_query(F.data == "accounts:add:zip")
async def add_zip_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddByZip.waiting_zip)
    await callback.message.edit_text("Загрузи .zip архив с .session и .json файлами.")
    await callback.answer()


@router.message(AddByZip.waiting_zip, F.document)
async def add_zip_file(message: Message, bot: Bot, state: FSMContext, config: Config) -> None:
    filename = message.document.file_name or ""
    if not filename.lower().endswith(".zip"):
        await message.answer("Нужен файл с расширением .zip.")
        return

    zip_path = unique_path(config.temp_dir, filename)
    await download_document(bot, message.document, zip_path)
    progress_message = await message.answer("ZIP получен. Начинаю импорт...")
    last_progress_update = 0.0

    async def update_zip_progress(done: int, total: int, current: str) -> None:
        nonlocal last_progress_update
        now = time.monotonic()
        if done not in {0, total} and done % 5 != 0 and now - last_progress_update < 3:
            return
        last_progress_update = now
        current_line = f"\nСейчас: {current}" if current else ""
        try:
            await progress_message.edit_text(
                f"Импорт ZIP...\nГотово: {done}/{total}{current_line}"
            )
        except Exception:
            pass

    try:
        results, summary = await import_zip(
            config,
            zip_path=zip_path,
            created_by=message.from_user.id if message.from_user else None,
            progress=update_zip_progress,
        )
    except Exception as exc:
        await state.clear()
        try:
            await progress_message.edit_text(f"Импорт ZIP не удался: {exc}")
        except Exception:
            pass
        await message.answer(
            f"Импорт ZIP не удался: {exc}",
            reply_markup=add_account_menu(),
        )
        return

    lines = [summary, "", "Раздел: НЕРЕГ", ""]
    for result in results[:20]:
        if result.account_id:
            lines.append(f"#{result.account_id} | {result.status} | {result.phone or result.username or '-'}")
        else:
            lines.append(f"ERROR | {result.note or 'unknown error'}")
    if len(results) > 20:
        lines.append(f"...и еще {len(results) - 20}")
    await state.clear()
    try:
        await progress_message.edit_text("Импорт ZIP завершён.")
    except Exception:
        pass
    await message.answer("\n".join(lines), reply_markup=accounts_menu())


@router.message(F.text == "/cancel")
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=accounts_menu())


@router.callback_query(F.data.startswith("accounts:page:"))
async def show_accounts_page(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.clear()
    _, _, origin, raw_ref, raw_page = callback.data.split(":", 4)
    await _show_account_page(callback, config, origin, int(raw_ref), int(raw_page))


@router.callback_query(F.data.startswith("accounts:reg_filter:"))
async def show_registration_filter(callback: CallbackQuery) -> None:
    origin = callback.data.split(":", 2)[-1]
    if parse_reg_origin(origin) is None:
        await callback.answer("Фильтр доступен только в разделе РЕГ.", show_alert=True)
        return
    await callback.message.edit_text(
        "Фильтр РЕГ\n\n"
        "Можно смотреть аккаунты, которые регались в конкретном сервисе, "
        "или наоборот исключить этот сервис.",
        reply_markup=registration_filter_menu(origin),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("accounts:phone:"))
async def send_account_phone(callback: CallbackQuery, config: Config) -> None:
    account_id = int(callback.data.rsplit(":", 1)[-1])
    account = get_account(config, account_id)
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    if not account.phone:
        await callback.answer("Номер не указан.", show_alert=True)
        return
    await callback.message.answer(_copyable(account.phone))
    await callback.answer("Номер отправлен.")


@router.callback_query(F.data.startswith("account:check_ask:"))
async def ask_check_account(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_account_id, origin, raw_ref, raw_page = callback.data.split(":", 5)
    if not _origin_is_valid(origin):
        await callback.answer("Этот раздел больше недоступен.", show_alert=True)
        return
    account = get_account(config, int(raw_account_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    await callback.message.edit_text(
        (
            "Проверить аккаунт?\n\n"
            "Бот только подключится к существующей session и проверит авторизацию. "
            "Новый код запрашиваться не будет."
        ),
        reply_markup=confirm_check_account_menu(account.id, origin, int(raw_ref), int(raw_page)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("account:check_confirm:"))
async def confirm_check_account(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_account_id, origin, raw_ref, raw_page = callback.data.split(":", 5)
    if not _origin_is_valid(origin):
        await callback.answer("Этот раздел больше недоступен.", show_alert=True)
        return
    account_id = int(raw_account_id)
    account = get_account(config, account_id)
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return

    session_path = Path(account.session_path)
    if not session_path.exists():
        update_account_status(config, account_id, "missing_file")
        account = get_account(config, account_id)
        await callback.message.edit_text(
            _account_detail_text(account),
            reply_markup=account_detail_menu(
                account.id,
                account_stage=account.account_stage,
                origin=origin,
                ref_id=int(raw_ref),
                page=int(raw_page),
            ),
        )
        await callback.answer("Session файл не найден.", show_alert=True)
        return

    try:
        api_id, api_hash, runtime = _account_connection_params(account, config)
        status, user, note = await inspect_session(session_path, api_id, api_hash, runtime)
    except Exception as exc:
        status = "error"
        user = None
        note = str(exc)

    update_account_status(config, account_id, status)
    account = get_account(config, account_id)
    text = _account_detail_text(account)
    if status == "active" and user:
        fields = user_fields(user)
        text += (
            "\n\nПроверка: <b>живой</b>"
            f"\nTelegram ID: {_copyable(fields['telegram_user_id'])}"
            f"\nUsername: {_username(fields['username'])}"
        )
    elif status == "unauthorized":
        text += "\n\nПроверка: <b>не авторизован</b>"
    elif status == "empty":
        text += "\n\nПроверка: <b>Telegram не вернул данные аккаунта</b>"
    elif status == "twofa_required":
        text += "\n\nПроверка: <b>требуется 2FA</b>"
    else:
        text += f"\n\nПроверка: <b>ошибка</b>\n{_text(note)}"

    await callback.message.edit_text(
        text,
        reply_markup=account_detail_menu(
            account.id,
            account_stage=account.account_stage,
            origin=origin,
            ref_id=int(raw_ref),
            page=int(raw_page),
        ),
    )
    await callback.answer("Проверка завершена.")


async def _send_account_code(callback: CallbackQuery, config: Config, *, verification: bool) -> None:
    account_id = int(callback.data.rsplit(":", 1)[-1])
    account = get_account(config, account_id)
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    session_path = Path(account.session_path)
    if not session_path.exists():
        await callback.answer("Session файл не найден.", show_alert=True)
        return
    try:
        api_id, api_hash, runtime = _account_connection_params(account, config)
        if verification:
            code = await get_latest_verification_code(session_path, api_id, api_hash, runtime)
            title = "Verification Code"
            not_found = "Свежий Verification Code не найден в последних сообщениях."
        else:
            code = await get_latest_telegram_code(session_path, api_id, api_hash, runtime)
            title = "Код Telegram"
            not_found = "Код Telegram не найден в последних сообщениях."
    except Exception as exc:
        await callback.message.answer(f"Не удалось получить код: {escape(str(exc))}")
        await callback.answer()
        return

    if not code:
        await callback.message.answer(not_found)
        await callback.answer()
        return

    await callback.message.answer(f"{title}: <code>{escape(code)}</code>")
    await callback.answer("Код найден.")


@router.callback_query(F.data.startswith("account:telegram_code:"))
async def send_telegram_code(callback: CallbackQuery, config: Config) -> None:
    await _send_account_code(callback, config, verification=False)


@router.callback_query(F.data.startswith("account:verification_code:"))
async def send_verification_code(callback: CallbackQuery, config: Config) -> None:
    await _send_account_code(callback, config, verification=True)


@router.callback_query(F.data.startswith("account:open:"))
async def show_account_detail_callback(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_id, origin, raw_ref, raw_page = callback.data.split(":", 5)
    if not _origin_is_valid(origin):
        await callback.answer("Этот раздел больше недоступен.", show_alert=True)
        return
    account = get_account(config, int(raw_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    await callback.message.edit_text(
        _account_detail_text(account),
        reply_markup=account_detail_menu(
            account.id,
            account_stage=account.account_stage,
            origin=origin,
            ref_id=int(raw_ref),
            page=int(raw_page),
        ),
    )
    await callback.answer()


@router.message(F.text.regexp(r"^/account_\d+$"))
async def show_account_detail_message(message: Message, config: Config) -> None:
    account_id = int((message.text or "").rsplit("_", 1)[-1])
    account = get_account(config, account_id)
    if not account:
        await message.answer("Аккаунт не найден.")
        return
    origin = _origin_for_account(account)
    await message.answer(
        _account_detail_text(account),
        reply_markup=account_detail_menu(account.id, account_stage=account.account_stage, origin=origin, ref_id=0, page=0),
    )


@router.callback_query(F.data.startswith("accounts:file:"))
async def download_account_file(callback: CallbackQuery, config: Config) -> None:
    _, _, file_type, raw_id = callback.data.split(":", 3)
    account = get_account(config, int(raw_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return

    path = Path(account.session_path) if file_type == "session" else Path(account.json_original_path or account.json_effective_path or "")
    if not path.exists():
        await callback.answer("Файл не найден.", show_alert=True)
        return

    await callback.message.answer_document(FSInputFile(path))
    await callback.answer()


@router.callback_query(F.data.startswith("accounts:zip_common:"))
async def download_common_stage_zip(callback: CallbackQuery, config: Config) -> None:
    parts = callback.data.split(":")
    stage = parts[2] if len(parts) > 2 else ""
    raw_filter = parts[3] if len(parts) > 3 else "all"
    service_filter = parse_service_filter(raw_filter)
    if stage not in {"nereg", "reg"}:
        await callback.answer("Неизвестный раздел.", show_alert=True)
        return
    if service_filter is None:
        await callback.answer("Неизвестный сервис.", show_alert=True)
        return
    registration_service, excluded_service = service_filter
    accounts = list_accounts_by_scope(
        config,
        account_stage=stage,
        registration_service=registration_service,
        excluded_registration_service=excluded_service,
    )
    if not accounts:
        await callback.answer("В этом разделе нет аккаунтов.", show_alert=True)
        return
    zip_path, files_count = _make_accounts_zip(config, accounts, stage)
    if files_count == 0:
        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass
        await callback.answer("Файлы для архива не найдены.", show_alert=True)
        return
    await callback.message.answer_document(
        FSInputFile(zip_path),
        caption=f"Хранилище {_stage_filter_title(stage, registration_service, excluded_service)}: {len(accounts)} аккаунтов, {files_count} файлов.",
    )
    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass
    await callback.answer("ZIP сформирован.")


@router.callback_query(F.data.startswith("account:delete_ask:"))
async def ask_delete_account(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_account_id, origin, raw_ref, raw_page = callback.data.split(":", 5)
    if not _origin_is_valid(origin):
        await callback.answer("Удаление здесь недоступно.", show_alert=True)
        return
    account = get_account(config, int(raw_account_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    await callback.message.edit_text(
        "ВЫ УВЕРЕНЫ ЧТО ХОТИТЕ УДАЛИТЬ АККАУНТ И ЕГО ФАЙЛЫ С СЕРВЕРА?",
        reply_markup=confirm_delete_account_menu(account.id, origin, int(raw_ref), int(raw_page)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("account:delete_confirm:"))
async def confirm_delete_account(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_account_id, origin, _raw_ref, _raw_page = callback.data.split(":", 5)
    if not _origin_is_valid(origin):
        await callback.answer("Удаление здесь недоступно.", show_alert=True)
        return
    account = get_account(config, int(raw_account_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    stage = account.account_stage
    removed_files = _delete_local_account_files(config, [account])
    delete_account_row(config, account.id)
    nereg_count, reg_count = _common_account_counts(config)
    await callback.message.edit_text(
        (
            f"Аккаунт #{account.id} удален из бота.\n"
            f"Файлов удалено с сервера: {removed_files}\n\n"
            f"Хранилище\nНЕРЕГ: {nereg_count}\nРЕГ: {reg_count}"
        ),
        reply_markup=common_storage_sections_menu(nereg_count=nereg_count, reg_count=reg_count),
    )
    await callback.answer(f"Удалено из {_stage_title(stage)}.")


@router.callback_query(F.data.startswith("accounts:delete_common_ask:"))
async def ask_delete_common_stage(callback: CallbackQuery, config: Config) -> None:
    parts = callback.data.split(":")
    stage = parts[2] if len(parts) > 2 else ""
    raw_filter = parts[3] if len(parts) > 3 else "all"
    service_filter = parse_service_filter(raw_filter)
    if stage not in {"nereg", "reg"}:
        await callback.answer("Неизвестный раздел.", show_alert=True)
        return
    if service_filter is None:
        await callback.answer("Неизвестный сервис.", show_alert=True)
        return
    registration_service, excluded_service = service_filter
    total = count_accounts_by_stage(
        config,
        account_stage=stage,
        registration_service=registration_service,
        excluded_registration_service=excluded_service,
    )
    if not total:
        await callback.answer("В этом разделе нет аккаунтов.", show_alert=True)
        return
    await callback.message.edit_text(
        (
            f"ВЫ УВЕРЕНЫ ЧТО ХОТИТЕ УДАЛИТЬ ВЕСЬ {_stage_filter_title(stage, registration_service, excluded_service)}?\n"
            f"Аккаунтов: {total}\nФайлы будут удалены только с сервера."
        ),
        reply_markup=confirm_delete_common_stage_menu(stage, registration_service, excluded_service),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("accounts:delete_common_confirm:"))
async def confirm_delete_common_stage(callback: CallbackQuery, config: Config) -> None:
    parts = callback.data.split(":")
    stage = parts[2] if len(parts) > 2 else ""
    raw_filter = parts[3] if len(parts) > 3 else "all"
    service_filter = parse_service_filter(raw_filter)
    if stage not in {"nereg", "reg"}:
        await callback.answer("Неизвестный раздел.", show_alert=True)
        return
    if service_filter is None:
        await callback.answer("Неизвестный сервис.", show_alert=True)
        return
    registration_service, excluded_service = service_filter
    accounts = list_accounts_by_scope(
        config,
        account_stage=stage,
        registration_service=registration_service,
        excluded_registration_service=excluded_service,
    )
    if not accounts:
        await callback.answer("В этом разделе нет аккаунтов.", show_alert=True)
        return
    removed_files = _delete_local_account_files(config, accounts)
    removed_rows = delete_accounts_by_stage(
        config,
        account_stage=stage,
        registration_service=registration_service,
        excluded_registration_service=excluded_service,
    )
    nereg_count, reg_count = _common_account_counts(config)
    await callback.message.edit_text(
        (
            f"{_stage_filter_title(stage, registration_service, excluded_service)} очищен.\n"
            f"Аккаунтов удалено из бота: {removed_rows}\n"
            f"Файлов удалено с сервера: {removed_files}\n\n"
            f"Хранилище\nНЕРЕГ: {nereg_count}\nРЕГ: {reg_count}"
        ),
        reply_markup=common_storage_sections_menu(nereg_count=nereg_count, reg_count=reg_count),
    )
    await callback.answer("Раздел очищен.")


@router.callback_query(F.data.startswith("account:service:"))
async def choose_registration_service(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    _, _, raw_account_id, origin, raw_ref, raw_page = callback.data.split(":", 5)
    if not _origin_is_valid(origin):
        await callback.answer("Этот раздел больше недоступен.", show_alert=True)
        return
    account = get_account(config, int(raw_account_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    selected_services = list(services_from_storage(account.registration_services, account.registration_service))
    await state.update_data(
        service_edit_account_id=account.id,
        service_edit_origin=origin,
        service_edit_ref_id=int(raw_ref),
        service_edit_page=int(raw_page),
        service_edit_selected=selected_services,
    )
    await callback.message.edit_text(
        _service_selection_text(selected_services),
        reply_markup=registration_service_menu(selected_services),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("account:service_toggle:"))
async def toggle_registration_service(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, service = callback.data.split(":", 2)
    if not is_registration_service(service):
        await callback.answer("Неизвестный сервис.", show_alert=True)
        return
    data = await state.get_data()
    if "service_edit_account_id" not in data:
        await callback.answer("Открой карточку аккаунта заново.", show_alert=True)
        return
    selected_services = list(data.get("service_edit_selected") or [])
    if service in selected_services:
        selected_services.remove(service)
    else:
        selected_services.append(service)
    await state.update_data(service_edit_selected=selected_services)
    await callback.message.edit_text(
        _service_selection_text(selected_services),
        reply_markup=registration_service_menu(selected_services),
    )
    await callback.answer()


@router.callback_query(F.data == "account:service_save")
async def save_registration_services(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    data = await state.get_data()
    account_id = data.get("service_edit_account_id")
    if account_id is None:
        await callback.answer("Открой карточку аккаунта заново.", show_alert=True)
        return
    selected_services = list(data.get("service_edit_selected") or [])
    if not selected_services:
        await callback.answer("Выбери хотя бы один сервис.", show_alert=True)
        return
    account = get_account(config, int(account_id))
    if not account:
        await state.clear()
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    set_account_stage(config, int(account_id), "reg", registration_services=selected_services)
    account = get_account(config, int(account_id))
    await state.clear()
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    new_origin = _origin_for_account(account)
    await callback.message.edit_text(
        _account_detail_text(account),
        reply_markup=account_detail_menu(
            account.id,
            account_stage=account.account_stage,
            origin=new_origin,
            ref_id=0,
            page=0,
        ),
    )
    await callback.answer(f"Сервисы сохранены: {services_label(account.registration_services, account.registration_service)}")


@router.callback_query(F.data == "account:service_cancel")
async def cancel_registration_services(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    data = await state.get_data()
    account_id = data.get("service_edit_account_id")
    origin = data.get("service_edit_origin") or "common_reg"
    ref_id = int(data.get("service_edit_ref_id") or 0)
    page = int(data.get("service_edit_page") or 0)
    await state.clear()
    if account_id is None:
        await callback.answer("Открой карточку аккаунта заново.", show_alert=True)
        return
    account = get_account(config, int(account_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    await callback.message.edit_text(
        _account_detail_text(account),
        reply_markup=account_detail_menu(
            account.id,
            account_stage=account.account_stage,
            origin=origin,
            ref_id=ref_id,
            page=page,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("account:stage_ask1:"))
async def ask_account_stage_first(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_account_id, target_stage, origin, raw_ref, raw_page = callback.data.split(":", 6)
    account = get_account(config, int(raw_account_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    if target_stage == "reg":
        await callback.answer("Сначала выбери сервисы.", show_alert=True)
        return
    else:
        text = "ВЫ УВЕРЕНЫ ЧТО ХОТИТЕ ПЕРЕНЕСТИ АКК В НЕРЕГ?"
    await callback.message.edit_text(
        text,
        reply_markup=confirm_account_stage_menu(
            account.id,
            target_stage,
            None,
            origin,
            int(raw_ref),
            int(raw_page),
            step=1,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("account:stage_ask2:"))
async def ask_account_stage_second(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_account_id, target_stage, raw_service, origin, raw_ref, raw_page = callback.data.split(":", 7)
    registration_service = None if raw_service == "none" else raw_service
    account = get_account(config, int(raw_account_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    if target_stage == "reg":
        if not is_registration_service(registration_service):
            await callback.answer("Неизвестный сервис.", show_alert=True)
            return
        text = f"ПОДТВЕРДИТЕ ЕЩЕ РАЗ: ПОМЕТИТЬ АКК РЕГАННЫМ?\nСервис: {service_label(registration_service)}"
    else:
        text = "ПОДТВЕРДИТЕ ЕЩЕ РАЗ: ПЕРЕНЕСТИ АКК В НЕРЕГ?"
    await callback.message.edit_text(
        text,
        reply_markup=confirm_account_stage_menu(
            account.id,
            target_stage,
            registration_service,
            origin,
            int(raw_ref),
            int(raw_page),
            step=2,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("account:stage_confirm:"))
async def confirm_account_stage(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_account_id, target_stage, raw_service, _origin, _raw_ref, _raw_page = callback.data.split(":", 7)
    registration_service = None if raw_service == "none" else raw_service
    account_id = int(raw_account_id)
    account = get_account(config, account_id)
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    if target_stage == "reg" and not is_registration_service(registration_service):
        await callback.answer("Неизвестный сервис.", show_alert=True)
        return
    set_account_stage(config, account_id, target_stage, registration_service)
    account = get_account(config, account_id)
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    new_origin = _origin_for_account(account)
    await callback.message.edit_text(
        _account_detail_text(account),
        reply_markup=account_detail_menu(
            account.id,
            account_stage=account.account_stage,
            origin=new_origin,
            ref_id=0,
            page=0,
        ),
    )
    if target_stage == "reg":
        done = f"Аккаунт перенесен в РЕГ {service_label(registration_service)}."
    else:
        done = "Аккаунт перенесен в НЕРЕГ."
    await callback.answer(done)
