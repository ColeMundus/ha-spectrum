from __future__ import annotations

from typing import Any

from aiospectrum import (
    EquipmentState,
    Modem,
    Pod,
    Router,
    SetTopBox,
    format_mac,
    is_mac_like,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceEntryType,
    DeviceInfo,
)
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import ACCOUNT_MODEL, DOMAIN, MANUFACTURER

_ACCOUNT_SUFFIX = ":account"
_EQUIPMENT_MARKER = ":equipment:"


class SpectrumCoordinatorEntity[CoordinatorT: DataUpdateCoordinator[Any]](
    CoordinatorEntity[CoordinatorT]
):
    _attr_has_entity_name = True


def account_device_identifier(entry_id: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}{_ACCOUNT_SUFFIX}")


def equipment_device_identifier(entry_id: str, equipment_id: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}{_EQUIPMENT_MARKER}{equipment_id}")


def parse_account_identifier(identifier: tuple[str, str]) -> str | None:
    domain, value = identifier
    if domain != DOMAIN or not value.endswith(_ACCOUNT_SUFFIX):
        return None
    entry_id = value.removesuffix(_ACCOUNT_SUFFIX)
    return entry_id or None


def parse_equipment_identifier(identifier: tuple[str, str]) -> tuple[str, str] | None:
    domain, value = identifier
    if domain != DOMAIN or _EQUIPMENT_MARKER not in value:
        return None
    entry_id, equipment_id = value.split(_EQUIPMENT_MARKER, 1)
    if not entry_id or not equipment_id:
        return None
    return (entry_id, equipment_id)


def _mac_connections(*mac_addresses: str | None) -> set[tuple[str, str]]:
    connections: set[tuple[str, str]] = set()
    for mac in mac_addresses:
        if mac and is_mac_like(mac):
            connections.add((CONNECTION_NETWORK_MAC, format_mac(mac)))
    return connections


def find_combo_modem(equipment: EquipmentState, router: Router) -> Modem | None:
    if not router.is_combo:
        return None
    for modem in equipment.modems:
        if modem.id == router.id:
            return modem
    return None


def is_merged_combo_modem(equipment: EquipmentState, modem: Modem) -> bool:
    return any(
        router.is_combo and router.id == modem.id for router in equipment.routers
    )


def find_pod_router_id(equipment: EquipmentState, pod: Pod) -> str | None:
    if pod.network_id:
        for router in equipment.routers:
            if router.network_id == pod.network_id:
                return router.id
    if len(equipment.routers) == 1:
        return equipment.routers[0].id
    return None


def account_device_info(entry_id: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={account_device_identifier(entry_id)},
        entry_type=DeviceEntryType.SERVICE,
        manufacturer=MANUFACTURER,
        model=ACCOUNT_MODEL,
        name="Spectrum Account",
    )


def router_device_info(
    entry_id: str,
    router: Router,
    modem: Modem | None = None,
    *,
    via_device_id: str | None = None,
) -> DeviceInfo:
    merged_modem = (
        modem
        if (router.is_combo and modem is not None and modem.id == router.id)
        else None
    )
    name = (
        router.nickname
        or (merged_modem.nickname if merged_modem else None)
        or router.model
        or (merged_modem.model if merged_modem else None)
        or f"Router {router.id}"
    )
    manufacturer = (
        router.manufacturer
        or (merged_modem.manufacturer if merged_modem else None)
        or MANUFACTURER
    )
    model = router.model or (merged_modem.model if merged_modem else None)
    sw_version = router.firmware_version or (
        merged_modem.firmware_version if merged_modem else None
    )
    serial_number = router.serial_number or (
        merged_modem.serial_number if merged_modem else None
    )
    connections = _mac_connections(
        router.mac_address,
        merged_modem.mac_address if merged_modem else None,
    )

    info = DeviceInfo(
        identifiers={equipment_device_identifier(entry_id, router.id)},
        manufacturer=manufacturer,
        model=model,
        name=name,
        sw_version=sw_version,
        serial_number=serial_number,
    )
    if connections:
        info["connections"] = connections
    if via_device_id is not None:
        info["via_device_id"] = via_device_id
    return info


