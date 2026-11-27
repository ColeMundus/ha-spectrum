from __future__ import annotations

import io
from collections.abc import Callable

import segno
from aiospectrum import EquipmentState, Modem, Router
from homeassistant.components.image import ImageEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import SpectrumConfigEntry, SpectrumEquipmentCoordinator
from .entity import (
    SpectrumCoordinatorEntity,
    async_register_equipment_devices,
    find_combo_modem,
    router_device_info,
)

PARALLEL_UPDATES = 0

type WifiCredentials = tuple[str, str, str]


def _escape_wifi_qr_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for char in (";", ",", ":", '"'):
        escaped = escaped.replace(char, f"\\{char}")
    return escaped


def _normalize_security_type(password: str, security_mode: str | None) -> str:
    if not password:
        return "nopass"
    if security_mode and "WEP" in security_mode.upper():
        return "WEP"
    return "WPA"


def build_wifi_qr_payload(
    ssid: str,
    password: str,
    security_mode: str | None = None,
) -> str:
    auth_type = _normalize_security_type(password, security_mode)
    escaped_ssid = _escape_wifi_qr_value(ssid)
    if auth_type == "nopass":
        return f"WIFI:T:nopass;S:{escaped_ssid};;"
    escaped_password = _escape_wifi_qr_value(password)
    return f"WIFI:T:{auth_type};S:{escaped_ssid};P:{escaped_password};;"


def _render_qr_png(payload: str) -> bytes:
    qr = segno.make_qr(payload)
    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=8, border=4)
    return buffer.getvalue()


def _find_router(equipment: EquipmentState, router_id: str) -> Router | None:
    for router in equipment.routers:
        if router.id == router_id:
            return router
    return None


def _primary_wifi_credentials(router: Router) -> WifiCredentials | None:
    selected = None
    for net in router.wifi_networks:
        if net.is_primary and net.name:
            selected = net
            break
    if selected is None:
        for net in router.wifi_networks:
            if net.name:
                selected = net
                break
    if selected is None or not selected.name:
        return None
    password = selected.password or ""
    auth_type = _normalize_security_type(password, selected.security_mode)
    return (selected.name, password, auth_type)


def _additional_network_credentials(router: Router) -> WifiCredentials | None:
    for net in router.additional_networks:
        if net.name and net.password:
            auth_type = _normalize_security_type(net.password, net.security_mode)
            return (net.name, net.password, auth_type)
    return None


class SpectrumWifiQrImage(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator], ImageEntity
):
    _attr_content_type = "image/png"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SpectrumEquipmentCoordinator,
        entry_id: str,
        router: Router,
        modem: Modem | None,
        *,
        key: str,
        credentials_fn: Callable[[Router], WifiCredentials | None],
    ) -> None:
        SpectrumCoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._router_id = router.id
        self._credentials_fn = credentials_fn
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}:router:{router.id}:{key}"
        self._attr_device_info = router_device_info(entry_id, router, modem)
        self._last_credentials: WifiCredentials | None = credentials_fn(router)
        self._cached_credentials: WifiCredentials | None = None
        self._cached_png: bytes | None = None
        if self._last_credentials is not None:
            self._attr_image_last_updated = dt_util.utcnow()

    def _current_router(self) -> Router | None:
        if self.coordinator.data is None:
            return None
        return _find_router(self.coordinator.data.equipment, self._router_id)

    def _current_credentials(self) -> WifiCredentials | None:
        router = self._current_router()
        if router is None:
            return None
        return self._credentials_fn(router)

    @property
    def available(self) -> bool:
        return super().available and self._current_credentials() is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        credentials = self._current_credentials()
        if credentials != self._last_credentials:
            self._last_credentials = credentials
            self._cached_credentials = None
            self._cached_png = None
            if credentials is not None:
                self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        credentials = self._current_credentials()
        if credentials is None:
            return None
        if self._cached_png is not None and self._cached_credentials == credentials:
            return self._cached_png
        ssid, password, security_mode = credentials
        payload = build_wifi_qr_payload(ssid, password, security_mode)
        png_bytes = await self.hass.async_add_executor_job(_render_qr_png, payload)
        self._cached_credentials = credentials
        self._cached_png = png_bytes
        return png_bytes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpectrumConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    equipment_coordinator = entry.runtime_data.equipment
    entry_id = entry.entry_id

    added_keys: set[str] = set()
    known_equipment_ids: set[str] = set()

    @callback
    def _async_sync_entities() -> None:
        if equipment_coordinator.data is None:
            return
        equipment = equipment_coordinator.data.equipment

        current_eq_ids = (
            {item.id for item in equipment.routers if item.id}
            | {item.id for item in equipment.modems if item.id}
            | {item.id for item in equipment.pods if item.id}
            | {item.id for item in equipment.set_top_boxes if item.id}
        )
        if current_eq_ids - known_equipment_ids:
            async_register_equipment_devices(hass, entry_id, equipment)
            known_equipment_ids.update(current_eq_ids)

        new_entities: list[ImageEntity] = []

        def _append(entity: ImageEntity) -> None:
            uid = entity.unique_id
            if uid and uid not in added_keys:
                added_keys.add(uid)
                new_entities.append(entity)

        for router in equipment.routers:
            if not router.id:
                continue
            combo_modem = find_combo_modem(equipment, router)
            if _primary_wifi_credentials(router) is not None:
                _append(
                    SpectrumWifiQrImage(
                        hass,
                        equipment_coordinator,
                        entry_id,
                        router,
                        combo_modem,
                        key="wifi_qr",
                        credentials_fn=_primary_wifi_credentials,
                    )
                )
            if _additional_network_credentials(router) is not None:
                _append(
                    SpectrumWifiQrImage(
                        hass,
                        equipment_coordinator,
                        entry_id,
                        router,
                        combo_modem,
                        key="additional_network_qr",
                        credentials_fn=_additional_network_credentials,
                    )
                )

        if new_entities:
            async_add_entities(new_entities)

    _async_sync_entities()
    entry.async_on_unload(
        equipment_coordinator.async_add_listener(_async_sync_entities)
    )
