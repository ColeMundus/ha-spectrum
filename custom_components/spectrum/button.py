from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiospectrum import (
    EquipmentState,
    Modem,
    Router,
    SpectrumAuthError,
    SpectrumError,
)
from homeassistant.components.button import ButtonEntity
from homeassistant.components.button.const import ButtonDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import (
    SpectrumConfigEntry,
    SpectrumEquipmentCoordinator,
    SpectrumRuntimeData,
)
from .entity import (
    SpectrumCoordinatorEntity,
    account_device_info,
    async_register_equipment_devices,
    find_combo_modem,
    is_merged_combo_modem,
    modem_device_info,
    router_device_info,
)

PARALLEL_UPDATES = 1


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


def _find_router(equipment: EquipmentState | None, router_id: str) -> Router | None:
    if equipment is None:
        return None
    for router in equipment.routers:
        if router.id == router_id:
            return router
    return None


def _find_modem(equipment: EquipmentState | None, modem_id: str) -> Modem | None:
    if equipment is None:
        return None
    for modem in equipment.modems:
        if modem.id == modem_id:
            return modem
    return None


class SpectrumAccountRefreshButton(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator],
    ButtonEntity,
):
    _attr_translation_key = "refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
    ) -> None:
        super().__init__(runtime.equipment)
        self._entry = entry
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}:account:refresh"
        self._attr_device_info = account_device_info(entry.entry_id)

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await _async_call_client(
            self.hass,
            self._entry,
            lambda: asyncio.gather(
                self._runtime.equipment.async_request_refresh(),
                self._runtime.network.async_request_refresh(),
                self._runtime.account.async_request_refresh(),
            ),
        )


class SpectrumRouterButton(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator],
    ButtonEntity,
):
    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
        router: Router,
        combo_modem: Modem | None,
        *,
        translation_key: str,
        unique_id: str,
        device_class: ButtonDeviceClass | None = None,
        entity_category: EntityCategory | None = None,
    ) -> None:
        super().__init__(runtime.equipment)
        self._entry = entry
        self._runtime = runtime
        self._router_id = router.id
        self._attr_translation_key = translation_key
        self._attr_unique_id = unique_id
        self._attr_device_info = router_device_info(entry.entry_id, router, combo_modem)
        if device_class is not None:
            self._attr_device_class = device_class
        if entity_category is not None:
            self._attr_entity_category = entity_category

    @property
    def available(self) -> bool:
        bundle = self.coordinator.data
        equipment = bundle.equipment if bundle is not None else None
        return (
            super().available and _find_router(equipment, self._router_id) is not None
        )


class SpectrumRouterRebootButton(SpectrumRouterButton):
    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
        router: Router,
        combo_modem: Modem | None,
    ) -> None:
        super().__init__(
            entry,
            runtime,
            router,
            combo_modem,
            translation_key="reboot",
            unique_id=f"{entry.entry_id}:reboot:{router.id}",
            device_class=ButtonDeviceClass.RESTART,
            entity_category=EntityCategory.CONFIG,
        )

    async def async_press(self) -> None:
        await _async_call_client(
            self.hass,
            self._entry,
            lambda: self._runtime.client.async_reboot_equipment(self._router_id),
        )
        await self.coordinator.async_request_refresh()


class SpectrumRouterSpeedTestButton(SpectrumRouterButton):
    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
        router: Router,
        combo_modem: Modem | None,
    ) -> None:
        super().__init__(
            entry,
            runtime,
            router,
            combo_modem,
            translation_key="run_speed_test",
            unique_id=f"{entry.entry_id}:run_speed_test:{router.id}",
        )

    async def async_press(self) -> None:
        await _async_call_client(
            self.hass,
            self._entry,
            lambda: self._runtime.client.async_initiate_speed_test(
                self._router_id,
                operation="ROUTER",
            ),
        )
        await self.coordinator.async_request_refresh()


class SpectrumRouterSmartSteerButton(SpectrumRouterButton):
    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
        router: Router,
        combo_modem: Modem | None,
    ) -> None:
        super().__init__(
            entry,
            runtime,
            router,
            combo_modem,
            translation_key="smart_steer",
            unique_id=f"{entry.entry_id}:smart_steer:{router.id}",
        )

    async def async_press(self) -> None:
        await _async_call_client(
            self.hass,
            self._entry,
            lambda: self._runtime.client.async_smart_steer(self._router_id),
        )
        await self.coordinator.async_request_refresh()


class SpectrumModemButton(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator],
    ButtonEntity,
):
    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
        modem: Modem,
        device_info: DeviceInfo,
        *,
        translation_key: str,
        unique_id: str,
        device_class: ButtonDeviceClass | None = None,
        entity_category: EntityCategory | None = None,
    ) -> None:
        super().__init__(runtime.equipment)
        self._entry = entry
        self._runtime = runtime
        self._modem_id = modem.id
        self._attr_translation_key = translation_key
        self._attr_unique_id = unique_id
        self._attr_device_info = device_info
        if device_class is not None:
            self._attr_device_class = device_class
        if entity_category is not None:
            self._attr_entity_category = entity_category

    @property
    def available(self) -> bool:
        bundle = self.coordinator.data
        equipment = bundle.equipment if bundle is not None else None
        return super().available and _find_modem(equipment, self._modem_id) is not None


