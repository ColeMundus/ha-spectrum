from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import probatio as vol
from aiospectrum import (
    DAYS_OF_WEEK,
    PROTOCOLS,
    SPEED_TEST_OPERATIONS,
    AmbiguousMatchError,
    ExternalPortImmutableError,
    ResolutionError,
    Router,
    SpectrumAuthError,
    SpectrumConnectionError,
    SpectrumError,
    SpectrumValidationError,
    is_mac_like,
    normalize_mac,
    resolve_client,
    resolve_client_group,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import SpectrumConfigEntry, SpectrumRuntimeData
from .entity import parse_equipment_identifier

SERVICE_ADD_PORT_FORWARD = "add_port_forward"
SERVICE_UPDATE_PORT_FORWARD = "update_port_forward"
SERVICE_DELETE_PORT_FORWARD = "delete_port_forward"
SERVICE_LIST_PORT_FORWARDS = "list_port_forwards"
SERVICE_SET_IP_RESERVATION = "set_ip_reservation"
SERVICE_DELETE_IP_RESERVATION = "delete_ip_reservation"
SERVICE_SET_ACCESS_SCHEDULE = "set_access_schedule"
SERVICE_SET_DNS = "set_dns"
SERVICE_UPDATE_WIFI = "update_wifi"
SERVICE_SET_ADDITIONAL_NETWORK = "set_additional_network"
SERVICE_DELETE_ADDITIONAL_NETWORK = "delete_additional_network"
SERVICE_RENAME_CLIENT = "rename_client"
SERVICE_REMOVE_CLIENT = "remove_client"
SERVICE_CREATE_CLIENT_GROUP = "create_client_group"
SERVICE_UPDATE_CLIENT_GROUP = "update_client_group"
SERVICE_DELETE_CLIENT_GROUP = "delete_client_group"
SERVICE_RUN_SPEED_TEST = "run_speed_test"

ATTR_DEVICE_ID = "device_id"
ATTR_CLIENT = "client"
ATTR_CLIENTS = "clients"
ATTR_GROUP = "group"
ATTR_EXTERNAL_PORT = "external_port"
ATTR_INTERNAL_PORT = "internal_port"
ATTR_NEW_EXTERNAL_PORT = "new_external_port"
ATTR_PROTOCOL = "protocol"
ATTR_NAME = "name"
ATTR_NEW_NAME = "new_name"
ATTR_IP_ADDRESS = "ip_address"
ATTR_AUTO_RESERVE = "auto_reserve"
ATTR_RECREATE = "recreate"
ATTR_TARGET_TYPE = "target_type"
ATTR_TARGET = "target"
ATTR_START_TIME = "start_time"
ATTR_END_TIME = "end_time"
ATTR_DAYS_OF_WEEK = "days_of_week"
ATTR_ENABLED = "enabled"
ATTR_CREATE = "create"
ATTR_CUSTOM_ENABLED = "custom_enabled"
ATTR_PRIMARY = "primary"
ATTR_SECONDARY = "secondary"
ATTR_SSID = "ssid"
ATTR_PASSWORD = "password"
ATTR_ACCESS_POINT_ID = "access_point_id"
ATTR_BROADCAST = "broadcast"
ATTR_ICON_REFERENCE = "icon_reference"
ATTR_OPERATION = "operation"
ATTR_WAIT = "wait"

_PORT_VALIDATOR = vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))
_DEVICE_ID_VALIDATOR = vol.Any(cv.string, vol.All(cv.ensure_list, [cv.string]))
_PROTOCOL_VALIDATOR = vol.All(cv.string, vol.Upper, vol.In(PROTOCOLS))
_DAY_VALIDATOR = vol.All(cv.string, vol.Upper, vol.In(DAYS_OF_WEEK))
_TARGET_TYPE_VALIDATOR = vol.All(
    cv.string,
    vol.Lower,
    vol.In(("client", "client_group", "device", "device_group")),
)
_SPEED_TEST_OP_VALIDATOR = vol.All(cv.string, vol.Upper, vol.In(SPEED_TEST_OPERATIONS))

