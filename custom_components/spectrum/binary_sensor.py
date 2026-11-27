from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aiospectrum import (
    AccountState,
    EquipmentState,
    Modem,
    ModemHealth,
    NetworkState,
    Pod,
    Router,
    SecurityShield,
    SecurityThreat,
    ServiceProblem,
    SetTopBox,
)
from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import (
    EquipmentBundle,
    SpectrumAccountCoordinator,
    SpectrumConfigEntry,
    SpectrumEquipmentCoordinator,
    SpectrumNetworkCoordinator,
)
from .entity import (
    SpectrumCoordinatorEntity,
    account_device_info,
    async_register_equipment_devices,
    find_combo_modem,
    find_pod_router_id,
    is_merged_combo_modem,
    modem_device_info,
    pod_device_info,
    router_device_info,
    set_top_box_device_info,
)

PARALLEL_UPDATES = 0

_NEGATIVE_OUTAGE_ELIGIBILITY = frozenset(
    {"NOT_ELIGIBLE", "INELIGIBLE", "NONE", "FALSE", "NO", "UNAVAILABLE", "N/A"}
)
_NEGATIVE_HOTLIST_TOKENS = frozenset(
    {
        "NOT_HOTLISTED",
        "NOT_HOT_LISTED",
        "NONE",
        "FALSE",
        "NO",
        "CLEAR",
        "CLEAN",
        "NORMAL",
        "OK",
        "INACTIVE",
    }
)
_NEGATIVE_QUARANTINE_TOKENS = frozenset(
    {
        "NOT_QUARANTINED",
        "NONE",
        "FALSE",
        "NO",
        "CLEAR",
        "CLEAN",
        "NORMAL",
        "OK",
        "INACTIVE",
    }
)


def _active_outages(bundle: EquipmentBundle) -> list[ServiceProblem]:
    return [item for item in bundle.outages if item.is_active]


def _is_outage_credit_eligible(value: str | None) -> bool:
    if not value:
        return False
    upper = value.strip().upper()
    if (
        upper in _NEGATIVE_OUTAGE_ELIGIBILITY
        or "NOT_ELIGIBLE" in upper
        or "INELIGIBLE" in upper
    ):
        return False
    return "ELIGIBLE" in upper or upper in {"TRUE", "YES", "1"}


def _parse_problem_status(
    value: str | None,
    negative_tokens: frozenset[str],
) -> bool | None:
    if not value:
        return None
    upper = value.strip().upper()
    if upper in negative_tokens or upper.startswith("NOT_"):
        return False
    return True


def _parse_speed_achievable(health: ModemHealth) -> bool | None:
    if health.speed_achievable is not None:
        return health.speed_achievable
    if not health.speed_achievable_status:
        return None
    upper = health.speed_achievable_status.strip().upper()
    if upper in {"YES", "TRUE", "ACHIEVABLE", "OK", "HEALTHY", "PASS", "PASSED"}:
        return True
    if upper in {
        "NO",
        "FALSE",
        "NOT_ACHIEVABLE",
        "UNACHIEVABLE",
        "FAIL",
        "FAILED",
        "UNHEALTHY",
    }:
        return False
    return None


def _find_router(equipment: EquipmentState, router_id: str) -> Router | None:
    for router in equipment.routers:
        if router.id == router_id:
            return router
    return None


def _find_modem(equipment: EquipmentState, modem_id: str) -> Modem | None:
    for modem in equipment.modems:
        if modem.id == modem_id:
            return modem
    return None


def _find_pod(equipment: EquipmentState, pod_id: str) -> Pod | None:
    for pod in equipment.pods:
        if pod.id == pod_id:
            return pod
    return None


def _find_stb(equipment: EquipmentState, stb_id: str) -> SetTopBox | None:
    for stb in equipment.set_top_boxes:
        if stb.id == stb_id:
            return stb
    return None


