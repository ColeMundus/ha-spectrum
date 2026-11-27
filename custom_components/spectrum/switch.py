from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiospectrum import (
    Client,
    ClientGroup,
    EquipmentState,
    NetworkState,
    Router,
    SecurityShield,
    SpectrumAuthError,
    SpectrumError,
    normalize_mac,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import (
    SpectrumConfigEntry,
    SpectrumEquipmentCoordinator,
    SpectrumNetworkCoordinator,
    SpectrumRuntimeData,
)
from .entity import (
    SpectrumCoordinatorEntity,
    async_register_equipment_devices,
    equipment_device_identifier,
    find_combo_modem,
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


def _router_device_info_for_id(
    entry_id: str,
    equipment: EquipmentState | None,
    router_id: str,
) -> DeviceInfo:
    router = _find_router(equipment, router_id)
    if router is not None:
        combo_modem = (
            find_combo_modem(equipment, router) if equipment is not None else None
        )
        return router_device_info(entry_id, router, combo_modem)
    return DeviceInfo(identifiers={equipment_device_identifier(entry_id, router_id)})


def _resolve_router_network_id(
    router: Router,
    equipment: EquipmentState | None,
    network: NetworkState | None,
) -> str | None:
    if router.network_id:
        return router.network_id
    if router.security_shield is not None and router.security_shield.network_id:
        return router.security_shield.network_id
    if network is not None and (equipment is None or len(equipment.routers) == 1):
        if network.network_ids:
            return network.network_ids[0]
        if network.security_shields:
            return next(iter(network.security_shields))
    return None


def _supports_security_shield(
    router: Router,
    equipment: EquipmentState | None,
    network: NetworkState | None,
) -> bool:
    network_id = _resolve_router_network_id(router, equipment, network)
    if not network_id:
        return False
    if router.security_shield is not None:
        return True
    return network is not None and network_id in network.security_shields


def _supports_upnp(router: Router) -> bool:
    return (
        router.supports("UPDATE_UPNP")
        or router.supports("GET_UPNP")
        or router.upnp is not None
    )


def _supports_hotspot(
    router: Router,
    equipment: EquipmentState | None,
    network: NetworkState | None,
) -> bool:
    if _resolve_router_network_id(router, equipment, network) is None:
        return False
    if router.hotspot_status is not None:
        return router.hotspot_status.strip().upper() != "NOT_SUPPORTED"
    return router.hotspot_enabled is not None


def _supports_thread_radio(router: Router) -> bool:
    return router.supports("THREAD_NETWORK") or router.thread_network is not None


def _resolve_group_router_id(
    group: ClientGroup,
    equipment: EquipmentState | None,
) -> str | None:
    if group.router_id:
        return group.router_id
    if (
        equipment is not None
        and len(equipment.routers) == 1
        and equipment.routers[0].id
    ):
        return equipment.routers[0].id
    return None


def _resolve_client_router_id(
    client: Client,
    equipment: EquipmentState | None,
) -> str | None:
    if client.router_id:
        return client.router_id
    if equipment is not None:
        if client.network_id:
            for router in equipment.routers:
                if router.network_id == client.network_id and router.id:
                    return router.id
        if len(equipment.routers) == 1 and equipment.routers[0].id:
            return equipment.routers[0].id
    return None


class SpectrumSecurityShieldSwitch(
    SpectrumCoordinatorEntity[SpectrumNetworkCoordinator],
    SwitchEntity,
):
    _attr_translation_key = "security_shield"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
        router: Router,
    ) -> None:
        super().__init__(runtime.network)
        self._entry = entry
        self._runtime = runtime
        self._router_id = router.id
        equipment = (
            runtime.equipment.data.equipment
            if runtime.equipment.data is not None
            else None
        )
        self._attr_unique_id = f"{entry.entry_id}:security_shield:{router.id}"
        self._attr_device_info = _router_device_info_for_id(
            entry.entry_id, equipment, router.id
        )

    def _router(self) -> Router | None:
        bundle = self._runtime.equipment.data
        if bundle is None:
            return None
        return _find_router(bundle.equipment, self._router_id)

    def _network_id(self) -> str | None:
        bundle = self._runtime.equipment.data
        equipment = bundle.equipment if bundle is not None else None
        router = _find_router(equipment, self._router_id)
        if router is None:
            return None
        return _resolve_router_network_id(router, equipment, self.coordinator.data)

    def _shield(self) -> SecurityShield | None:
        network_id = self._network_id()
        if network_id and self.coordinator.data is not None:
            shield = self.coordinator.data.security_shields.get(network_id)
            if shield is not None:
                return shield
        router = self._router()
        return router.security_shield if router is not None else None

    @property
    def available(self) -> bool:
        return (
            super().available
            and self._network_id() is not None
            and self._shield() is not None
        )

    @property
    def is_on(self) -> bool | None:
        shield = self._shield()
        return shield.enabled if shield is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        _ = kwargs
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        _ = kwargs
        await self._async_set_enabled(False)

    async def _async_set_enabled(self, enabled: bool) -> None:
        network_id = self._network_id()
        if not network_id:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={
                    "error": "Network identifier unavailable for Security Shield."
                },
            )
        await _async_call_client(
            self.hass,
            self._entry,
            lambda: self._runtime.client.async_set_security_shield(network_id, enabled),
        )
        await self.coordinator.async_request_refresh()


