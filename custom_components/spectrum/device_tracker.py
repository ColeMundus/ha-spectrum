from __future__ import annotations

from typing import Any

from aiospectrum import (
    Client,
    EquipmentState,
    NetworkState,
    Router,
    format_mac,
    is_mac_like,
    normalize_mac,
)
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.components.device_tracker.entity import (
    BaseScannerEntity,
    ScannerEntity,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CLIENT_TRACKER_MODE_ALL,
    CLIENT_TRACKER_MODE_CONNECTED_OR_RESERVED,
    CLIENT_TRACKER_MODE_DISABLED,
    DOMAIN,
    OPT_CLIENT_TRACKER_MODE,
)
from .coordinator import (
    SpectrumConfigEntry,
    SpectrumEquipmentCoordinator,
    SpectrumNetworkCoordinator,
)
from .entity import (
    SpectrumCoordinatorEntity,
    account_device_identifier,
    async_register_equipment_devices,
    equipment_device_identifier,
)


def _client_key(client: Client) -> str | None:
    if client.mac_address:
        return normalize_mac(client.mac_address)
    if client.id:
        return client.id
    return None


def _resolve_client_router(
    client: Client,
    equipment: EquipmentState | None,
) -> Router | None:
    if equipment is None or not equipment.routers:
        return None
    if client.router_id:
        for router in equipment.routers:
            if router.id == client.router_id:
                return router
    if client.network_id:
        for router in equipment.routers:
            if router.network_id == client.network_id:
                return router
    if len(equipment.routers) == 1:
        return equipment.routers[0]
    return None


def _client_router_id(
    client: Client,
    equipment: EquipmentState | None,
) -> str:
    router = _resolve_client_router(client, equipment)
    if router is not None and router.id:
        return router.id
    if client.router_id:
        return client.router_id
    return "account"


def _tracker_unique_id(entry_id: str, router_id: str, client_mac_key: str) -> str:
    return f"{entry_id}:client_tracker:{router_id}:{client_mac_key}"


def _tracker_enabled_default(mode: str, client: Client) -> bool:
    if mode == CLIENT_TRACKER_MODE_ALL:
        return True
    if mode == CLIENT_TRACKER_MODE_DISABLED:
        return False
    return client.connected is True or client.ip_reservation is not None


def _client_group_names(client: Client, network: NetworkState | None) -> list[str]:
    names: list[str] = []
    if client.group_name:
        names.append(client.group_name)
    if network is None:
        return names
    client_mac_norm = normalize_mac(client.mac_address) if client.mac_address else ""
    group_id_set = set(client.group_ids)
    if client.group_id:
        group_id_set.add(client.group_id)
    for group in network.client_groups:
        matched = (
            (group.id in group_id_set)
            or (client.id is not None and client.id in group.client_ids)
            or (
                bool(client_mac_norm)
                and any(
                    normalize_mac(mac) == client_mac_norm for mac in group.client_macs
                )
            )
        )
        if matched and group.name and group.name not in names:
            names.append(group.name)
    return names