def _router_security_shield(
    router: Router,
    network: NetworkState | None,
) -> SecurityShield | None:
    if network is not None and router.network_id:
        shield = network.security_shields.get(router.network_id)
        if shield is not None:
            return shield
    return router.security_shield


def _router_threats(
    router: Router,
    network: NetworkState | None,
    equipment: EquipmentState | None,
) -> list[SecurityThreat]:
    shield = _router_security_shield(router, network)
    if shield is not None and shield.threats:
        return shield.threats
    if network is not None and network.threats:
        if equipment is None or len(equipment.routers) <= 1:
            return network.threats
        router_client_ids = {
            client.id
            for client in network.clients
            if client.id
            and (
                client.router_id == router.id
                or (
                    client.router_id is None
                    and router.network_id is not None
                    and client.network_id == router.network_id
                )
            )
        }
        return [
            threat
            for threat in network.threats
            if threat.client_id in router_client_ids
        ]
    return []


def _modem_target_device_info(
    entry_id: str,
    equipment: EquipmentState,
    modem: Modem,
) -> tuple[DeviceInfo, bool]:
    if is_merged_combo_modem(equipment, modem):
        router = _find_router(equipment, modem.id)
        if router is not None:
            return router_device_info(entry_id, router, modem), True
    return modem_device_info(entry_id, modem), False


def _equipment_online(
    equipment: EquipmentState,
    equipment_id: str,
    direct_online: bool | None,
) -> bool | None:
    if direct_online is not None:
        return direct_online
    conn = equipment.connection_states.get(equipment_id)
    return conn.is_online if conn is not None else None


@dataclass(frozen=True, kw_only=True)
class SpectrumAccountEquipmentBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[EquipmentBundle], bool | None]
    attrs_fn: Callable[[EquipmentBundle], dict[str, Any] | None] | None = None


@dataclass(frozen=True, kw_only=True)
class SpectrumAccountBillingBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[AccountState], bool | None]
    attrs_fn: Callable[[AccountState], dict[str, Any] | None] | None = None


@dataclass(frozen=True, kw_only=True)
class SpectrumRouterBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[EquipmentState, Router], bool | None]


@dataclass(frozen=True, kw_only=True)
class SpectrumModemHealthBinarySensorDescription(BinarySensorEntityDescription):
    present_fn: Callable[[ModemHealth], bool]
    value_fn: Callable[[ModemHealth], bool | None]


def _outage_attrs(bundle: EquipmentBundle) -> dict[str, Any] | None:
    active = _active_outages(bundle)
    if not active:
        return None
    primary = active[0]
    return {
        "outage_count": len(active),
        "event_ids": [item.evt_id for item in active if item.evt_id],
        "services_affected": primary.services_affected,
        "estimated_resolution": primary.estimated_resolution,
        "cause": primary.outage_cause_type or primary.outage_detail,
    }


def _appointment_alert_attrs(bundle: EquipmentBundle) -> dict[str, Any] | None:
    alert = bundle.appointment_alert
    if alert is None:
        return None
    return {
        "alert_status": alert.alert_status,
        "appointment_status": alert.appointment_status,
        "appointment_id": alert.appointment_id,
        "sro_type": alert.sro_type,
        "program_type": alert.program_type,
    }


def _autopay_attrs(state: AccountState) -> dict[str, Any] | None:
    billing = state.billing_summary
    if billing is None:
        return None
    return {
        "autopay_locked": billing.autopay_locked,
        "autopay_suspended": billing.autopay_suspended,
    }


def _delinquent_attrs(state: AccountState) -> dict[str, Any] | None:
    billing = state.billing_summary
    if billing is None:
        return None
    return {"delinquency_status": billing.delinquency_status}