_BASE_TARGET_SCHEMA: dict[Any, Any] = {
    vol.Required(ATTR_DEVICE_ID): _DEVICE_ID_VALIDATOR,
}

ADD_PORT_FORWARD_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Required(ATTR_CLIENT): cv.string,
        vol.Required(ATTR_EXTERNAL_PORT): _PORT_VALIDATOR,
        vol.Optional(ATTR_INTERNAL_PORT): _PORT_VALIDATOR,
        vol.Optional(ATTR_PROTOCOL, default="TCP"): _PROTOCOL_VALIDATOR,
        vol.Optional(ATTR_NAME): cv.string,
        vol.Optional(ATTR_IP_ADDRESS): cv.string,
        vol.Optional(ATTR_AUTO_RESERVE, default=False): cv.boolean,
    }
)

UPDATE_PORT_FORWARD_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Required(ATTR_CLIENT): cv.string,
        vol.Required(ATTR_EXTERNAL_PORT): _PORT_VALIDATOR,
        vol.Optional(ATTR_INTERNAL_PORT): _PORT_VALIDATOR,
        vol.Optional(ATTR_PROTOCOL): _PROTOCOL_VALIDATOR,
        vol.Optional(ATTR_NAME): cv.string,
        vol.Optional(ATTR_NEW_EXTERNAL_PORT): _PORT_VALIDATOR,
        vol.Optional(ATTR_RECREATE, default=False): cv.boolean,
    }
)

DELETE_PORT_FORWARD_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Required(ATTR_CLIENT): cv.string,
        vol.Required(ATTR_EXTERNAL_PORT): _PORT_VALIDATOR,
    }
)

LIST_PORT_FORWARDS_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Optional(ATTR_CLIENT): cv.string,
    }
)

SET_IP_RESERVATION_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Required(ATTR_CLIENT): cv.string,
        vol.Required(ATTR_IP_ADDRESS): cv.string,
    }
)

DELETE_IP_RESERVATION_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Required(ATTR_CLIENT): cv.string,
    }
)

SET_ACCESS_SCHEDULE_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Required(ATTR_TARGET_TYPE): _TARGET_TYPE_VALIDATOR,
        vol.Required(ATTR_TARGET): cv.string,
        vol.Required(ATTR_NAME): cv.string,
        vol.Required(ATTR_START_TIME): cv.string,
        vol.Required(ATTR_END_TIME): cv.string,
        vol.Required(ATTR_DAYS_OF_WEEK): vol.All(cv.ensure_list, [_DAY_VALIDATOR]),
        vol.Optional(ATTR_ENABLED, default=True): cv.boolean,
        vol.Optional(ATTR_CREATE, default=False): cv.boolean,
    }
)

SET_DNS_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Required(ATTR_CUSTOM_ENABLED): cv.boolean,
        vol.Optional(ATTR_PRIMARY): cv.string,
        vol.Optional(ATTR_SECONDARY): cv.string,
    }
)

UPDATE_WIFI_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Required(ATTR_SSID): cv.string,
        vol.Required(ATTR_PASSWORD): cv.string,
        vol.Optional(ATTR_ACCESS_POINT_ID): cv.string,
    }
)

SET_ADDITIONAL_NETWORK_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Required(ATTR_SSID): cv.string,
        vol.Required(ATTR_PASSWORD): cv.string,
        vol.Optional(ATTR_BROADCAST, default=True): cv.boolean,
        vol.Optional(ATTR_CREATE, default=False): cv.boolean,
    }
)

DELETE_ADDITIONAL_NETWORK_SCHEMA = vol.Schema(dict(_BASE_TARGET_SCHEMA))

RENAME_CLIENT_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Required(ATTR_CLIENT): cv.string,
        vol.Required(ATTR_NEW_NAME): cv.string,
        vol.Optional(ATTR_ICON_REFERENCE, default="DEFAULT"): cv.string,
    }
)

