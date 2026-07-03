from __future__ import annotations

from collections.abc import Sequence
from math import ceil

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from tglol.registration import (
    REGISTRATION_SERVICES,
    parse_reg_origin,
    reg_origin,
    service_filter_label,
    service_filter_value,
    service_label,
    services_label,
    services_short_label,
)


ACCOUNTS_PER_PAGE = 14


def accounts_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Добавить аккаунт", callback_data="accounts:add")
    builder.button(text="Хранилище", callback_data="accounts:common_sections")
    builder.adjust(1)
    return builder.as_markup()


def add_account_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="По номеру + код", callback_data="accounts:add:code")
    builder.button(text="Массово .zip", callback_data="accounts:add:zip")
    builder.button(text="Назад", callback_data="accounts:menu")
    builder.adjust(1)
    return builder.as_markup()


def digit_code_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for digit in "123456789":
        builder.button(text=digit, callback_data=f"code:digit:{digit}")
    builder.button(text="Очистить", callback_data="code:clear")
    builder.button(text="0", callback_data="code:digit:0")
    builder.button(text="Стереть", callback_data="code:backspace")
    builder.button(text="Подтвердить", callback_data="code:done")
    builder.button(text="Отмена", callback_data="accounts:add")
    builder.adjust(3, 3, 3, 3, 1, 1)
    return builder.as_markup()


def _account_label(account) -> str:
    name = account.phone or account.username or str(account.telegram_user_id or "без данных")
    if account.account_stage == "reg":
        stage = services_short_label(account.registration_services, account.registration_service)
    else:
        stage = "nereg"
    return f"{name} | {stage}"


def _origin_stage(origin: str) -> str | None:
    if origin == "common_nereg":
        return "nereg"
    if origin == "common_reg" or parse_reg_origin(origin) is not None:
        return "reg"
    return None