ACCOUNT_EQUIPMENT_BINARY_SENSORS: tuple[
    SpectrumAccountEquipmentBinarySensorDescription, ...
] = (
    SpectrumAccountEquipmentBinarySensorDescription(
        key="active_outage",
        translation_key="active_outage",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda bundle: bool(_active_outages(bundle)),
        attrs_fn=_outage_attrs,
    ),
    SpectrumAccountEquipmentBinarySensorDescription(
        key="outage_credit_eligible",
        translation_key="outage_credit_eligible",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda bundle: any(
            _is_outage_credit_eligible(item.outage_credit_eligibility)
            for item in _active_outages(bundle)
        ),
    ),
    SpectrumAccountEquipmentBinarySensorDescription(
        key="outage_credit_enrolled",
        translation_key="outage_credit_enrolled",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda bundle: any(
            item.is_outage_credit_enrolled is True for item in _active_outages(bundle)
        ),
    ),
    SpectrumAccountEquipmentBinarySensorDescription(
        key="outage_callback_eligible",
        translation_key="outage_callback_eligible",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda bundle: any(
            item.is_callback_eligible is True for item in _active_outages(bundle)
        ),
    ),
    SpectrumAccountEquipmentBinarySensorDescription(
        key="service_appointment_alert",
        translation_key="service_appointment_alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda bundle: bool(
            bundle.appointment_alert and bundle.appointment_alert.has_active_alert
        ),
        attrs_fn=_appointment_alert_attrs,
    ),
)

ACCOUNT_BILLING_BINARY_SENSORS: tuple[
    SpectrumAccountBillingBinarySensorDescription, ...
] = (
    SpectrumAccountBillingBinarySensorDescription(
        key="autopay",
        translation_key="autopay",
        value_fn=lambda state: (
            state.billing_summary.autopay_enabled
            if state.billing_summary is not None
            else None
        ),
        attrs_fn=_autopay_attrs,
    ),
    SpectrumAccountBillingBinarySensorDescription(
        key="delinquent",
        translation_key="delinquent",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda state: (
            state.billing_summary.is_delinquent
            if state.billing_summary is not None
            else None
        ),
        attrs_fn=_delinquent_attrs,
    ),
    SpectrumAccountBillingBinarySensorDescription(
        key="seasonal_suspend",
        translation_key="seasonal_suspend",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: (
            state.billing_summary.is_seasonal_suspend
            if state.billing_summary is not None
            else None
        ),
    ),
)

ROUTER_CONNECTIVITY_DESC = SpectrumRouterBinarySensorDescription(
    key="connectivity",
    translation_key="connectivity",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    value_fn=lambda equipment, router: _equipment_online(
        equipment, router.id, router.is_online
    ),
)

ROUTER_ADDITIONAL_NETWORK_DESC = SpectrumRouterBinarySensorDescription(
    key="additional_network_active",
    translation_key="additional_network_active",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    value_fn=lambda _equipment, router: any(
        net.enabled is True or (net.enabled is None and bool(net.name))
        for net in router.additional_networks
    ),
)

ROUTER_CELLULAR_BACKUP_DESCS: tuple[SpectrumRouterBinarySensorDescription, ...] = (
    SpectrumRouterBinarySensorDescription(
        key="cellular_backup_ready",
        translation_key="cellular_backup_ready",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda _equipment, router: (
            router.cellular_backup.is_ready
            if router.cellular_backup is not None
            else None
        ),
    ),
    SpectrumRouterBinarySensorDescription(
        key="cellular_backup_in_use",
        translation_key="cellular_backup_in_use",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda _equipment, router: (
            router.cellular_backup.is_in_use
            if router.cellular_backup is not None
            else None
        ),
    ),
)

