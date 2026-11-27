from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiospectrum import EquipmentState, Pod, SpectrumAuthError, SpectrumError
from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import (
    SpectrumConfigEntry,
    SpectrumEquipmentCoordinator,
    SpectrumRuntimeData,
)
from .entity import (
    SpectrumCoordinatorEntity,
    async_register_equipment_devices,
    find_pod_router_id,
    pod_device_info,
)

PARALLEL_UPDATES = 1

POD_LED_OPTIONS: tuple[str, ...] = ("normal", "locate")


async def _async_call_client(
    hass: HomeAssistant,
    entry: SpectrumConfigEntry,
    action: Callable[[], Awaitable[Any]],
) -> Any:
    try:
        return await action()
    except SpectrumAuthError as err:
        entry.async_start_reauth(hass)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="auth_failed",
        ) from err
    except SpectrumError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="api_error",
            translation_placeholders={"error": str(err)},
        ) from err


def _find_pod(equipment: EquipmentState | None, pod_id: str) -> Pod | None:
    if equipment is None:
        return None
    for pod in equipment.pods:
        if pod.id == pod_id:
            return pod
    return None


class SpectrumPodLedModeSelect(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator],
    SelectEntity,
):
    _attr_translation_key = "pod_led_mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(POD_LED_OPTIONS)

    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
        pod: Pod,
        parent_router_id: str | None,
    ) -> None:
        super().__init__(runtime.equipment)
        self._entry = entry
        self._runtime = runtime
        self._pod_id = pod.id
        self._selected_mode: str = "normal"
        self._attr_unique_id = f"{entry.entry_id}:pod_led_mode:{pod.id}"
        self._attr_device_info = pod_device_info(entry.entry_id, pod, parent_router_id)

    def _pod(self) -> Pod | None:
        bundle = self.coordinator.data
        if bundle is None:
            return None
        return _find_pod(bundle.equipment, self._pod_id)

    @property
    def available(self) -> bool:
        return super().available and self._pod() is not None

    @property
    def current_option(self) -> str | None:
        pod = self._pod()
        if pod is None:
            return None
        raw_mode: object = getattr(pod, "led_mode", None)
        if isinstance(raw_mode, str) and raw_mode.strip():
            normalized = raw_mode.strip().lower()
            return normalized if normalized in POD_LED_OPTIONS else None
        return self._selected_mode

    async def async_select_option(self, option: str) -> None:
        result = await _async_call_client(
            self.hass,
            self._entry,
            lambda: self._runtime.client.async_update_pod_led(
                self._pod_id, option.upper()
            ),
        )
        returned_mode = result.get("mode") if isinstance(result, dict) else None
        if (
            isinstance(returned_mode, str)
            and returned_mode.strip().lower() in POD_LED_OPTIONS
        ):
            self._selected_mode = returned_mode.strip().lower()
        elif option.strip().lower() in POD_LED_OPTIONS:
            self._selected_mode = option.strip().lower()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpectrumConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    added_uids: set[str] = set()

    @callback
    def _sync_pod_selects() -> None:
        bundle = runtime.equipment.data
        if bundle is None:
            return
        equipment = bundle.equipment
        new_entities: list[SelectEntity] = []

        for pod in equipment.pods:
            if not pod.id:
                continue
            uid = f"{entry.entry_id}:pod_led_mode:{pod.id}"
            if uid not in added_uids:
                added_uids.add(uid)
                parent_router_id = find_pod_router_id(equipment, pod)
                new_entities.append(
                    SpectrumPodLedModeSelect(entry, runtime, pod, parent_router_id)
                )

        if new_entities:
            async_register_equipment_devices(hass, entry.entry_id, equipment)
            async_add_entities(new_entities)

    _sync_pod_selects()
    entry.async_on_unload(runtime.equipment.async_add_listener(_sync_pod_selects))