class SpectrumRouterEquipmentSwitch(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator],
    SwitchEntity,
):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
        router: Router,
        key: str,
    ) -> None:
        super().__init__(runtime.equipment)
        self._entry = entry
        self._runtime = runtime
        self._router_id = router.id
        equipment = (
            runtime.equipment.data.equipment
            if runtime.equipment.data is not None
            else None
        )
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}:{key}:{router.id}"
        self._attr_device_info = _router_device_info_for_id(
            entry.entry_id, equipment, router.id
        )

    def _router(self) -> Router | None:
        bundle = self.coordinator.data
        if bundle is None:
            return None
        return _find_router(bundle.equipment, self._router_id)

    @property
    def available(self) -> bool:
        return super().available and self._router() is not None


class SpectrumUpnpSwitch(SpectrumRouterEquipmentSwitch):
    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
        router: Router,
    ) -> None:
        super().__init__(entry, runtime, router, "upnp")

    @property
    def is_on(self) -> bool | None:
        router = self._router()
        if router is None or router.upnp is None:
            return None
        return router.upnp.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        _ = kwargs
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        _ = kwargs
        await self._async_set_enabled(False)

    async def _async_set_enabled(self, enabled: bool) -> None:
        await _async_call_client(
            self.hass,
            self._entry,
            lambda: self._runtime.client.async_set_upnp(self._router_id, enabled),
        )
        await self.coordinator.async_request_refresh()


class SpectrumHotspotSwitch(SpectrumRouterEquipmentSwitch):
    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
        router: Router,
    ) -> None:
        super().__init__(entry, runtime, router, "hotspot")

    def _network_id(self) -> str | None:
        bundle = self.coordinator.data
        equipment = bundle.equipment if bundle is not None else None
        router = _find_router(equipment, self._router_id)
        if router is None:
            return None
        return _resolve_router_network_id(router, equipment, self._runtime.network.data)

    @property
    def available(self) -> bool:
        return super().available and self._network_id() is not None

    @property
    def is_on(self) -> bool | None:
        router = self._router()
        return router.hotspot_enabled if router is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        _ = kwargs
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        _ = kwargs
        await self._async_set_enabled(False)

    async def _async_set_enabled(self, enabled: bool) -> None:
        network_id = self._network_id()
        if not network_id:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={
                    "error": (
                        "Network identifier unavailable for Spectrum Wi-Fi hotspot."
                    )
                },
            )
        await _async_call_client(
            self.hass,
            self._entry,
            lambda: self._runtime.client.async_set_hotspot(network_id, enabled),
        )
        await self.coordinator.async_request_refresh()


class SpectrumThreadRadioSwitch(SpectrumRouterEquipmentSwitch):
    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
        router: Router,
    ) -> None:
        super().__init__(entry, runtime, router, "thread_radio")

    @property
    def is_on(self) -> bool | None:
        router = self._router()
        if router is None:
            return None
        if router.thread_enabled is not None:
            return router.thread_enabled
        if router.thread_network is not None:
            return router.thread_network.enabled
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        _ = kwargs
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        _ = kwargs
        await self._async_set_enabled(False)

    async def _async_set_enabled(self, enabled: bool) -> None:
        await _async_call_client(
            self.hass,
            self._entry,
            lambda: self._runtime.client.async_set_thread_radio(
                self._router_id, enabled
            ),
        )
        await self.coordinator.async_request_refresh()