def accounts_page_keyboard(
    accounts: Sequence,
    *,
    total: int,
    page: int,
    origin: str,
    ref_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for account in accounts:
        builder.button(
            text=_account_label(account),
            callback_data=f"account:open:{account.id}:{origin}:{ref_id}:{page}",
        )

    pages = max(1, ceil(total / ACCOUNTS_PER_PAGE))
    nav_count = 0
    if page > 0:
        builder.button(text="‹ Назад", callback_data=f"accounts:page:{origin}:{ref_id}:{page - 1}")
        nav_count += 1
    builder.button(text=f"{page + 1}/{pages}", callback_data="noop")
    nav_count += 1
    if page + 1 < pages:
        builder.button(text="Вперед ›", callback_data=f"accounts:page:{origin}:{ref_id}:{page + 1}")
        nav_count += 1

    action_count = 0
    reg_filter = parse_reg_origin(origin)
    if reg_filter is not None:
        registration_service, excluded_service = reg_filter
        filter_label = service_filter_label(registration_service, excluded_service)
        builder.button(text=f"Фильтр: {filter_label}", callback_data=f"accounts:reg_filter:{origin}")
        action_count += 1

    if origin == "common_nereg" and total > 0:
        builder.button(text="Скачать ZIP", callback_data="accounts:zip_common:nereg:all")
        builder.button(text="Удалить весь раздел", callback_data="accounts:delete_common_ask:nereg:all")
        action_count += 2
    elif reg_filter is not None and total > 0:
        registration_service, excluded_service = reg_filter
        filter_value = service_filter_value(registration_service, excluded_service)
        delete_label = service_filter_label(registration_service, excluded_service)
        builder.button(text="Скачать ZIP", callback_data=f"accounts:zip_common:reg:{filter_value}")
        builder.button(text=f"Удалить: {delete_label}", callback_data=f"accounts:delete_common_ask:reg:{filter_value}")
        action_count += 2

    if origin == "common_nereg" or reg_filter is not None:
        builder.button(text="К разделам хранилища", callback_data="accounts:common_sections")
        action_count += 1
    builder.button(text="Меню аккаунтов", callback_data="accounts:menu")
    action_count += 1
    builder.adjust(*([1] * len(accounts)), nav_count, *([1] * action_count))
    return builder.as_markup()


def account_detail_menu(
    account_id: int,
    *,
    account_stage: str = "nereg",
    origin: str,
    ref_id: int,
    page: int,
) -> InlineKeyboardMarkup:
    back = f"accounts:page:{origin}:{ref_id}:{page}"
    builder = InlineKeyboardBuilder()
    builder.button(text="Проверить аккаунт", callback_data=f"account:check_ask:{account_id}:{origin}:{ref_id}:{page}")
    builder.button(text="Скопировать номер", callback_data=f"accounts:phone:{account_id}")
    builder.button(text="Получить код Telegram", callback_data=f"account:telegram_code:{account_id}")
    builder.button(text="Get Verification Code", callback_data=f"account:verification_code:{account_id}")
    if account_stage == "reg":
        builder.button(
            text="Сменить сервисы",
            callback_data=f"account:service:{account_id}:{origin}:{ref_id}:{page}",
        )
        builder.button(
            text="Перенести в НЕРЕГ",
            callback_data=f"account:stage_ask1:{account_id}:nereg:{origin}:{ref_id}:{page}",
        )
    else:
        builder.button(
            text="Отметить аккаунт реганным",
            callback_data=f"account:service:{account_id}:{origin}:{ref_id}:{page}",
        )
    builder.button(text="Скачать session", callback_data=f"accounts:file:session:{account_id}")
    builder.button(text="Скачать JSON", callback_data=f"accounts:file:json:{account_id}")
    builder.button(text="Удалить аккаунт", callback_data=f"account:delete_ask:{account_id}:{origin}:{ref_id}:{page}")
    builder.button(text="Назад", callback_data=back)
    builder.adjust(1)
    return builder.as_markup()


def common_storage_sections_menu(*, nereg_count: int, reg_count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"НЕРЕГ | {nereg_count}", callback_data="accounts:page:common_nereg:0:0")
    builder.button(text=f"РЕГ | {reg_count}", callback_data="accounts:page:common_reg:0:0")
    builder.button(text="Назад", callback_data="accounts:menu")
    builder.adjust(1)
    return builder.as_markup()


def confirm_delete_account_menu(account_id: int, origin: str, ref_id: int, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="ДА, УДАЛИТЬ", callback_data=f"account:delete_confirm:{account_id}:{origin}:{ref_id}:{page}")
    builder.button(text="Отмена", callback_data=f"account:open:{account_id}:{origin}:{ref_id}:{page}")
    builder.adjust(1)
    return builder.as_markup()


def confirm_check_account_menu(account_id: int, origin: str, ref_id: int, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, проверить", callback_data=f"account:check_confirm:{account_id}:{origin}:{ref_id}:{page}")
    builder.button(text="Назад", callback_data=f"account:open:{account_id}:{origin}:{ref_id}:{page}")
    builder.adjust(1)
    return builder.as_markup()


def confirm_delete_common_stage_menu(
    stage: str,
    registration_service: str | None = None,
    excluded_service: str | None = None,
) -> InlineKeyboardMarkup:
    service_part = service_filter_value(registration_service, excluded_service)
    cancel_origin = reg_origin(registration_service, excluded_service) if stage == "reg" else f"common_{stage}"
    builder = InlineKeyboardBuilder()
    builder.button(text="ДА, УДАЛИТЬ ВСЕ", callback_data=f"accounts:delete_common_confirm:{stage}:{service_part}")
    builder.button(text="Отмена", callback_data=f"accounts:page:{cancel_origin}:0:0")
    builder.adjust(1)
    return builder.as_markup()


def confirm_account_stage_menu(
    account_id: int,
    target_stage: str,
    registration_service: str | None,
    origin: str,
    ref_id: int,
    page: int,
    step: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    next_step = "ask2" if step == 1 else "confirm"
    label = "ДА, ПРОДОЛЖИТЬ" if step == 1 else "ДА, ПОДТВЕРЖДАЮ"
    builder.button(
        text=label,
        callback_data=(
            f"account:stage_{next_step}:{account_id}:{target_stage}:"
            f"{registration_service or 'none'}:{origin}:{ref_id}:{page}"
        ),
    )
    builder.button(text="Отмена", callback_data=f"account:open:{account_id}:{origin}:{ref_id}:{page}")
    builder.adjust(1)
    return builder.as_markup()


def registration_service_menu(selected_services: Sequence[str]) -> InlineKeyboardMarkup:
    selected = set(selected_services)
    builder = InlineKeyboardBuilder()
    for service in REGISTRATION_SERVICES:
        builder.button(
            text=f"✓ {service_label(service)}" if service in selected else service_label(service),
            callback_data=f"account:service_toggle:{service}",
        )
    builder.button(text="Сохранить", callback_data="account:service_save")
    builder.button(text="Назад", callback_data="account:service_cancel")
    builder.adjust(1)
    return builder.as_markup()


def registration_filter_menu(current_origin: str) -> InlineKeyboardMarkup:
    current_filter = parse_reg_origin(current_origin) or (None, None)
    current_service, current_excluded = current_filter
    builder = InlineKeyboardBuilder()

    label = "• Все РЕГ" if current_filter == (None, None) else "Все РЕГ"
    builder.button(text=label, callback_data="accounts:page:common_reg:0:0")

    for service in REGISTRATION_SERVICES:
        include_label = service_label(service)
        exclude_label = f"не {service_label(service)}"
        if current_service == service:
            include_label = f"• {include_label}"
        if current_excluded == service:
            exclude_label = f"• {exclude_label}"
        builder.button(text=include_label, callback_data=f"accounts:page:{reg_origin(registration_service=service)}:0:0")
        builder.button(text=exclude_label, callback_data=f"accounts:page:{reg_origin(excluded_service=service)}:0:0")

    builder.button(text="Назад к списку", callback_data=f"accounts:page:{current_origin}:0:0")
    builder.adjust(1, 2, 2, 2, 1)
    return builder.as_markup()