MODEM_HEALTH_BINARY_SENSORS: tuple[SpectrumModemHealthBinarySensorDescription, ...] = (
    SpectrumModemHealthBinarySensorDescription(
        key="t4_timeout_threshold",
        translation_key="t4_timeout_threshold",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        present_fn=lambda health: health.t4_timeouts_threshold_reached is not None,
        value_fn=lambda health: health.t4_timeouts_threshold_reached,
    ),
    SpectrumModemHealthBinarySensorDescription(
        key="ethernet_port_speed_healthy",
        translation_key="ethernet_port_speed_healthy",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        present_fn=lambda health: health.ethernet_port_speed_healthy is not None,
        value_fn=lambda health: health.ethernet_port_speed_healthy,
    ),
    SpectrumModemHealthBinarySensorDescription(
        key="speed_achievable",
        translation_key="speed_achievable",
        entity_category=EntityCategory.DIAGNOSTIC,
        present_fn=lambda health: (
            health.speed_achievable is not None
            or health.speed_achievable_status is not None
        ),
        value_fn=_parse_speed_achievable,
    ),
    SpectrumModemHealthBinarySensorDescription(
        key="hot_listed",
        translation_key="hot_listed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        present_fn=lambda health: health.hot_listed_status is not None,
        value_fn=lambda health: _parse_problem_status(
            health.hot_listed_status, _NEGATIVE_HOTLIST_TOKENS
        ),
    ),
    SpectrumModemHealthBinarySensorDescription(
        key="quarantined",
        translation_key="quarantined",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        present_fn=lambda health: health.quarantine_status is not None,
        value_fn=lambda health: _parse_problem_status(
            health.quarantine_status, _NEGATIVE_QUARANTINE_TOKENS
        ),
    ),
)


class SpectrumAccountEquipmentBinarySensor(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator], BinarySensorEntity
):
    entity_description: SpectrumAccountEquipmentBinarySensorDescription

    def __init__(
        self,
        coordinator: SpectrumEquipmentCoordinator,
        entry_id: str,
        description: SpectrumAccountEquipmentBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}:account:{description.key}"
        self._attr_device_info = account_device_info(entry_id)

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class SpectrumAccountBillingBinarySensor(
    SpectrumCoordinatorEntity[SpectrumAccountCoordinator], BinarySensorEntity
):
    entity_description: SpectrumAccountBillingBinarySensorDescription

    def __init__(
        self,
        coordinator: SpectrumAccountCoordinator,
        entry_id: str,
        description: SpectrumAccountBillingBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}:account:{description.key}"
        self._attr_device_info = account_device_info(entry_id)

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class SpectrumRouterBinarySensor(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator], BinarySensorEntity
):
    entity_description: SpectrumRouterBinarySensorDescription

    def __init__(
        self,
        coordinator: SpectrumEquipmentCoordinator,
        entry_id: str,
        router: Router,
        modem: Modem | None,
        description: SpectrumRouterBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._router_id = router.id
        self._attr_unique_id = f"{entry_id}:router:{router.id}:{description.key}"
        self._attr_device_info = router_device_info(entry_id, router, modem)

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and _find_router(self.coordinator.data.equipment, self._router_id)
            is not None
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        router = _find_router(self.coordinator.data.equipment, self._router_id)
        if router is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data.equipment, router)


class SpectrumRouterSecurityThreatsBinarySensor(
    SpectrumCoordinatorEntity[SpectrumNetworkCoordinator], BinarySensorEntity
):
    _attr_translation_key = "security_shield_threats_active"
    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(
        self,
        network_coordinator: SpectrumNetworkCoordinator,
        equipment_coordinator: SpectrumEquipmentCoordinator,
        entry_id: str,
        router: Router,
        modem: Modem | None,
    ) -> None:
        super().__init__(network_coordinator)
        self._equipment_coordinator = equipment_coordinator
        self._router_id = router.id
        self._attr_unique_id = (
            f"{entry_id}:router:{router.id}:security_shield_threats_active"
        )
        self._attr_device_info = router_device_info(entry_id, router, modem)

    def _current_router(self) -> Router | None:
        if self._equipment_coordinator.data is None:
            return None
        return _find_router(self._equipment_coordinator.data.equipment, self._router_id)

    @property
    def available(self) -> bool:
        return super().available and self._current_router() is not None

    def _current_threats(self) -> list[SecurityThreat] | None:
        router = self._current_router()
        if router is None:
            return None
        equipment = (
            self._equipment_coordinator.data.equipment
            if self._equipment_coordinator.data is not None
            else None
        )
        return _router_threats(router, self.coordinator.data, equipment)

    @property
    def is_on(self) -> bool | None:
        threats = self._current_threats()
        if threats is None:
            return None
        return any(threat.ended_at is None for threat in threats)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        threats = self._current_threats()
        if threats is None:
            return None
        active_count = sum(1 for threat in threats if threat.ended_at is None)
        return {
            "active_threats": active_count,
            "total_threats": len(threats),
        }