class SpectrumClientGroupPauseSwitch(
    SpectrumCoordinatorEntity[SpectrumNetworkCoordinator],
    SwitchEntity,
):
    _attr_translation_key = "client_group_pause"

    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
        router_id: str,
        group: ClientGroup,
    ) -> None:
        super().__init__(runtime.network)
        self._entry = entry
        self._runtime = runtime
        self._router_id = router_id
        self._group_id = group.id
        equipment = (
            runtime.equipment.data.equipment
            if runtime.equipment.data is not None
            else None
        )
        self._attr_unique_id = (
            f"{entry.entry_id}:client_group_pause:{router_id}:{group.id}"
        )
        self._attr_translation_placeholders = {"group_name": group.name or group.id}
        self._attr_device_info = _router_device_info_for_id(
            entry.entry_id, equipment, router_id
        )

    def _group(self) -> ClientGroup | None:
        network = self.coordinator.data
        if network is None:
            return None
        equipment = (
            self._runtime.equipment.data.equipment
            if self._runtime.equipment.data is not None
            else None
        )
        for group in network.client_groups:
            if group.id != self._group_id:
                continue
            resolved_router = _resolve_group_router_id(group, equipment)
            if resolved_router is None or resolved_router == self._router_id:
                return group
        return None

    @callback
    def _handle_coordinator_update(self) -> None:
        group = self._group()
        if group is not None:
            self._attr_translation_placeholders = {"group_name": group.name or group.id}
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        return super().available and self._group() is not None

    @property
    def is_on(self) -> bool | None:
        group = self._group()
        if group is None:
            return None
        return group.paused is True

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        group = self._group()
        if group is None:
            return None
        attrs: dict[str, Any] = {
            "group_id": group.id,
            "client_count": group.client_count,
        }
        if group.pause_reason is not None:
            attrs["pause_reason"] = group.pause_reason
        if group.pause_until is not None:
            attrs["pause_until"] = group.pause_until
        return attrs

    async def async_turn_on(self, **kwargs: Any) -> None:
        _ = kwargs
        await self._async_set_paused(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        _ = kwargs
        await self._async_set_paused(False)

    async def _async_set_paused(self, paused: bool) -> None:
        await _async_call_client(
            self.hass,
            self._entry,
            lambda: self._runtime.client.async_set_group_pause(
                self._router_id,
                self._group_id,
                paused=paused,
            ),
        )
        await self.coordinator.async_request_refresh()


class SpectrumClientPauseSwitch(
    SpectrumCoordinatorEntity[SpectrumNetworkCoordinator],
    SwitchEntity,
):
    _attr_translation_key = "client_pause"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        entry: SpectrumConfigEntry,
        runtime: SpectrumRuntimeData,
        router_id: str,
        normalized_mac: str,
        client: Client,
    ) -> None:
        super().__init__(runtime.network)
        self._entry = entry
        self._runtime = runtime
        self._router_id = router_id
        self._normalized_mac = normalized_mac
        self._client_id = client.id or ""
        equipment = (
            runtime.equipment.data.equipment
            if runtime.equipment.data is not None
            else None
        )
        self._attr_unique_id = (
            f"{entry.entry_id}:client_pause:{router_id}:{normalized_mac}"
        )
        self._attr_translation_placeholders = {
            "client_name": client.label or normalized_mac
        }
        self._attr_device_info = _router_device_info_for_id(
            entry.entry_id, equipment, router_id
        )

    def _client(self) -> Client | None:
        network = self.coordinator.data
        if network is None:
            return None
        equipment = (
            self._runtime.equipment.data.equipment
            if self._runtime.equipment.data is not None
            else None
        )
        for client in network.clients:
            if not client.mac_address:
                continue
            if normalize_mac(client.mac_address) != self._normalized_mac:
                continue
            resolved_router = _resolve_client_router_id(client, equipment)
            if resolved_router is None or resolved_router == self._router_id:
                return client
        return None

    @callback
    def _handle_coordinator_update(self) -> None:
        client = self._client()
        if client is not None:
            if client.id:
                self._client_id = client.id
            self._attr_translation_placeholders = {
                "client_name": client.label or self._normalized_mac
            }
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        client = self._client()
        return (
            super().available
            and client is not None
            and bool(client.id or self._client_id)
        )

    @property
    def is_on(self) -> bool | None:
        client = self._client()
        if client is None:
            return None
        return client.paused is True

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        client = self._client()
        if client is None:
            return None
        attrs: dict[str, Any] = {
            "mac_address": self._normalized_mac,
        }
        if client.id:
            attrs["client_id"] = client.id
        if client.pause_until is not None:
            attrs["pause_until"] = client.pause_until
        return attrs

    async def async_turn_on(self, **kwargs: Any) -> None:
        _ = kwargs
        await self._async_set_paused(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        _ = kwargs
        await self._async_set_paused(False)

    async def _async_set_paused(self, paused: bool) -> None:
        client = self._client()
        client_id = (client.id if client is not None else None) or self._client_id
        if not client_id:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={
                    "error": "Spectrum Client ID is unavailable for pause control."
                },
            )
        await _async_call_client(
            self.hass,
            self._entry,
            lambda: self._runtime.client.async_set_client_pause(
                self._router_id,
                client_id,
                paused=paused,
            ),
        )
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpectrumConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    added_router_uids: set[str] = set()
    added_group_uids: set[str] = set()
    added_client_uids: set[str] = set()
    group_prefix = f"{entry.entry_id}:client_group_pause:"
    client_prefix = f"{entry.entry_id}:client_pause:"

    @callback
    def _sync_router_switches() -> None:
        bundle = runtime.equipment.data
        if bundle is None:
            return
        equipment = bundle.equipment
        network = runtime.network.data
        new_entities: list[SwitchEntity] = []

        for router in equipment.routers:
            if not router.id:
                continue

            shield_uid = f"{entry.entry_id}:security_shield:{router.id}"
            if shield_uid not in added_router_uids and _supports_security_shield(
                router, equipment, network
            ):
                added_router_uids.add(shield_uid)
                new_entities.append(
                    SpectrumSecurityShieldSwitch(entry, runtime, router)
                )

            upnp_uid = f"{entry.entry_id}:upnp:{router.id}"
            if upnp_uid not in added_router_uids and _supports_upnp(router):
                added_router_uids.add(upnp_uid)
                new_entities.append(SpectrumUpnpSwitch(entry, runtime, router))

            hotspot_uid = f"{entry.entry_id}:hotspot:{router.id}"
            if hotspot_uid not in added_router_uids and _supports_hotspot(
                router, equipment, network
            ):
                added_router_uids.add(hotspot_uid)
                new_entities.append(SpectrumHotspotSwitch(entry, runtime, router))

            thread_uid = f"{entry.entry_id}:thread_radio:{router.id}"
            if thread_uid not in added_router_uids and _supports_thread_radio(router):
                added_router_uids.add(thread_uid)
                new_entities.append(SpectrumThreadRadioSwitch(entry, runtime, router))

        if new_entities:
            async_register_equipment_devices(hass, entry.entry_id, equipment)
            async_add_entities(new_entities)

    @callback
    def _sync_network_switches() -> None:
        _sync_router_switches()
        network = runtime.network.data
        if network is None:
            return
        equipment = (
            runtime.equipment.data.equipment
            if runtime.equipment.data is not None
            else None
        )
        new_entities: list[SwitchEntity] = []
        active_group_uids: set[str] = set()
        active_client_uids: set[str] = set()

        for group in network.client_groups:
            if not group.id:
                continue
            router_id = _resolve_group_router_id(group, equipment)
            if not router_id:
                continue
            uid = f"{group_prefix}{router_id}:{group.id}"
            active_group_uids.add(uid)
            if uid not in added_group_uids:
                added_group_uids.add(uid)
                new_entities.append(
                    SpectrumClientGroupPauseSwitch(entry, runtime, router_id, group)
                )

        for client in network.clients:
            if not client.mac_address or not client.id:
                continue
            normalized_mac = normalize_mac(client.mac_address)
            if not normalized_mac:
                continue
            router_id = _resolve_client_router_id(client, equipment)
            if not router_id:
                continue
            uid = f"{client_prefix}{router_id}:{normalized_mac}"
            active_client_uids.add(uid)
            if uid not in added_client_uids:
                added_client_uids.add(uid)
                new_entities.append(
                    SpectrumClientPauseSwitch(
                        entry, runtime, router_id, normalized_mac, client
                    )
                )

        if new_entities:
            async_add_entities(new_entities)

        if runtime.network.last_update_success:
            ent_reg = er.async_get(hass)
            for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
                if reg_entry.domain != Platform.SWITCH:
                    continue
                if (
                    reg_entry.unique_id.startswith(group_prefix)
                    and reg_entry.unique_id not in active_group_uids
                ):
                    ent_reg.async_remove(reg_entry.entity_id)
                    added_group_uids.discard(reg_entry.unique_id)
                elif (
                    reg_entry.unique_id.startswith(client_prefix)
                    and reg_entry.unique_id not in active_client_uids
                ):
                    ent_reg.async_remove(reg_entry.entity_id)
                    added_client_uids.discard(reg_entry.unique_id)

    _sync_router_switches()
    _sync_network_switches()
    entry.async_on_unload(runtime.equipment.async_add_listener(_sync_router_switches))
    entry.async_on_unload(runtime.network.async_add_listener(_sync_network_switches))