def modem_device_info(
    entry_id: str,
    modem: Modem,
    *,
    via_device_id: str | None = None,
) -> DeviceInfo:
    connections = _mac_connections(modem.mac_address)
    info = DeviceInfo(
        identifiers={equipment_device_identifier(entry_id, modem.id)},
        manufacturer=modem.manufacturer or MANUFACTURER,
        model=modem.model,
        name=modem.nickname or modem.model or f"Modem {modem.id}",
        sw_version=modem.firmware_version,
        serial_number=modem.serial_number,
    )
    if connections:
        info["connections"] = connections
    if via_device_id is not None:
        info["via_device_id"] = via_device_id
    return info


def pod_device_info(
    entry_id: str,
    pod: Pod,
    router_id: str | None = None,
    *,
    via_device_id: str | None = None,
) -> DeviceInfo:
    _ = router_id
    connections = _mac_connections(pod.mac_address)
    info = DeviceInfo(
        identifiers={equipment_device_identifier(entry_id, pod.id)},
        manufacturer=pod.manufacturer or MANUFACTURER,
        model=pod.model,
        name=pod.nickname or pod.model or f"Pod {pod.id}",
        sw_version=pod.firmware_version,
        serial_number=pod.serial_number,
    )
    if connections:
        info["connections"] = connections
    if via_device_id is not None:
        info["via_device_id"] = via_device_id
    return info


def set_top_box_device_info(
    entry_id: str,
    stb: SetTopBox,
    *,
    via_device_id: str | None = None,
) -> DeviceInfo:
    connections = _mac_connections(stb.mac_address)
    info = DeviceInfo(
        identifiers={equipment_device_identifier(entry_id, stb.id)},
        manufacturer=stb.manufacturer or MANUFACTURER,
        model=stb.model,
        name=stb.nickname or stb.model or f"Set-Top Box {stb.id}",
        sw_version=stb.firmware_version,
        serial_number=stb.serial_number,
    )
    if connections:
        info["connections"] = connections
    if via_device_id is not None:
        info["via_device_id"] = via_device_id
    return info


def async_register_equipment_devices(
    hass: HomeAssistant,
    entry_id: str,
    equipment: EquipmentState,
) -> None:
    registry = dr.async_get(hass)
    account_device = registry.async_get_or_create(
        config_entry_id=entry_id,
        **account_device_info(entry_id),
    )

    router_device_ids: dict[str, str] = {}
    for router in equipment.routers:
        combo_modem = find_combo_modem(equipment, router)
        device = registry.async_get_or_create(
            config_entry_id=entry_id,
            **router_device_info(
                entry_id,
                router,
                combo_modem,
                via_device_id=account_device.id,
            ),
        )
        router_device_ids[router.id] = device.id

    for modem in equipment.modems:
        if is_merged_combo_modem(equipment, modem):
            continue
        registry.async_get_or_create(
            config_entry_id=entry_id,
            **modem_device_info(
                entry_id,
                modem,
                via_device_id=account_device.id,
            ),
        )

    for pod in equipment.pods:
        parent_router_id = find_pod_router_id(equipment, pod)
        parent_device_id = (
            router_device_ids.get(parent_router_id) if parent_router_id else None
        ) or account_device.id
        registry.async_get_or_create(
            config_entry_id=entry_id,
            **pod_device_info(
                entry_id,
                pod,
                parent_router_id,
                via_device_id=parent_device_id,
            ),
        )

    for stb in equipment.set_top_boxes:
        registry.async_get_or_create(
            config_entry_id=entry_id,
            **set_top_box_device_info(
                entry_id,
                stb,
                via_device_id=account_device.id,
            ),
        )