class SpectrumModemConnectivityBinarySensor(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator], BinarySensorEntity
):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: SpectrumEquipmentCoordinator,
        entry_id: str,
        modem: Modem,
        device_info: DeviceInfo,
        merged: bool,
    ) -> None:
        super().__init__(coordinator)
        self._modem_id = modem.id
        self._attr_translation_key = "modem_connectivity" if merged else "connectivity"
        self._attr_unique_id = f"{entry_id}:modem:{modem.id}:connectivity"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and _find_modem(self.coordinator.data.equipment, self._modem_id) is not None
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        modem = _find_modem(self.coordinator.data.equipment, self._modem_id)
        if modem is None:
            return None
        return _equipment_online(
            self.coordinator.data.equipment, modem.id, modem.is_online
        )


class SpectrumModemHealthBinarySensor(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator], BinarySensorEntity
):
    entity_description: SpectrumModemHealthBinarySensorDescription

    def __init__(
        self,
        coordinator: SpectrumEquipmentCoordinator,
        entry_id: str,
        modem: Modem,
        device_info: DeviceInfo,
        description: SpectrumModemHealthBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._modem_id = modem.id
        self._attr_unique_id = f"{entry_id}:modem:{modem.id}:{description.key}"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and _find_modem(self.coordinator.data.equipment, self._modem_id) is not None
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        health = self.coordinator.data.equipment.modem_health.get(self._modem_id)
        if health is None:
            return None
        return self.entity_description.value_fn(health)


class SpectrumPodConnectivityBinarySensor(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator], BinarySensorEntity
):
    _attr_translation_key = "connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: SpectrumEquipmentCoordinator,
        entry_id: str,
        pod: Pod,
        router_id: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._pod_id = pod.id
        self._attr_unique_id = f"{entry_id}:pod:{pod.id}:connectivity"
        self._attr_device_info = pod_device_info(entry_id, pod, router_id)

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and _find_pod(self.coordinator.data.equipment, self._pod_id) is not None
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        pod = _find_pod(self.coordinator.data.equipment, self._pod_id)
        if pod is None:
            return None
        return _equipment_online(self.coordinator.data.equipment, pod.id, pod.is_online)