class SpectrumModemRebootButton(SpectrumModemButton):
    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
        modem: Modem,
        device_info: DeviceInfo,
        *,
        merged_combo: bool,
    ) -> None:
        key = "reboot_modem" if merged_combo else "reboot"
        super().__init__(
            entry,
            runtime,
            modem,
            device_info,
            translation_key=key,
            unique_id=f"{entry.entry_id}:{key}:{modem.id}",
            device_class=ButtonDeviceClass.RESTART,
            entity_category=EntityCategory.CONFIG,
        )

    async def async_press(self) -> None:
        await _async_call_client(
            self.hass,
            self._entry,
            lambda: self._runtime.client.async_reboot_equipment(self._modem_id),
        )
        await self.coordinator.async_request_refresh()


class SpectrumModemSpeedTestButton(SpectrumModemButton):
    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
        modem: Modem,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(
            entry,
            runtime,
            modem,
            device_info,
            translation_key="run_modem_speed_test",
            unique_id=f"{entry.entry_id}:run_modem_speed_test:{modem.id}",
        )

    async def async_press(self) -> None:
        await _async_call_client(
            self.hass,
            self._entry,
            lambda: self._runtime.client.async_initiate_speed_test(
                self._modem_id,
                operation="MODEM_DOWNLOAD",
            ),
        )
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpectrumConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    added_uids: set[str] = set()

    async_add_entities([SpectrumAccountRefreshButton(entry, runtime)])

    @callback
    def _sync_equipment_buttons() -> None:
        bundle = runtime.equipment.data
        if bundle is None:
            return
        equipment = bundle.equipment
        new_entities: list[ButtonEntity] = []

        for router in equipment.routers:
            if not router.id:
                continue
            combo_modem = find_combo_modem(equipment, router)
            r_device_info = router_device_info(entry.entry_id, router, combo_modem)

            if router.supports("REBOOT"):
                uid = f"{entry.entry_id}:reboot:{router.id}"
                if uid not in added_uids:
                    added_uids.add(uid)
                    new_entities.append(
                        SpectrumRouterRebootButton(entry, runtime, router, combo_modem)
                    )

            if router.supports("SPEED_TEST"):
                uid = f"{entry.entry_id}:run_speed_test:{router.id}"
                if uid not in added_uids:
                    added_uids.add(uid)
                    new_entities.append(
                        SpectrumRouterSpeedTestButton(
                            entry, runtime, router, combo_modem
                        )
                    )

            if router.supports("SMART_STEERING"):
                uid = f"{entry.entry_id}:smart_steer:{router.id}"
                if uid not in added_uids:
                    added_uids.add(uid)
                    new_entities.append(
                        SpectrumRouterSmartSteerButton(
                            entry, runtime, router, combo_modem
                        )
                    )

            if combo_modem is not None:
                if combo_modem.supports("REBOOT"):
                    uid = f"{entry.entry_id}:reboot_modem:{combo_modem.id}"
                    if uid not in added_uids:
                        added_uids.add(uid)
                        new_entities.append(
                            SpectrumModemRebootButton(
                                entry,
                                runtime,
                                combo_modem,
                                r_device_info,
                                merged_combo=True,
                            )
                        )

                if combo_modem.supports("SPEED_TEST"):
                    uid = f"{entry.entry_id}:run_modem_speed_test:{combo_modem.id}"
                    if uid not in added_uids:
                        added_uids.add(uid)
                        new_entities.append(
                            SpectrumModemSpeedTestButton(
                                entry, runtime, combo_modem, r_device_info
                            )
                        )

        for modem in equipment.modems:
            if not modem.id or is_merged_combo_modem(equipment, modem):
                continue
            m_device_info = modem_device_info(entry.entry_id, modem)

            if modem.supports("REBOOT"):
                uid = f"{entry.entry_id}:reboot:{modem.id}"
                if uid not in added_uids:
                    added_uids.add(uid)
                    new_entities.append(
                        SpectrumModemRebootButton(
                            entry,
                            runtime,
                            modem,
                            m_device_info,
                            merged_combo=False,
                        )
                    )

            if modem.supports("SPEED_TEST"):
                uid = f"{entry.entry_id}:run_modem_speed_test:{modem.id}"
                if uid not in added_uids:
                    added_uids.add(uid)
                    new_entities.append(
                        SpectrumModemSpeedTestButton(
                            entry, runtime, modem, m_device_info
                        )
                    )

        if new_entities:
            async_register_equipment_devices(hass, entry.entry_id, equipment)
            async_add_entities(new_entities)

    _sync_equipment_buttons()
    entry.async_on_unload(runtime.equipment.async_add_listener(_sync_equipment_buttons))