REMOVE_CLIENT_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Required(ATTR_CLIENT): cv.string,
    }
)

CREATE_CLIENT_GROUP_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Required(ATTR_NAME): cv.string,
        vol.Optional(ATTR_CLIENTS, default=[]): vol.All(cv.ensure_list, [cv.string]),
    }
)

UPDATE_CLIENT_GROUP_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Required(ATTR_GROUP): cv.string,
        vol.Required(ATTR_NAME): cv.string,
        vol.Required(ATTR_CLIENTS): vol.All(cv.ensure_list, [cv.string]),
    }
)

DELETE_CLIENT_GROUP_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Required(ATTR_GROUP): cv.string,
    }
)

RUN_SPEED_TEST_SCHEMA = vol.Schema(
    {
        **_BASE_TARGET_SCHEMA,
        vol.Optional(ATTR_OPERATION): _SPEED_TEST_OP_VALIDATOR,
        vol.Optional(ATTR_WAIT, default=True): cv.boolean,
    }
)


def _extract_single_device_id(raw_device_id: str | list[str]) -> str:
    if isinstance(raw_device_id, str):
        cleaned = raw_device_id.strip()
        if cleaned:
            return cleaned
    elif isinstance(raw_device_id, list) and len(raw_device_id) == 1:
        cleaned = str(raw_device_id[0]).strip()
        if cleaned:
            return cleaned
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="invalid_input",
        translation_placeholders={"detail": "Select exactly one Spectrum device."},
    )


def _resolve_equipment_target(
    hass: HomeAssistant,
    raw_device_id: str | list[str],
) -> tuple[SpectrumConfigEntry, str]:
    device_id = _extract_single_device_id(raw_device_id)
    device_entry = dr.async_get(hass).async_get(device_id)
    if device_entry is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
        )

    parsed: tuple[str, str] | None = None
    for identifier in device_entry.identifiers:
        parsed = parse_equipment_identifier(identifier)
        if parsed is not None:
            break

    if parsed is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_equipment_device",
        )

    entry_id, equipment_id = parsed
    entry = hass.config_entries.async_get_entry(entry_id)
    if (
        entry is None
        or entry.domain != DOMAIN
        or entry.state is not ConfigEntryState.LOADED
        or not hasattr(entry, "runtime_data")
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
        )

    return entry, equipment_id


def _resolve_router_target(
    hass: HomeAssistant,
    raw_device_id: str | list[str],
) -> tuple[SpectrumConfigEntry, Router]:
    entry, equipment_id = _resolve_equipment_target(hass, raw_device_id)
    runtime = entry.runtime_data
    bundle = runtime.equipment.data
    if bundle is not None:
        for router in bundle.equipment.routers:
            if router.id == equipment_id:
                return entry, router
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="not_router_device",
    )


def _resolve_speed_test_target(
    hass: HomeAssistant,
    raw_device_id: str | list[str],
) -> tuple[SpectrumConfigEntry, str, bool]:
    entry, equipment_id = _resolve_equipment_target(hass, raw_device_id)
    bundle = entry.runtime_data.equipment.data
    if bundle is not None:
        for router in bundle.equipment.routers:
            if router.id == equipment_id:
                return entry, router.id, True
        for modem in bundle.equipment.modems:
            if modem.id == equipment_id:
                return entry, modem.id, False
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="not_speed_test_device",
    )


def _router_clients(runtime: SpectrumRuntimeData, router_id: str) -> list[Any]:
    network_data = runtime.network.data
    if network_data is None:
        return []
    scoped = [
        c for c in network_data.clients if not c.router_id or c.router_id == router_id
    ]
    return scoped if scoped else list(network_data.clients)


def _router_client_groups(runtime: SpectrumRuntimeData, router_id: str) -> list[Any]:
    network_data = runtime.network.data
    if network_data is None:
        return []
    scoped = [
        g
        for g in network_data.client_groups
        if not g.router_id or g.router_id == router_id
    ]
    return scoped if scoped else list(network_data.client_groups)


