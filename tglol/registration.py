REGISTRATION_SERVICES = ("imo", "bebe", "whoosh")


def is_registration_service(value: str | None) -> bool:
    return value in REGISTRATION_SERVICES


def service_label(value: str | None) -> str:
    return value.upper() if value in REGISTRATION_SERVICES else "без сервиса"


def normalize_services(values) -> tuple[str, ...]:
    result: list[str] = []
    for value in values or ():
        if value in REGISTRATION_SERVICES and value not in result:
            result.append(value)
    return tuple(result)


def services_to_storage(values) -> str | None:
    services = normalize_services(values)
    return ",".join(services) if services else None


def services_from_storage(raw: str | None, legacy_service: str | None = None) -> tuple[str, ...]:
    values: list[str] = []
    if raw:
        values.extend(part.strip() for part in raw.split(","))
    elif legacy_service:
        values.append(legacy_service)
    return normalize_services(values)


def services_label(raw: str | None, legacy_service: str | None = None) -> str:
    services = services_from_storage(raw, legacy_service)
    return ", ".join(service_label(service) for service in services) if services else "без сервиса"


def service_filter_label(
    registration_service: str | None = None,
    excluded_service: str | None = None,
) -> str:
    if registration_service:
        return service_label(registration_service)
    if excluded_service:
        return f"не {service_label(excluded_service)}"
    return "Все"


def reg_origin(
    registration_service: str | None = None,
    excluded_service: str | None = None,
) -> str:
    if registration_service:
        return f"common_reg_{registration_service}"
    if excluded_service:
        return f"common_reg_not_{excluded_service}"
    return "common_reg"


def parse_reg_origin(origin: str) -> tuple[str | None, str | None] | None:
    if origin == "common_reg":
        return None, None
    if origin.startswith("common_reg_not_"):
        service = origin.removeprefix("common_reg_not_")
        return (None, service) if is_registration_service(service) else None
    if origin.startswith("common_reg_"):
        service = origin.removeprefix("common_reg_")
        return (service, None) if is_registration_service(service) else None
    return None


def parse_service_filter(value: str) -> tuple[str | None, str | None] | None:
    if value == "all":
        return None, None
    if value.startswith("not_"):
        service = value.removeprefix("not_")
        return (None, service) if is_registration_service(service) else None
    return (value, None) if is_registration_service(value) else None


def service_filter_value(
    registration_service: str | None = None,
    excluded_service: str | None = None,
) -> str:
    if registration_service:
        return registration_service
    if excluded_service:
        return f"not_{excluded_service}"
    return "all"