class SpectrumClientTracker(
    SpectrumCoordinatorEntity[SpectrumNetworkCoordinator], ScannerEntity
):
    _attr_translation_key = "client"
    _attr_source_type = SourceType.ROUTER

    def __init__(
        self,
        network_coordinator: SpectrumNetworkCoordinator,
        equipment_coordinator: SpectrumEquipmentCoordinator,
        entry_id: str,
        router_id: str,
        client_mac_key: str,
        client: Client,
        enabled_default: bool,
    ) -> None:
        super().__init__(network_coordinator)
        self._equipment_coordinator = equipment_coordinator
        self._entry_id = entry_id
        self._router_id = router_id
        self._client_mac_key = client_mac_key
        self._unique_id = _tracker_unique_id(entry_id, router_id, client_mac_key)
        self._enabled_default = enabled_default
        self._attr_translation_placeholders = {"client_name": client.label}
        self._sync_cached_attrs(client)

    def _current_client(self) -> Client | None:
        if self.coordinator.data is None:
            return None
        equipment = (
            self._equipment_coordinator.data.equipment
            if self._equipment_coordinator.data is not None
            else None
        )
        for client in self.coordinator.data.clients:
            if _client_key(client) != self._client_mac_key:
                continue
            if _client_router_id(client, equipment) == self._router_id:
                return client
        return None

    def _sync_cached_attrs(self, client: Client | None) -> None:
        if client is None:
            return
        self._attr_mac_address = (
            format_mac(client.mac_address)
            if client.mac_address and is_mac_like(client.mac_address)
            else (client.mac_address or None)
        )
        self._attr_ip_address = client.primary_ipv4
        self._attr_hostname = client.host_name
        if client.label:
            self._attr_translation_placeholders = {"client_name": client.label}

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def entity_registry_enabled_default(self) -> bool:
        return self._enabled_default

    @property
    def available(self) -> bool:
        return super().available and self._current_client() is not None

    @property
    def is_connected(self) -> bool:
        client = self._current_client()
        return bool(client is not None and client.connected is True)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        client = self._current_client()
        if client is None:
            return {}
        return {
            "connection_type": client.connection_type,
            "ssid": client.ssid,
            "wifi_radio_frequency": client.wifi_radio_frequency,
            "signal_strength": client.signal_strength,
            "paused": client.paused,
            "group_names": _client_group_names(client, self.coordinator.data),
            "reserved_ip": (
                client.ip_reservation.ip_address
                if client.ip_reservation is not None
                else None
            ),
        }

    def _parent_device_entry(self) -> dr.DeviceEntry | None:
        if self.hass is None:
            return None
        dev_reg = dr.async_get(self.hass)
        if self._router_id != "account":
            device = dev_reg.async_get_device_by_identifier(
                equipment_device_identifier(self._entry_id, self._router_id),
                self._entry_id,
            )
            if device is not None:
                return device
        return dev_reg.async_get_device_by_identifier(
            account_device_identifier(self._entry_id),
            self._entry_id,
        )

    @callback
    def _async_link_parent_device(self) -> None:
        if self.hass is None:
            return
        parent_device = self._parent_device_entry()
        if parent_device is None:
            return
        ent_reg = er.async_get(self.hass)
        entity_id = (
            self.registry_entry.entity_id
            if self.registry_entry is not None
            else ent_reg.async_get_entity_id(
                Platform.DEVICE_TRACKER, DOMAIN, self._unique_id
            )
        )
        if entity_id is None:
            return
        reg_entry = ent_reg.async_get(entity_id)
        if reg_entry is None:
            return
        if reg_entry.device_id != parent_device.id:
            updated = ent_reg.async_update_entity(
                entity_id,
                device_id=parent_device.id,
            )
            if self.registry_entry is not None:
                self.registry_entry = updated
        self.device_entry = parent_device

    async def async_internal_added_to_hass(self) -> None:
        self._async_link_parent_device()
        await BaseScannerEntity.async_internal_added_to_hass(self)

    @callback
    def add_to_platform_abort(self) -> None:
        self._async_link_parent_device()
        super().add_to_platform_abort()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sync_cached_attrs(self._current_client())
        super()._handle_coordinator_update()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpectrumConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    network_coordinator = runtime.network
    equipment_coordinator = runtime.equipment
    entry_id = entry.entry_id
    tracker_mode = str(
        entry.options.get(
            OPT_CLIENT_TRACKER_MODE, CLIENT_TRACKER_MODE_CONNECTED_OR_RESERVED
        )
    )
    prefix = f"{entry_id}:client_tracker:"

    added_unique_ids: set[str] = set()
    known_equipment_ids: set[str] = set()

    @callback
    def _async_sync_clients() -> None:
        equipment = (
            equipment_coordinator.data.equipment
            if equipment_coordinator.data is not None
            else None
        )
        if equipment is not None:
            current_eq_ids = (
                {item.id for item in equipment.routers if item.id}
                | {item.id for item in equipment.modems if item.id}
                | {item.id for item in equipment.pods if item.id}
                | {item.id for item in equipment.set_top_boxes if item.id}
            )
            if current_eq_ids - known_equipment_ids:
                async_register_equipment_devices(hass, entry_id, equipment)
                known_equipment_ids.update(current_eq_ids)

        if (
            not network_coordinator.last_update_success
            or network_coordinator.data is None
        ):
            return

        active_clients: dict[str, tuple[str, str, Client]] = {}
        for client in network_coordinator.data.clients:
            mac_key = _client_key(client)
            if mac_key is None:
                continue
            router_id = _client_router_id(client, equipment)
            uid = _tracker_unique_id(entry_id, router_id, mac_key)
            if uid not in active_clients:
                active_clients[uid] = (router_id, mac_key, client)

        new_entities: list[SpectrumClientTracker] = []
        for uid, (router_id, mac_key, client) in active_clients.items():
            if uid in added_unique_ids:
                continue
            added_unique_ids.add(uid)
            new_entities.append(
                SpectrumClientTracker(
                    network_coordinator,
                    equipment_coordinator,
                    entry_id,
                    router_id,
                    mac_key,
                    client,
                    _tracker_enabled_default(tracker_mode, client),
                )
            )

        if new_entities:
            async_add_entities(new_entities)

        ent_reg = er.async_get(hass)
        stale_entries = [
            reg_entry
            for reg_entry in er.async_entries_for_config_entry(ent_reg, entry_id)
            if reg_entry.domain == Platform.DEVICE_TRACKER
            and reg_entry.unique_id.startswith(prefix)
            and reg_entry.unique_id not in active_clients
        ]
        for reg_entry in stale_entries:
            ent_reg.async_remove(reg_entry.entity_id)
            added_unique_ids.discard(reg_entry.unique_id)

    _async_sync_clients()
    entry.async_on_unload(network_coordinator.async_add_listener(_async_sync_clients))
    entry.async_on_unload(equipment_coordinator.async_add_listener(_async_sync_clients))