class SpectrumSetTopBoxConnectivityBinarySensor(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator], BinarySensorEntity
):
    _attr_translation_key = "connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: SpectrumEquipmentCoordinator,
        entry_id: str,
        stb: SetTopBox,
    ) -> None:
        super().__init__(coordinator)
        self._stb_id = stb.id
        self._attr_unique_id = f"{entry_id}:stb:{stb.id}:connectivity"
        self._attr_device_info = set_top_box_device_info(entry_id, stb)

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and _find_stb(self.coordinator.data.equipment, self._stb_id) is not None
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        stb = _find_stb(self.coordinator.data.equipment, self._stb_id)
        if stb is None:
            return None
        return _equipment_online(self.coordinator.data.equipment, stb.id, stb.is_online)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpectrumConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    equipment_coordinator = runtime.equipment
    network_coordinator = runtime.network
    account_coordinator = runtime.account
    entry_id = entry.entry_id

    added_keys: set[str] = set()
    known_equipment_ids: set[str] = set()

    initial_entities: list[BinarySensorEntity] = [
        SpectrumAccountEquipmentBinarySensor(equipment_coordinator, entry_id, desc)
        for desc in ACCOUNT_EQUIPMENT_BINARY_SENSORS
    ]
    initial_entities.extend(
        SpectrumAccountBillingBinarySensor(account_coordinator, entry_id, desc)
        for desc in ACCOUNT_BILLING_BINARY_SENSORS
    )
    for entity in initial_entities:
        if entity.unique_id:
            added_keys.add(entity.unique_id)
    async_add_entities(initial_entities)

    @callback
    def _async_sync_entities() -> None:
        if equipment_coordinator.data is None:
            return
        equipment = equipment_coordinator.data.equipment
        network = network_coordinator.data

        all_equipment: tuple[Router | Modem | Pod | SetTopBox, ...] = (
            *equipment.routers,
            *equipment.modems,
            *equipment.pods,
            *equipment.set_top_boxes,
        )
        current_eq_ids = {item.id for item in all_equipment if item.id}
        if current_eq_ids - known_equipment_ids:
            async_register_equipment_devices(hass, entry_id, equipment)
            known_equipment_ids.update(current_eq_ids)

        new_entities: list[BinarySensorEntity] = []

        def _append(entity: BinarySensorEntity) -> None:
            uid = entity.unique_id
            if uid and uid not in added_keys:
                added_keys.add(uid)
                new_entities.append(entity)

        for router in equipment.routers:
            if not router.id:
                continue
            combo_modem = find_combo_modem(equipment, router)
            _append(
                SpectrumRouterBinarySensor(
                    equipment_coordinator,
                    entry_id,
                    router,
                    combo_modem,
                    ROUTER_CONNECTIVITY_DESC,
                )
            )
            if _router_security_shield(router, network) is not None:
                _append(
                    SpectrumRouterSecurityThreatsBinarySensor(
                        network_coordinator,
                        equipment_coordinator,
                        entry_id,
                        router,
                        combo_modem,
                    )
                )
            if router.additional_networks or router.supports("ADDITIONAL_NETWORK"):
                _append(
                    SpectrumRouterBinarySensor(
                        equipment_coordinator,
                        entry_id,
                        router,
                        combo_modem,
                        ROUTER_ADDITIONAL_NETWORK_DESC,
                    )
                )
            if router.cellular_backup is not None:
                for desc in ROUTER_CELLULAR_BACKUP_DESCS:
                    _append(
                        SpectrumRouterBinarySensor(
                            equipment_coordinator,
                            entry_id,
                            router,
                            combo_modem,
                            desc,
                        )
                    )

        for modem in equipment.modems:
            if not modem.id:
                continue
            device_info, merged = _modem_target_device_info(entry_id, equipment, modem)
            _append(
                SpectrumModemConnectivityBinarySensor(
                    equipment_coordinator,
                    entry_id,
                    modem,
                    device_info,
                    merged,
                )
            )
            health = equipment.modem_health.get(modem.id)
            if health is not None:
                for health_desc in MODEM_HEALTH_BINARY_SENSORS:
                    if health_desc.present_fn(health):
                        _append(
                            SpectrumModemHealthBinarySensor(
                                equipment_coordinator,
                                entry_id,
                                modem,
                                device_info,
                                health_desc,
                            )
                        )

        for pod in equipment.pods:
            if not pod.id:
                continue
            parent_router_id = find_pod_router_id(equipment, pod)
            _append(
                SpectrumPodConnectivityBinarySensor(
                    equipment_coordinator,
                    entry_id,
                    pod,
                    parent_router_id,
                )
            )

        for stb in equipment.set_top_boxes:
            if not stb.id:
                continue
            if not (stb.supports("CONNECTION_STATE") or stb.is_online is not None):
                continue
            _append(
                SpectrumSetTopBoxConnectivityBinarySensor(
                    equipment_coordinator,
                    entry_id,
                    stb,
                )
            )

        if new_entities:
            async_add_entities(new_entities)

    _async_sync_entities()
    entry.async_on_unload(
        equipment_coordinator.async_add_listener(_async_sync_entities)
    )
    entry.async_on_unload(network_coordinator.async_add_listener(_async_sync_entities))