def _resolve_client_mac(
    runtime: SpectrumRuntimeData, router_id: str, selector_value: str
) -> str:
    clients = _router_clients(runtime, router_id)
    if clients:
        try:
            matched = resolve_client(clients, selector_value)
            if matched.mac_address:
                return matched.mac_address
        except ResolutionError:
            if not is_mac_like(selector_value):
                raise
    if is_mac_like(selector_value):
        return normalize_mac(selector_value)
    raise ResolutionError(
        f"No Client matched selector {selector_value!r}.",
        kind="client",
        selector=selector_value,
    )


def _resolve_client_id(
    runtime: SpectrumRuntimeData, router_id: str, selector_value: str
) -> str:
    clients = _router_clients(runtime, router_id)
    matched = resolve_client(clients, selector_value)
    if not matched.id:
        raise ResolutionError(
            f"Client {selector_value!r} has no Spectrum Client ID.",
            kind="client",
            selector=selector_value,
        )
    return matched.id


def _resolve_group_id(
    runtime: SpectrumRuntimeData, router_id: str, selector_value: str
) -> str:
    groups = _router_client_groups(runtime, router_id)
    matched = resolve_client_group(groups, selector_value)
    if not matched.id:
        raise ResolutionError(
            f"Client Group {selector_value!r} has no Spectrum Client Group ID.",
            kind="client_group",
            selector=selector_value,
        )
    return matched.id


def _normalize_time_hhmm(value: str) -> str:
    cleaned = value.strip()
    parts = cleaned.split(":")
    if len(parts) == 3:
        return f"{parts[0]}:{parts[1]}"
    return cleaned


async def _run_service_action[ResultT](
    hass: HomeAssistant,
    entry: SpectrumConfigEntry,
    operation: Callable[[], Awaitable[ResultT]],
) -> ResultT:
    try:
        return await operation()
    except ExternalPortImmutableError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="external_port_immutable",
        ) from err
    except (SpectrumValidationError, AmbiguousMatchError, ResolutionError) as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_input",
            translation_placeholders={"detail": str(err)},
        ) from err
    except SpectrumAuthError as err:
        entry.async_start_reauth(hass)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="auth_failed",
        ) from err
    except SpectrumConnectionError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="connection_failed",
            translation_placeholders={"detail": str(err)},
        ) from err
    except SpectrumError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="api_error",
            translation_placeholders={"error": str(err)},
        ) from err


