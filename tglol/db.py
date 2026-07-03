from __future__ import annotations

from dataclasses import dataclass, fields
import sqlite3
from typing import Any

from tglol.config import Config
from tglol.registration import services_to_storage


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT,
    telegram_user_id INTEGER,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    session_path TEXT NOT NULL,
    json_original_path TEXT,
    json_effective_path TEXT,
    json_source TEXT NOT NULL,
    twofa_password TEXT,
    source_type TEXT NOT NULL,
    account_stage TEXT NOT NULL DEFAULT 'nereg',
    registration_service TEXT,
    registration_services TEXT,
    status TEXT NOT NULL,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Account:
    id: int
    phone: str | None
    telegram_user_id: int | None
    username: str | None
    first_name: str | None
    last_name: str | None
    session_path: str
    json_original_path: str | None
    json_effective_path: str | None
    json_source: str
    twofa_password: str | None
    source_type: str
    account_stage: str
    registration_service: str | None
    registration_services: str | None
    status: str
    created_by: int | None
    created_at: str
    updated_at: str


def connect(config: Config) -> sqlite3.Connection:
    connection = sqlite3.connect(config.db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(config: Config) -> None:
    with connect(config) as connection:
        connection.executescript(SCHEMA)
        _ensure_column(connection, "accounts", "account_stage", "TEXT NOT NULL DEFAULT 'nereg'")
        _ensure_column(connection, "accounts", "registration_service", "TEXT")
        _ensure_column(connection, "accounts", "registration_services", "TEXT")
        connection.execute(
            """
            UPDATE accounts
            SET registration_services = registration_service
            WHERE registration_services IS NULL
              AND registration_service IS NOT NULL
              AND registration_service != ''
            """
        )


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in _table_columns(connection, table):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _account_from_row(row: sqlite3.Row) -> Account:
    values = dict(row)
    names = {field.name for field in fields(Account)}
    return Account(**{name: values.get(name) for name in names})


def add_account(config: Config, values: dict[str, Any]) -> int:
    columns = ", ".join(values.keys())
    placeholders = ", ".join(f":{key}" for key in values)
    with connect(config) as connection:
        cursor = connection.execute(
            f"INSERT INTO accounts ({columns}) VALUES ({placeholders})",
            values,
        )
        return int(cursor.lastrowid)


def list_accounts(
    config: Config,
    limit: int = 20,
    offset: int = 0,
    account_stage: str | None = None,
    registration_service: str | None = None,
    excluded_registration_service: str | None = None,
) -> list[Account]:
    clauses: list[str] = []
    params: list[Any] = []
    if account_stage is not None:
        clauses.append("account_stage = ?")
        params.append(account_stage)
    if registration_service is not None:
        clauses.append("instr(',' || coalesce(nullif(registration_services, ''), registration_service, '') || ',', ?) > 0")
        params.append(f",{registration_service},")
    if excluded_registration_service is not None:
        clauses.append("instr(',' || coalesce(nullif(registration_services, ''), registration_service, '') || ',', ?) = 0")
        params.append(f",{excluded_registration_service},")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with connect(config) as connection:
        rows = connection.execute(
            f"SELECT * FROM accounts {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    return [_account_from_row(row) for row in rows]


def list_accounts_by_scope(
    config: Config,
    *,
    account_stage: str | None = None,
    registration_service: str | None = None,
    excluded_registration_service: str | None = None,
) -> list[Account]:
    return list_accounts(
        config,
        limit=100000,
        offset=0,
        account_stage=account_stage,
        registration_service=registration_service,
        excluded_registration_service=excluded_registration_service,
    )


def get_account(config: Config, account_id: int) -> Account | None:
    with connect(config) as connection:
        row = connection.execute(
            "SELECT * FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
    return _account_from_row(row) if row else None


def count_accounts(config: Config) -> int:
    return count_accounts_by_stage(config)


def count_accounts_by_stage(
    config: Config,
    account_stage: str | None = None,
    registration_service: str | None = None,
    excluded_registration_service: str | None = None,
) -> int:
    clauses: list[str] = []
    params: list[Any] = []
    if account_stage is not None:
        clauses.append("account_stage = ?")
        params.append(account_stage)
    if registration_service is not None:
        clauses.append("instr(',' || coalesce(nullif(registration_services, ''), registration_service, '') || ',', ?) > 0")
        params.append(f",{registration_service},")
    if excluded_registration_service is not None:
        clauses.append("instr(',' || coalesce(nullif(registration_services, ''), registration_service, '') || ',', ?) = 0")
        params.append(f",{excluded_registration_service},")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with connect(config) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM accounts {where}", params).fetchone()[0])


def set_account_stage(
    config: Config,
    account_id: int,
    account_stage: str,
    registration_service: str | None = None,
    registration_services=None,
) -> None:
    if account_stage not in {"nereg", "reg"}:
        raise ValueError("unknown account stage")
    if account_stage == "reg":
        if registration_services is None and registration_service is not None:
            registration_services = (registration_service,)
        stored_services = services_to_storage(registration_services)
        if not stored_services:
            raise ValueError("unknown registration service")
        first_service = stored_services.split(",", 1)[0]
    else:
        stored_services = None
        first_service = None
    with connect(config) as connection:
        connection.execute(
            """
            UPDATE accounts
            SET account_stage = ?,
                registration_service = ?,
                registration_services = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (account_stage, first_service, stored_services, account_id),
        )


def update_account_status(config: Config, account_id: int, status: str) -> None:
    with connect(config) as connection:
        connection.execute(
            "UPDATE accounts SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, account_id),
        )


def delete_account_row(config: Config, account_id: int) -> None:
    with connect(config) as connection:
        connection.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def delete_accounts_by_stage(
    config: Config,
    *,
    account_stage: str,
    registration_service: str | None = None,
    excluded_registration_service: str | None = None,
) -> int:
    if account_stage not in {"nereg", "reg"}:
        raise ValueError("unknown account stage")
    clauses = ["account_stage = ?"]
    params: list[Any] = [account_stage]
    if registration_service is not None:
        clauses.append("instr(',' || coalesce(nullif(registration_services, ''), registration_service, '') || ',', ?) > 0")
        params.append(f",{registration_service},")
    if excluded_registration_service is not None:
        clauses.append("instr(',' || coalesce(nullif(registration_services, ''), registration_service, '') || ',', ?) = 0")
        params.append(f",{excluded_registration_service},")
    with connect(config) as connection:
        cursor = connection.execute(
            f"DELETE FROM accounts WHERE {' AND '.join(clauses)}",
            params,
        )
        return int(cursor.rowcount or 0)