def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_ADD_PORT_FORWARD):
        return

    async def _handle_add_port_forward(call: ServiceCall) -> ServiceResponse:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> dict[str, Any]:
            client_mac = _resolve_client_mac(runtime, router.id, call.data[ATTR_CLIENT])
            result = await runtime.client.async_add_port_forward(
                router.id,
                client_mac,
                external_port=call.data[ATTR_EXTERNAL_PORT],
                internal_port=call.data.get(ATTR_INTERNAL_PORT),
                protocol=call.data[ATTR_PROTOCOL],
                name=call.data.get(ATTR_NAME),
                ip_address=call.data.get(ATTR_IP_ADDRESS),
                auto_reserve=call.data[ATTR_AUTO_RESERVE],
            )
            await runtime.network.async_request_refresh()
            return result.to_dict()

        payload = await _run_service_action(hass, entry, _action)
        return payload if call.return_response else None

    async def _handle_update_port_forward(call: ServiceCall) -> ServiceResponse:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> dict[str, Any]:
            client_mac = _resolve_client_mac(runtime, router.id, call.data[ATTR_CLIENT])
            result = await runtime.client.async_update_port_forward(
                router.id,
                client_mac,
                call.data[ATTR_EXTERNAL_PORT],
                internal_port=call.data.get(ATTR_INTERNAL_PORT),
                protocol=call.data.get(ATTR_PROTOCOL),
                name=call.data.get(ATTR_NAME),
                new_external_port=call.data.get(ATTR_NEW_EXTERNAL_PORT),
                recreate=call.data[ATTR_RECREATE],
            )
            await runtime.network.async_request_refresh()
            return result.to_dict()

        payload = await _run_service_action(hass, entry, _action)
        return payload if call.return_response else None

    async def _handle_delete_port_forward(call: ServiceCall) -> None:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> None:
            client_mac = _resolve_client_mac(runtime, router.id, call.data[ATTR_CLIENT])
            await runtime.client.async_delete_port_forward(
                router.id,
                client_mac,
                call.data[ATTR_EXTERNAL_PORT],
            )
            await runtime.network.async_request_refresh()

        await _run_service_action(hass, entry, _action)

    async def _handle_list_port_forwards(call: ServiceCall) -> ServiceResponse:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> dict[str, Any]:
            client_selector = call.data.get(ATTR_CLIENT)
            if client_selector:
                client_mac = _resolve_client_mac(runtime, router.id, client_selector)
                items = await runtime.client.async_list_port_forwards(
                    router.id, client_mac
                )
                return {"port_forwards": [item.to_dict() for item in items]}

            network_data = runtime.network.data
            if network_data is None:
                return {"port_forwards": []}
            matching_res_ids = {
                res.id
                for res in network_data.ip_reservations
                if res.id and (not res.router_id or res.router_id == router.id)
            }
            matching_macs = {
                normalize_mac(res.mac_address)
                for res in network_data.ip_reservations
                if res.mac_address and (not res.router_id or res.router_id == router.id)
            }
            items = [
                pf
                for pf in network_data.port_forwards
                if (not matching_res_ids and not matching_macs)
                or (pf.reservation_id in matching_res_ids)
                or (pf.mac_address and normalize_mac(pf.mac_address) in matching_macs)
            ]
            return {"port_forwards": [item.to_dict() for item in items]}

        return await _run_service_action(hass, entry, _action)

    async def _handle_set_ip_reservation(call: ServiceCall) -> ServiceResponse:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> dict[str, Any]:
            client_mac = _resolve_client_mac(runtime, router.id, call.data[ATTR_CLIENT])
            result = await runtime.client.async_set_ip_reservation(
                router.id,
                client_mac,
                call.data[ATTR_IP_ADDRESS],
            )
            await runtime.network.async_request_refresh()
            return result.to_dict()

        payload = await _run_service_action(hass, entry, _action)
        return payload if call.return_response else None

    async def _handle_delete_ip_reservation(call: ServiceCall) -> None:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> None:
            client_mac = _resolve_client_mac(runtime, router.id, call.data[ATTR_CLIENT])
            await runtime.client.async_delete_ip_reservation(router.id, client_mac)
            await runtime.network.async_request_refresh()

        await _run_service_action(hass, entry, _action)

    async def _handle_set_access_schedule(call: ServiceCall) -> ServiceResponse:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> dict[str, Any]:
            target_type = call.data[ATTR_TARGET_TYPE]
            target_selector = call.data[ATTR_TARGET]
            start_time = _normalize_time_hhmm(call.data[ATTR_START_TIME])
            end_time = _normalize_time_hhmm(call.data[ATTR_END_TIME])
            if target_type in ("client_group", "device_group"):
                group_id = _resolve_group_id(runtime, router.id, target_selector)
                schedule = await runtime.client.async_set_group_schedule(
                    router.id,
                    group_id,
                    name=call.data[ATTR_NAME],
                    start_time=start_time,
                    end_time=end_time,
                    days_of_week=call.data[ATTR_DAYS_OF_WEEK],
                    enabled=call.data[ATTR_ENABLED],
                    create=call.data[ATTR_CREATE],
                )
            else:
                client_id = _resolve_client_id(runtime, router.id, target_selector)
                schedule = await runtime.client.async_set_client_schedule(
                    router.id,
                    client_id,
                    name=call.data[ATTR_NAME],
                    start_time=start_time,
                    end_time=end_time,
                    days_of_week=call.data[ATTR_DAYS_OF_WEEK],
                    enabled=call.data[ATTR_ENABLED],
                    create=call.data[ATTR_CREATE],
                )
            await runtime.network.async_request_refresh()
            return schedule.to_dict()

        payload = await _run_service_action(hass, entry, _action)
        return payload if call.return_response else None

    async def _handle_set_dns(call: ServiceCall) -> ServiceResponse:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> dict[str, Any]:
            result = await runtime.client.async_set_dns(
                router.id,
                custom_enabled=call.data[ATTR_CUSTOM_ENABLED],
                primary=call.data.get(ATTR_PRIMARY),
                secondary=call.data.get(ATTR_SECONDARY),
            )
            await runtime.equipment.async_request_refresh()
            return result.to_dict()

        payload = await _run_service_action(hass, entry, _action)
        return payload if call.return_response else None

    async def _handle_update_wifi(call: ServiceCall) -> None:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> None:
            await runtime.client.async_update_wifi(
                router.id,
                call.data[ATTR_SSID],
                call.data[ATTR_PASSWORD],
                access_point_id=call.data.get(ATTR_ACCESS_POINT_ID),
            )
            await runtime.equipment.async_request_refresh()

        await _run_service_action(hass, entry, _action)

    async def _handle_set_additional_network(call: ServiceCall) -> ServiceResponse:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> dict[str, Any]:
            should_create = (
                bool(call.data[ATTR_CREATE]) or not router.additional_networks
            )
            if should_create:
                result = await runtime.client.async_create_additional_network(
                    router.id,
                    call.data[ATTR_SSID],
                    call.data[ATTR_PASSWORD],
                    broadcast=call.data[ATTR_BROADCAST],
                )
            else:
                result = await runtime.client.async_update_additional_network(
                    router.id,
                    call.data[ATTR_SSID],
                    call.data[ATTR_PASSWORD],
                    broadcast=call.data[ATTR_BROADCAST],
                )
            await runtime.equipment.async_request_refresh()
            return result.to_dict()

        payload = await _run_service_action(hass, entry, _action)
        return payload if call.return_response else None

    async def _handle_delete_additional_network(call: ServiceCall) -> None:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> None:
            await runtime.client.async_delete_additional_network(router.id)
            await runtime.equipment.async_request_refresh()

        await _run_service_action(hass, entry, _action)

    async def _handle_rename_client(call: ServiceCall) -> None:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> None:
            client_id = _resolve_client_id(runtime, router.id, call.data[ATTR_CLIENT])
            await runtime.client.async_rename_client(
                router.id,
                client_id,
                call.data[ATTR_NEW_NAME],
                icon_reference=call.data[ATTR_ICON_REFERENCE],
            )
            await runtime.network.async_request_refresh()

        await _run_service_action(hass, entry, _action)

    async def _handle_remove_client(call: ServiceCall) -> None:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> None:
            client_id = _resolve_client_id(runtime, router.id, call.data[ATTR_CLIENT])
            await runtime.client.async_remove_client(router.id, client_id)
            await runtime.network.async_request_refresh()

        await _run_service_action(hass, entry, _action)

    async def _handle_create_client_group(call: ServiceCall) -> ServiceResponse:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> dict[str, Any]:
            client_ids = [
                _resolve_client_id(runtime, router.id, selector_value)
                for selector_value in call.data[ATTR_CLIENTS]
            ]
            result = await runtime.client.async_create_client_group(
                router.id,
                call.data[ATTR_NAME],
                client_ids,
            )
            await runtime.network.async_request_refresh()
            return result.to_dict()

        payload = await _run_service_action(hass, entry, _action)
        return payload if call.return_response else None

    async def _handle_update_client_group(call: ServiceCall) -> ServiceResponse:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> dict[str, Any]:
            group_id = _resolve_group_id(runtime, router.id, call.data[ATTR_GROUP])
            client_ids = [
                _resolve_client_id(runtime, router.id, selector_value)
                for selector_value in call.data[ATTR_CLIENTS]
            ]
            result = await runtime.client.async_update_client_group(
                router.id,
                group_id,
                call.data[ATTR_NAME],
                client_ids,
            )
            await runtime.network.async_request_refresh()
            return result.to_dict()

        payload = await _run_service_action(hass, entry, _action)
        return payload if call.return_response else None

    async def _handle_delete_client_group(call: ServiceCall) -> None:
        entry, router = _resolve_router_target(hass, call.data[ATTR_DEVICE_ID])
        runtime = entry.runtime_data

        async def _action() -> None:
            group_id = _resolve_group_id(runtime, router.id, call.data[ATTR_GROUP])
            await runtime.client.async_delete_client_group(router.id, group_id)
            await runtime.network.async_request_refresh()

        await _run_service_action(hass, entry, _action)

    async def _handle_run_speed_test(call: ServiceCall) -> ServiceResponse:
        entry, equipment_id, is_router = _resolve_speed_test_target(
            hass, call.data[ATTR_DEVICE_ID]
        )
        runtime = entry.runtime_data
        default_operation = "ROUTER" if is_router else "MODEM_DOWNLOAD"
        operation = str(call.data.get(ATTR_OPERATION) or default_operation)

        async def _action() -> dict[str, Any]:
            result = await runtime.client.async_run_speed_test(
                equipment_id,
                operation=operation,
                wait=call.data[ATTR_WAIT],
            )
            await runtime.equipment.async_request_refresh()
            return result.to_dict()

        payload = await _run_service_action(hass, entry, _action)
        return payload if call.return_response else None

    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_PORT_FORWARD,
        _handle_add_port_forward,
        schema=ADD_PORT_FORWARD_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_PORT_FORWARD,
        _handle_update_port_forward,
        schema=UPDATE_PORT_FORWARD_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_PORT_FORWARD,
        _handle_delete_port_forward,
        schema=DELETE_PORT_FORWARD_SCHEMA,
        supports_response=SupportsResponse.NONE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_PORT_FORWARDS,
        _handle_list_port_forwards,
        schema=LIST_PORT_FORWARDS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_IP_RESERVATION,
        _handle_set_ip_reservation,
        schema=SET_IP_RESERVATION_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_IP_RESERVATION,
        _handle_delete_ip_reservation,
        schema=DELETE_IP_RESERVATION_SCHEMA,
        supports_response=SupportsResponse.NONE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_ACCESS_SCHEDULE,
        _handle_set_access_schedule,
        schema=SET_ACCESS_SCHEDULE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_DNS,
        _handle_set_dns,
        schema=SET_DNS_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_WIFI,
        _handle_update_wifi,
        schema=UPDATE_WIFI_SCHEMA,
        supports_response=SupportsResponse.NONE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_ADDITIONAL_NETWORK,
        _handle_set_additional_network,
        schema=SET_ADDITIONAL_NETWORK_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_ADDITIONAL_NETWORK,
        _handle_delete_additional_network,
        schema=DELETE_ADDITIONAL_NETWORK_SCHEMA,
        supports_response=SupportsResponse.NONE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RENAME_CLIENT,
        _handle_rename_client,
        schema=RENAME_CLIENT_SCHEMA,
        supports_response=SupportsResponse.NONE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_CLIENT,
        _handle_remove_client,
        schema=REMOVE_CLIENT_SCHEMA,
        supports_response=SupportsResponse.NONE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_CLIENT_GROUP,
        _handle_create_client_group,
        schema=CREATE_CLIENT_GROUP_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_CLIENT_GROUP,
        _handle_update_client_group,
        schema=UPDATE_CLIENT_GROUP_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_CLIENT_GROUP,
        _handle_delete_client_group,
        schema=DELETE_CLIENT_GROUP_SCHEMA,
        supports_response=SupportsResponse.NONE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_SPEED_TEST,
        _handle_run_speed_test,
        schema=RUN_SPEED_TEST_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
