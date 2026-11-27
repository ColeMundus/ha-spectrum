from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from aiospectrum import (
    AccountPlan,
    AccountState,
    AdditionalNetwork,
    Client,
    EquipmentState,
    Modem,
    NetworkState,
    Pod,
    Router,
    SecurityShield,
    SecurityThreat,
    ServiceAppointment,
    ServiceProblem,
    SpeedTestResult,
    WifiCoverage,
    WifiNetwork,
    normalize_mac,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfDataRate,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

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
)

PARALLEL_UPDATES = 0

_MAX_STATE_LENGTH = 255


def _truncate_state(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) <= _MAX_STATE_LENGTH:
        return value
    return f"{value[: _MAX_STATE_LENGTH - 1]}…"


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    parsed = dt_util.parse_datetime(cleaned)
    if parsed is not None:
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=dt_util.UTC)
        return parsed
    try:
        numeric = float(cleaned)
    except ValueError:
        return None
    if numeric > 1_000_000_000_000:
        numeric /= 1000.0
    return datetime.fromtimestamp(numeric, tz=dt_util.UTC)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    parsed_date = dt_util.parse_date(cleaned)
    if parsed_date is not None:
        return parsed_date
    parsed_dt = dt_util.parse_datetime(cleaned)
    if parsed_dt is not None:
        return parsed_dt.date()
    slash_parts = cleaned.split("/")
    if len(slash_parts) == 3 and all(part.isdigit() for part in slash_parts):
        month, day, year = (int(part) for part in slash_parts)
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _active_outages(bundle: EquipmentBundle) -> list[ServiceProblem]:
    return [item for item in bundle.outages if item.is_active]


def _primary_outage(bundle: EquipmentBundle) -> ServiceProblem | None:
    active = _active_outages(bundle)
    return active[0] if active else None


def _next_appointment(bundle: EquipmentBundle) -> ServiceAppointment | None:
    if not bundle.appointments:
        return None
    upcoming = [appt for appt in bundle.appointments if appt.past_end_time is not True]
    candidates = upcoming if upcoming else bundle.appointments

    def _sort_key(appt: ServiceAppointment) -> tuple[int, datetime]:
        parsed = _parse_timestamp(appt.start_time)
        if parsed is not None:
            return (0, parsed)
        return (1, datetime.max.replace(tzinfo=dt_util.UTC))

    return min(candidates, key=_sort_key)


def _provisioned_services_list(plan: AccountPlan) -> list[str]:
    entries = (
        ("internet", plan.internet_provisioned),
        ("wifi", plan.wifi_provisioned),
        ("tv", plan.tv_provisioned),
        ("voice", plan.voice_provisioned),
        ("mobile", plan.mobile_provisioned),
        ("secure_connect", plan.secure_connect_provisioned),
    )
    return [name for name, provisioned in entries if provisioned is True]


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


def _primary_wifi(router: Router) -> WifiNetwork | None:
    for net in router.wifi_networks:
        if net.is_primary and net.name:
            return net
    for net in router.wifi_networks:
        if net.name:
            return net
    return router.wifi_networks[0] if router.wifi_networks else None


def _primary_additional_network(router: Router) -> AdditionalNetwork | None:
    for net in router.additional_networks:
        if net.name:
            return net
    return router.additional_networks[0] if router.additional_networks else None


def _router_clients(
    network: NetworkState,
    equipment: EquipmentState | None,
    router: Router,
) -> list[Client]:
    single_router = equipment is not None and len(equipment.routers) == 1
    result: list[Client] = []
    for client in network.clients:
        if client.router_id == router.id:
            result.append(client)
        elif client.router_id is None and (
            (router.network_id is not None and client.network_id == router.network_id)
            or single_router
        ):
            result.append(client)
    return result


def _router_ip_reservation_count(
    network: NetworkState,
    equipment: EquipmentState | None,
    router: Router,
) -> int:
    single_router = equipment is not None and len(equipment.routers) == 1
    client_macs = {
        normalize_mac(client.mac_address)
        for client in _router_clients(network, equipment, router)
        if client.mac_address
    }
    count = 0
    for reservation in network.ip_reservations:
        if reservation.router_id == router.id:
            count += 1
        elif reservation.router_id is None and (
            single_router
            or (
                reservation.mac_address
                and normalize_mac(reservation.mac_address) in client_macs
            )
        ):
            count += 1
    return count


def _router_port_forward_count(
    network: NetworkState,
    equipment: EquipmentState | None,
    router: Router,
) -> int:
    single_router = equipment is not None and len(equipment.routers) == 1
    if single_router:
        return len(network.port_forwards)
    client_macs = {
        normalize_mac(client.mac_address)
        for client in _router_clients(network, equipment, router)
        if client.mac_address
    }
    client_ids = {
        client.id for client in _router_clients(network, equipment, router) if client.id
    }
    return sum(
        1
        for pf in network.port_forwards
        if (pf.client_id and pf.client_id in client_ids)
        or (pf.mac_address and normalize_mac(pf.mac_address) in client_macs)
    )


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
        client_ids = {
            client.id
            for client in _router_clients(network, equipment, router)
            if client.id
        }
        return [threat for threat in network.threats if threat.client_id in client_ids]
    return []


def _router_wifi_coverage(
    router: Router,
    network: NetworkState | None,
) -> WifiCoverage | None:
    if network is None:
        return None
    if router.network_id and router.network_id in network.wifi_coverage:
        return network.wifi_coverage[router.network_id]
    if len(network.wifi_coverage) == 1:
        return next(iter(network.wifi_coverage.values()))
    return None


def _pod_coverage_score(
    pod: Pod,
    network: NetworkState | None,
) -> str | None:
    if network is not None:
        if pod.network_id and pod.network_id in network.wifi_coverage:
            cov = network.wifi_coverage[pod.network_id]
            score = cov.pod_scores.get(pod.id) or cov.historical_pod_scores.get(pod.id)
            if score is not None:
                return score
        for cov in network.wifi_coverage.values():
            score = cov.pod_scores.get(pod.id) or cov.historical_pod_scores.get(pod.id)
            if score is not None:
                return score
    return pod.coverage_score or pod.historical_coverage_score


def _latest_speed_test_for_router(
    bundle: EquipmentBundle,
    router: Router,
) -> SpeedTestResult | None:
    history = bundle.speed_test_history or bundle.equipment.speed_test_history
    if not history:
        return None
    for item in history:
        if item.equipment_id == router.id:
            return item
    if len(bundle.equipment.routers) == 1:
        return history[0]
    for item in history:
        if item.equipment_id is None:
            return item
    return None


def _modem_target_device_info(
    entry_id: str,
    equipment: EquipmentState,
    modem: Modem,
) -> DeviceInfo:
    if is_merged_combo_modem(equipment, modem):
        router = _find_router(equipment, modem.id)
        if router is not None:
            return router_device_info(entry_id, router, modem)
    return modem_device_info(entry_id, modem)


@dataclass(frozen=True, kw_only=True)
class SpectrumAccountEquipmentSensorDescription(SensorEntityDescription):
    value_fn: Callable[[EquipmentBundle], Any]
    attrs_fn: Callable[[EquipmentBundle], dict[str, Any] | None] | None = None


@dataclass(frozen=True, kw_only=True)
class SpectrumAccountBillingSensorDescription(SensorEntityDescription):
    value_fn: Callable[[AccountState], Any]
    attrs_fn: Callable[[AccountState], dict[str, Any] | None] | None = None


@dataclass(frozen=True, kw_only=True)
class SpectrumRouterEquipmentSensorDescription(SensorEntityDescription):
    value_fn: Callable[[EquipmentBundle, Router], Any]
    attrs_fn: Callable[[EquipmentBundle, Router], dict[str, Any] | None] | None = None
    unit_fn: Callable[[EquipmentBundle, Router], str | None] | None = None


@dataclass(frozen=True, kw_only=True)
class SpectrumRouterNetworkSensorDescription(SensorEntityDescription):
    value_fn: Callable[[NetworkState, EquipmentState | None, Router], Any]
    attrs_fn: (
        Callable[[NetworkState, EquipmentState | None, Router], dict[str, Any] | None]
        | None
    ) = None


@dataclass(frozen=True, kw_only=True)
class SpectrumModemSensorDescription(SensorEntityDescription):
    value_fn: Callable[[EquipmentState, Modem], Any]


@dataclass(frozen=True, kw_only=True)
class SpectrumPodEquipmentSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Pod], Any]


def _outage_message_attrs(bundle: EquipmentBundle) -> dict[str, Any] | None:
    outage = _primary_outage(bundle)
    if outage is None:
        return None
    return {
        "full_message": outage.message,
        "short_message": outage.short_message,
        "event_id": outage.evt_id,
    }


def _next_appointment_attrs(bundle: EquipmentBundle) -> dict[str, Any] | None:
    appt = _next_appointment(bundle)
    if appt is None:
        return None
    return {
        "appointment_id": appt.appointment_id,
        "status": appt.status,
        "work_order_type": appt.work_order_type,
        "order_class": appt.order_class,
        "end_time": appt.end_time,
        "timezone": appt.timezone,
        "held": appt.held,
    }


def _provisioned_services_attrs(state: AccountState) -> dict[str, Any]:
    plan = state.plan
    return {
        "internet": plan.internet_provisioned,
        "wifi": plan.wifi_provisioned,
        "tv": plan.tv_provisioned,
        "voice": plan.voice_provisioned,
        "mobile": plan.mobile_provisioned,
        "secure_connect": plan.secure_connect_provisioned,
    }


def _wifi_network_attrs(
    _bundle: EquipmentBundle, router: Router
) -> dict[str, Any] | None:
    wifi = _primary_wifi(router)
    if wifi is None:
        return None
    return {
        "bands": wifi.bands,
        "security_mode": wifi.security_mode,
        "broadcast_hidden": wifi.broadcast_hidden,
    }


def _additional_network_attrs(
    _bundle: EquipmentBundle, router: Router
) -> dict[str, Any] | None:
    net = _primary_additional_network(router)
    if net is None:
        return None
    return {
        "band": net.band,
        "security_mode": net.security_mode,
        "broadcast": net.broadcast,
        "isolated": net.isolated,
        "expires_at": net.expires_at,
    }


def _cellular_usage_attrs(
    _bundle: EquipmentBundle, router: Router
) -> dict[str, Any] | None:
    cb = router.cellular_backup
    if cb is None:
        return None
    return {
        "limit": cb.data_usage_limit,
        "unit": cb.data_usage_unit,
    }


def _clean_status_value(value: str | None) -> str | None:
    if not value:
        return None
    if value.strip().upper().startswith("UNKNOWN_BECAUSE_"):
        return None
    return value


def _cellular_backup_status(router: Router) -> str | None:
    cb = router.cellular_backup
    if cb is None:
        return None
    return _clean_status_value(cb.failover_status) or _clean_status_value(
        cb.activation_status
    )


def _has_thread_channel(router: Router) -> bool:
    tn = router.thread_network
    if tn is None:
        return False
    if tn.channel is not None:
        return True
    has_data = any(
        val is not None
        for val in (
            tn.radio_state,
            tn.enabled,
            tn.source,
            tn.extended_pan_id,
            tn.network_name,
            tn.pan_id,
        )
    )
    return router.supports("THREAD_NETWORK") and has_data


def _cellular_status_attrs(
    _bundle: EquipmentBundle, router: Router
) -> dict[str, Any] | None:
    cb = router.cellular_backup
    if cb is None:
        return None
    return {
        "activation_status": _clean_status_value(cb.activation_status),
        "power_source_in_use": cb.power_source_in_use,
        "last_failover_start": cb.last_failover_start,
    }


def _thread_attrs(_bundle: EquipmentBundle, router: Router) -> dict[str, Any] | None:
    tn = router.thread_network
    if tn is None:
        return None
    return {
        "network_name": tn.network_name,
        "pan_id": tn.pan_id,
        "radio_state": tn.radio_state,
        "source": tn.source,
    }


def _wifi_coverage_attrs(
    network: NetworkState,
    _equipment: EquipmentState | None,
    router: Router,
) -> dict[str, Any] | None:
    cov = _router_wifi_coverage(router, network)
    if cov is None:
        return None
    return {
        "recommendation": cov.recommendation,
        "last_checked_timestamp": cov.last_checked_timestamp,
        "client_count": cov.client_count,
        "pod_count": cov.pod_count,
    }


ACCOUNT_EQUIPMENT_SENSORS: tuple[SpectrumAccountEquipmentSensorDescription, ...] = (
    SpectrumAccountEquipmentSensorDescription(
        key="outage_count",
        translation_key="outage_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda bundle: len(_active_outages(bundle)),
    ),
    SpectrumAccountEquipmentSensorDescription(
        key="outage_status",
        translation_key="outage_status",
        value_fn=lambda bundle: (
            outage.status if (outage := _primary_outage(bundle)) is not None else None
        ),
    ),
    SpectrumAccountEquipmentSensorDescription(
        key="outage_message",
        translation_key="outage_message",
        value_fn=lambda bundle: (
            _truncate_state(outage.short_message or outage.message)
            if (outage := _primary_outage(bundle)) is not None
            else None
        ),
        attrs_fn=_outage_message_attrs,
    ),
    SpectrumAccountEquipmentSensorDescription(
        key="outage_services_affected",
        translation_key="outage_services_affected",
        value_fn=lambda bundle: (
            _truncate_state(", ".join(outage.services_affected))
            if (outage := _primary_outage(bundle)) is not None
            and outage.services_affected
            else None
        ),
    ),
    SpectrumAccountEquipmentSensorDescription(
        key="outage_estimated_resolution",
        translation_key="outage_estimated_resolution",
        value_fn=lambda bundle: (
            _truncate_state(outage.estimated_resolution)
            if (outage := _primary_outage(bundle)) is not None
            else None
        ),
    ),
    SpectrumAccountEquipmentSensorDescription(
        key="outage_cause",
        translation_key="outage_cause",
        value_fn=lambda bundle: (
            _truncate_state(outage.outage_cause_type or outage.outage_detail)
            if (outage := _primary_outage(bundle)) is not None
            else None
        ),
    ),
    SpectrumAccountEquipmentSensorDescription(
        key="next_service_appointment",
        translation_key="next_service_appointment",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda bundle: (
            _parse_timestamp(appt.start_time)
            if (appt := _next_appointment(bundle)) is not None
            else None
        ),
        attrs_fn=_next_appointment_attrs,
    ),
    SpectrumAccountEquipmentSensorDescription(
        key="service_appointment_status",
        translation_key="service_appointment_status",
        value_fn=lambda bundle: (
            appt.status
            if (appt := _next_appointment(bundle)) is not None
            else (
                bundle.appointment_alert.appointment_status
                if bundle.appointment_alert is not None
                else None
            )
        ),
    ),
    SpectrumAccountEquipmentSensorDescription(
        key="service_appointment_count",
        translation_key="service_appointment_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda bundle: len(bundle.appointments),
    ),
)

ACCOUNT_BILLING_SENSORS: tuple[SpectrumAccountBillingSensorDescription, ...] = (
    SpectrumAccountBillingSensorDescription(
        key="current_balance",
        translation_key="current_balance",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        value_fn=lambda state: (
            state.billing_summary.current_balance
            if state.billing_summary is not None
            else None
        ),
    ),
    SpectrumAccountBillingSensorDescription(
        key="amount_due",
        translation_key="amount_due",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        value_fn=lambda state: (
            state.billing_summary.total_amount_due
            if state.billing_summary is not None
            else None
        ),
    ),
    SpectrumAccountBillingSensorDescription(
        key="past_due_amount",
        translation_key="past_due_amount",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        value_fn=lambda state: (
            state.billing_summary.past_due_amount
            if state.billing_summary is not None
            else None
        ),
    ),
    SpectrumAccountBillingSensorDescription(
        key="pending_payment",
        translation_key="pending_payment",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        value_fn=lambda state: (
            state.billing_summary.pending_payment_amount
            if state.billing_summary is not None
            else None
        ),
    ),
    SpectrumAccountBillingSensorDescription(
        key="last_payment",
        translation_key="last_payment",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        value_fn=lambda state: (
            state.billing_summary.last_payment_amount
            if state.billing_summary is not None
            else None
        ),
    ),
    SpectrumAccountBillingSensorDescription(
        key="upcoming_autopay_charge",
        translation_key="upcoming_autopay_charge",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        value_fn=lambda state: (
            state.billing_summary.upcoming_autopay_charge_amount
            if state.billing_summary is not None
            else None
        ),
    ),
    SpectrumAccountBillingSensorDescription(
        key="payment_due_date",
        translation_key="payment_due_date",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda state: (
            _parse_date(state.billing_summary.due_date)
            if state.billing_summary is not None
            else None
        ),
    ),
    SpectrumAccountBillingSensorDescription(
        key="next_autopay_date",
        translation_key="next_autopay_date",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda state: (
            _parse_date(
                state.billing_summary.payment_date_for_autopay
                or state.billing_summary.debit_date
                or state.billing_summary.first_autopay_date
            )
            if state.billing_summary is not None
            else None
        ),
    ),
    SpectrumAccountBillingSensorDescription(
        key="next_billing_cycle",
        translation_key="next_billing_cycle",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda state: (
            _parse_date(state.billing_summary.next_cycle_date)
            if state.billing_summary is not None
            else None
        ),
    ),
    SpectrumAccountBillingSensorDescription(
        key="account_status",
        translation_key="account_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: (
            state.billing_summary.account_status
            if state.billing_summary is not None
            else None
        ),
    ),
    SpectrumAccountBillingSensorDescription(
        key="monetary_status",
        translation_key="monetary_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: (
            state.billing_summary.monetary_status
            if state.billing_summary is not None
            else None
        ),
    ),
    SpectrumAccountBillingSensorDescription(
        key="internet_plan",
        translation_key="internet_plan",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.plan.internet_plan_name,
    ),
    SpectrumAccountBillingSensorDescription(
        key="subscribed_download_speed",
        translation_key="subscribed_download_speed",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.plan.subscribed_download_mbps,
    ),
    SpectrumAccountBillingSensorDescription(
        key="subscribed_upload_speed",
        translation_key="subscribed_upload_speed",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.plan.subscribed_upload_mbps,
    ),
    SpectrumAccountBillingSensorDescription(
        key="provisioned_services",
        translation_key="provisioned_services",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: (
            ", ".join(services)
            if (services := _provisioned_services_list(state.plan))
            else None
        ),
        attrs_fn=_provisioned_services_attrs,
    ),
)

ROUTER_CORE_NETWORK_SENSORS: tuple[SpectrumRouterNetworkSensorDescription, ...] = (
    SpectrumRouterNetworkSensorDescription(
        key="connected_clients",
        translation_key="connected_clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda network, equipment, router: sum(
            1
            for client in _router_clients(network, equipment, router)
            if client.connected is True
        ),
    ),
    SpectrumRouterNetworkSensorDescription(
        key="total_clients",
        translation_key="total_clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda network, equipment, router: len(
            _router_clients(network, equipment, router)
        ),
    ),
    SpectrumRouterNetworkSensorDescription(
        key="paused_clients",
        translation_key="paused_clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda network, equipment, router: sum(
            1
            for client in _router_clients(network, equipment, router)
            if client.paused is True
        ),
    ),
    SpectrumRouterNetworkSensorDescription(
        key="ip_reservation_count",
        translation_key="ip_reservation_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_router_ip_reservation_count,
    ),
    SpectrumRouterNetworkSensorDescription(
        key="port_forward_count",
        translation_key="port_forward_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_router_port_forward_count,
    ),
)

ROUTER_SECURITY_THREATS_DESC = SpectrumRouterNetworkSensorDescription(
    key="security_shield_threats",
    translation_key="security_shield_threats",
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda network, equipment, router: len(
        _router_threats(router, network, equipment)
    ),
)

ROUTER_WIFI_COVERAGE_DESC = SpectrumRouterNetworkSensorDescription(
    key="wifi_coverage_score",
    translation_key="wifi_coverage_score",
    value_fn=lambda network, _equipment, router: (
        cov.overall_historical_score
        if (cov := _router_wifi_coverage(router, network)) is not None
        else None
    ),
    attrs_fn=_wifi_coverage_attrs,
)

ROUTER_CORE_EQUIPMENT_SENSORS: tuple[SpectrumRouterEquipmentSensorDescription, ...] = (
    SpectrumRouterEquipmentSensorDescription(
        key="wan_ipv4",
        translation_key="wan_ipv4",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _bundle, router: router.wan_ipv4,
    ),
    SpectrumRouterEquipmentSensorDescription(
        key="wan_ipv6",
        translation_key="wan_ipv6",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _bundle, router: router.wan_ipv6,
    ),
    SpectrumRouterEquipmentSensorDescription(
        key="connection_state",
        translation_key="connection_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda bundle, router: (
            router.connection_state
            or (
                conn.status
                if (conn := bundle.equipment.connection_states.get(router.id))
                is not None
                else None
            )
        ),
    ),
    SpectrumRouterEquipmentSensorDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda _bundle, router: router.firmware_version,
    ),
)

ROUTER_LAN_IPV4_DESC = SpectrumRouterEquipmentSensorDescription(
    key="lan_ipv4",
    translation_key="lan_ipv4",
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda _bundle, router: router.lan_ipv4,
)

ROUTER_DNS_SENSORS: tuple[SpectrumRouterEquipmentSensorDescription, ...] = (
    SpectrumRouterEquipmentSensorDescription(
        key="dns_mode",
        translation_key="dns_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _bundle, router: router.dns.mode if router.dns else None,
    ),
    SpectrumRouterEquipmentSensorDescription(
        key="primary_dns",
        translation_key="primary_dns",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _bundle, router: router.dns.primary if router.dns else None,
    ),
    SpectrumRouterEquipmentSensorDescription(
        key="secondary_dns",
        translation_key="secondary_dns",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _bundle, router: router.dns.secondary if router.dns else None,
    ),
)

ROUTER_WIFI_NAME_DESC = SpectrumRouterEquipmentSensorDescription(
    key="wifi_network_name",
    translation_key="wifi_network_name",
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda _bundle, router: (
        wifi.name if (wifi := _primary_wifi(router)) is not None else None
    ),
    attrs_fn=_wifi_network_attrs,
)

ROUTER_WIFI_PASSWORD_DESC = SpectrumRouterEquipmentSensorDescription(
    key="wifi_password",
    translation_key="wifi_password",
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    value_fn=lambda _bundle, router: (
        wifi.password if (wifi := _primary_wifi(router)) is not None else None
    ),
)

ROUTER_ADDITIONAL_NAME_DESC = SpectrumRouterEquipmentSensorDescription(
    key="additional_network_name",
    translation_key="additional_network_name",
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda _bundle, router: (
        net.name if (net := _primary_additional_network(router)) is not None else None
    ),
    attrs_fn=_additional_network_attrs,
)

ROUTER_ADDITIONAL_PASSWORD_DESC = SpectrumRouterEquipmentSensorDescription(
    key="additional_network_password",
    translation_key="additional_network_password",
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    value_fn=lambda _bundle, router: (
        net.password
        if (net := _primary_additional_network(router)) is not None
        else None
    ),
)

ROUTER_SPEED_TEST_SENSORS: tuple[SpectrumRouterEquipmentSensorDescription, ...] = (
    SpectrumRouterEquipmentSensorDescription(
        key="speed_test_download",
        translation_key="speed_test_download",
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        value_fn=lambda bundle, router: (
            res.download_mbps
            if (res := _latest_speed_test_for_router(bundle, router)) is not None
            else None
        ),
    ),
    SpectrumRouterEquipmentSensorDescription(
        key="speed_test_upload",
        translation_key="speed_test_upload",
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        value_fn=lambda bundle, router: (
            res.upload_mbps
            if (res := _latest_speed_test_for_router(bundle, router)) is not None
            else None
        ),
    ),
    SpectrumRouterEquipmentSensorDescription(
        key="speed_test_latency",
        translation_key="speed_test_latency",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        value_fn=lambda bundle, router: (
            res.latency_ms
            if (res := _latest_speed_test_for_router(bundle, router)) is not None
            else None
        ),
    ),
    SpectrumRouterEquipmentSensorDescription(
        key="speed_test_jitter",
        translation_key="speed_test_jitter",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        value_fn=lambda bundle, router: (
            res.jitter_ms
            if (res := _latest_speed_test_for_router(bundle, router)) is not None
            else None
        ),
    ),
    SpectrumRouterEquipmentSensorDescription(
        key="speed_test_packet_loss",
        translation_key="speed_test_packet_loss",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda bundle, router: (
            res.packet_loss_pct
            if (res := _latest_speed_test_for_router(bundle, router)) is not None
            else None
        ),
    ),
    SpectrumRouterEquipmentSensorDescription(
        key="speed_test_last_run",
        translation_key="speed_test_last_run",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda bundle, router: (
            _parse_timestamp(res.timestamp)
            if (res := _latest_speed_test_for_router(bundle, router)) is not None
            else None
        ),
    ),
    SpectrumRouterEquipmentSensorDescription(
        key="speed_test_status",
        translation_key="speed_test_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda bundle, router: (
            res.status
            if (res := _latest_speed_test_for_router(bundle, router)) is not None
            else None
        ),
    ),
)

ROUTER_CELLULAR_BACKUP_SIGNAL_DESC = SpectrumRouterEquipmentSensorDescription(
    key="cellular_backup_signal",
    translation_key="cellular_backup_signal",
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda _bundle, router: (
        router.cellular_backup.signal_strength
        if router.cellular_backup is not None
        else None
    ),
)

ROUTER_CELLULAR_BACKUP_SENSORS: tuple[SpectrumRouterEquipmentSensorDescription, ...] = (
    SpectrumRouterEquipmentSensorDescription(
        key="cellular_backup_usage",
        translation_key="cellular_backup_usage",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda _bundle, router: (
            router.cellular_backup.data_usage_current
            if router.cellular_backup is not None
            else None
        ),
        attrs_fn=_cellular_usage_attrs,
        unit_fn=lambda _bundle, router: (
            router.cellular_backup.data_usage_unit
            if router.cellular_backup is not None
            else None
        ),
    ),
    SpectrumRouterEquipmentSensorDescription(
        key="cellular_backup_status",
        translation_key="cellular_backup_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _bundle, router: _cellular_backup_status(router),
        attrs_fn=_cellular_status_attrs,
    ),
)

ROUTER_THREAD_CHANNEL_DESC = SpectrumRouterEquipmentSensorDescription(
    key="thread_channel",
    translation_key="thread_channel",
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda _bundle, router: (
        router.thread_network.channel if router.thread_network is not None else None
    ),
    attrs_fn=_thread_attrs,
)

MODEM_SENSORS: tuple[SpectrumModemSensorDescription, ...] = (
    SpectrumModemSensorDescription(
        key="modem_connection_state",
        translation_key="modem_connection_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda equipment, modem: (
            modem.connection_state
            or (
                conn.status
                if (conn := equipment.connection_states.get(modem.id)) is not None
                else None
            )
        ),
    ),
    SpectrumModemSensorDescription(
        key="docsis_capability",
        translation_key="docsis_capability",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _equipment, modem: modem.docsis_capability,
    ),
)

POD_EQUIPMENT_SENSORS: tuple[SpectrumPodEquipmentSensorDescription, ...] = (
    SpectrumPodEquipmentSensorDescription(
        key="pod_connected_clients",
        translation_key="pod_connected_clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda pod: pod.connected_client_count,
    ),
    SpectrumPodEquipmentSensorDescription(
        key="pod_backhaul_type",
        translation_key="pod_backhaul_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda pod: pod.backhaul_connection_type,
    ),
    SpectrumPodEquipmentSensorDescription(
        key="pod_backhaul_frequency",
        translation_key="pod_backhaul_frequency",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda pod: pod.backhaul_frequency,
    ),
)


class SpectrumAccountEquipmentSensor(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator], SensorEntity
):
    entity_description: SpectrumAccountEquipmentSensorDescription

    def __init__(
        self,
        coordinator: SpectrumEquipmentCoordinator,
        entry_id: str,
        description: SpectrumAccountEquipmentSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}:account:{description.key}"
        self._attr_device_info = account_device_info(entry_id)

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class SpectrumAccountBillingSensor(
    SpectrumCoordinatorEntity[SpectrumAccountCoordinator], SensorEntity
):
    entity_description: SpectrumAccountBillingSensorDescription

    def __init__(
        self,
        coordinator: SpectrumAccountCoordinator,
        entry_id: str,
        description: SpectrumAccountBillingSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}:account:{description.key}"
        self._attr_device_info = account_device_info(entry_id)

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class SpectrumRouterEquipmentSensor(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator], SensorEntity
):
    entity_description: SpectrumRouterEquipmentSensorDescription

    def __init__(
        self,
        coordinator: SpectrumEquipmentCoordinator,
        entry_id: str,
        router: Router,
        modem: Modem | None,
        description: SpectrumRouterEquipmentSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._router_id = router.id
        self._attr_unique_id = f"{entry_id}:router:{router.id}:{description.key}"
        self._attr_device_info = router_device_info(entry_id, router, modem)

    def _current_router(self) -> Router | None:
        if self.coordinator.data is None:
            return None
        return _find_router(self.coordinator.data.equipment, self._router_id)

    @property
    def available(self) -> bool:
        return super().available and self._current_router() is not None

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        router = self._current_router()
        if router is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data, router)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if (
            self.entity_description.unit_fn is not None
            and self.coordinator.data is not None
        ):
            router = self._current_router()
            if router is not None:
                return self.entity_description.unit_fn(self.coordinator.data, router)
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None or self.entity_description.attrs_fn is None:
            return None
        router = self._current_router()
        if router is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data, router)


class SpectrumRouterNetworkSensor(
    SpectrumCoordinatorEntity[SpectrumNetworkCoordinator], SensorEntity
):
    entity_description: SpectrumRouterNetworkSensorDescription

    def __init__(
        self,
        network_coordinator: SpectrumNetworkCoordinator,
        equipment_coordinator: SpectrumEquipmentCoordinator,
        entry_id: str,
        router: Router,
        modem: Modem | None,
        description: SpectrumRouterNetworkSensorDescription,
    ) -> None:
        super().__init__(network_coordinator)
        self._equipment_coordinator = equipment_coordinator
        self.entity_description = description
        self._router_id = router.id
        self._attr_unique_id = f"{entry_id}:router:{router.id}:{description.key}"
        self._attr_device_info = router_device_info(entry_id, router, modem)

    def _current_router(self) -> Router | None:
        if self._equipment_coordinator.data is None:
            return None
        return _find_router(self._equipment_coordinator.data.equipment, self._router_id)

    @property
    def available(self) -> bool:
        return super().available and self._current_router() is not None

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        router = self._current_router()
        if router is None:
            return None
        equipment = (
            self._equipment_coordinator.data.equipment
            if self._equipment_coordinator.data is not None
            else None
        )
        return self.entity_description.value_fn(
            self.coordinator.data, equipment, router
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None or self.entity_description.attrs_fn is None:
            return None
        router = self._current_router()
        if router is None:
            return None
        equipment = (
            self._equipment_coordinator.data.equipment
            if self._equipment_coordinator.data is not None
            else None
        )
        return self.entity_description.attrs_fn(
            self.coordinator.data, equipment, router
        )


class SpectrumModemSensor(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator], SensorEntity
):
    entity_description: SpectrumModemSensorDescription

    def __init__(
        self,
        coordinator: SpectrumEquipmentCoordinator,
        entry_id: str,
        modem: Modem,
        device_info: DeviceInfo,
        description: SpectrumModemSensorDescription,
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
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        modem = _find_modem(self.coordinator.data.equipment, self._modem_id)
        if modem is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data.equipment, modem)


class SpectrumPodEquipmentSensor(
    SpectrumCoordinatorEntity[SpectrumEquipmentCoordinator], SensorEntity
):
    entity_description: SpectrumPodEquipmentSensorDescription

    def __init__(
        self,
        coordinator: SpectrumEquipmentCoordinator,
        entry_id: str,
        pod: Pod,
        router_id: str | None,
        description: SpectrumPodEquipmentSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._pod_id = pod.id
        self._attr_unique_id = f"{entry_id}:pod:{pod.id}:{description.key}"
        self._attr_device_info = pod_device_info(entry_id, pod, router_id)

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and _find_pod(self.coordinator.data.equipment, self._pod_id) is not None
        )

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        pod = _find_pod(self.coordinator.data.equipment, self._pod_id)
        if pod is None:
            return None
        return self.entity_description.value_fn(pod)


class SpectrumPodCoverageSensor(
    SpectrumCoordinatorEntity[SpectrumNetworkCoordinator], SensorEntity
):
    _attr_translation_key = "pod_coverage_score"

    def __init__(
        self,
        network_coordinator: SpectrumNetworkCoordinator,
        equipment_coordinator: SpectrumEquipmentCoordinator,
        entry_id: str,
        pod: Pod,
        router_id: str | None,
    ) -> None:
        super().__init__(network_coordinator)
        self._equipment_coordinator = equipment_coordinator
        self._pod_id = pod.id
        self._attr_unique_id = f"{entry_id}:pod:{pod.id}:pod_coverage_score"
        self._attr_device_info = pod_device_info(entry_id, pod, router_id)

    def _current_pod(self) -> Pod | None:
        if self._equipment_coordinator.data is None:
            return None
        return _find_pod(self._equipment_coordinator.data.equipment, self._pod_id)

    @property
    def available(self) -> bool:
        return super().available and self._current_pod() is not None

    @property
    def native_value(self) -> str | None:
        pod = self._current_pod()
        if pod is None:
            return None
        return _pod_coverage_score(pod, self.coordinator.data)


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

    initial_entities: list[SensorEntity] = [
        SpectrumAccountEquipmentSensor(equipment_coordinator, entry_id, desc)
        for desc in ACCOUNT_EQUIPMENT_SENSORS
    ]
    initial_entities.extend(
        SpectrumAccountBillingSensor(account_coordinator, entry_id, desc)
        for desc in ACCOUNT_BILLING_SENSORS
    )
    for entity in initial_entities:
        if entity.unique_id:
            added_keys.add(entity.unique_id)
    async_add_entities(initial_entities)

    @callback
    def _async_sync_entities() -> None:
        if equipment_coordinator.data is None:
            return
        bundle = equipment_coordinator.data
        equipment = bundle.equipment
        network = network_coordinator.data

        current_eq_ids = (
            {item.id for item in equipment.routers if item.id}
            | {item.id for item in equipment.modems if item.id}
            | {item.id for item in equipment.pods if item.id}
            | {item.id for item in equipment.set_top_boxes if item.id}
        )
        if current_eq_ids - known_equipment_ids:
            async_register_equipment_devices(hass, entry_id, equipment)
            known_equipment_ids.update(current_eq_ids)

        new_entities: list[SensorEntity] = []

        def _append(entity: SensorEntity) -> None:
            uid = entity.unique_id
            if uid and uid not in added_keys:
                added_keys.add(uid)
                new_entities.append(entity)

        for router in equipment.routers:
            if not router.id:
                continue
            combo_modem = find_combo_modem(equipment, router)

            for net_desc in ROUTER_CORE_NETWORK_SENSORS:
                _append(
                    SpectrumRouterNetworkSensor(
                        network_coordinator,
                        equipment_coordinator,
                        entry_id,
                        router,
                        combo_modem,
                        net_desc,
                    )
                )

            if _router_security_shield(router, network) is not None:
                _append(
                    SpectrumRouterNetworkSensor(
                        network_coordinator,
                        equipment_coordinator,
                        entry_id,
                        router,
                        combo_modem,
                        ROUTER_SECURITY_THREATS_DESC,
                    )
                )

            if (
                router.supports("WIFI_COVERAGE")
                or _router_wifi_coverage(router, network) is not None
            ):
                _append(
                    SpectrumRouterNetworkSensor(
                        network_coordinator,
                        equipment_coordinator,
                        entry_id,
                        router,
                        combo_modem,
                        ROUTER_WIFI_COVERAGE_DESC,
                    )
                )

            for eq_desc in ROUTER_CORE_EQUIPMENT_SENSORS:
                _append(
                    SpectrumRouterEquipmentSensor(
                        equipment_coordinator,
                        entry_id,
                        router,
                        combo_modem,
                        eq_desc,
                    )
                )

            if router.lan_ipv4 is not None:
                _append(
                    SpectrumRouterEquipmentSensor(
                        equipment_coordinator,
                        entry_id,
                        router,
                        combo_modem,
                        ROUTER_LAN_IPV4_DESC,
                    )
                )

            if (
                router.dns is not None
                or router.supports("GET_DNS")
                or router.supports("UPDATE_DNS")
            ):
                for dns_desc in ROUTER_DNS_SENSORS:
                    _append(
                        SpectrumRouterEquipmentSensor(
                            equipment_coordinator,
                            entry_id,
                            router,
                            combo_modem,
                            dns_desc,
                        )
                    )

            primary_wifi = _primary_wifi(router)
            if primary_wifi is not None or router.supports("GET_ACCESS_POINTS"):
                _append(
                    SpectrumRouterEquipmentSensor(
                        equipment_coordinator,
                        entry_id,
                        router,
                        combo_modem,
                        ROUTER_WIFI_NAME_DESC,
                    )
                )
            if primary_wifi is not None and primary_wifi.password:
                _append(
                    SpectrumRouterEquipmentSensor(
                        equipment_coordinator,
                        entry_id,
                        router,
                        combo_modem,
                        ROUTER_WIFI_PASSWORD_DESC,
                    )
                )

            add_net = _primary_additional_network(router)
            if add_net is not None:
                _append(
                    SpectrumRouterEquipmentSensor(
                        equipment_coordinator,
                        entry_id,
                        router,
                        combo_modem,
                        ROUTER_ADDITIONAL_NAME_DESC,
                    )
                )
                if add_net.password:
                    _append(
                        SpectrumRouterEquipmentSensor(
                            equipment_coordinator,
                            entry_id,
                            router,
                            combo_modem,
                            ROUTER_ADDITIONAL_PASSWORD_DESC,
                        )
                    )

            if _latest_speed_test_for_router(bundle, router) is not None:
                for speed_desc in ROUTER_SPEED_TEST_SENSORS:
                    if speed_desc.value_fn(bundle, router) is not None:
                        _append(
                            SpectrumRouterEquipmentSensor(
                                equipment_coordinator,
                                entry_id,
                                router,
                                combo_modem,
                                speed_desc,
                            )
                        )

            if router.cellular_backup is not None:
                if router.cellular_backup.signal_strength is not None:
                    _append(
                        SpectrumRouterEquipmentSensor(
                            equipment_coordinator,
                            entry_id,
                            router,
                            combo_modem,
                            ROUTER_CELLULAR_BACKUP_SIGNAL_DESC,
                        )
                    )
                for cb_desc in ROUTER_CELLULAR_BACKUP_SENSORS:
                    _append(
                        SpectrumRouterEquipmentSensor(
                            equipment_coordinator,
                            entry_id,
                            router,
                            combo_modem,
                            cb_desc,
                        )
                    )

            if _has_thread_channel(router):
                _append(
                    SpectrumRouterEquipmentSensor(
                        equipment_coordinator,
                        entry_id,
                        router,
                        combo_modem,
                        ROUTER_THREAD_CHANNEL_DESC,
                    )
                )

        for modem in equipment.modems:
            if not modem.id:
                continue
            device_info = _modem_target_device_info(entry_id, equipment, modem)
            for modem_desc in MODEM_SENSORS:
                _append(
                    SpectrumModemSensor(
                        equipment_coordinator,
                        entry_id,
                        modem,
                        device_info,
                        modem_desc,
                    )
                )

        for pod in equipment.pods:
            if not pod.id:
                continue
            parent_router_id = find_pod_router_id(equipment, pod)
            for pod_desc in POD_EQUIPMENT_SENSORS:
                _append(
                    SpectrumPodEquipmentSensor(
                        equipment_coordinator,
                        entry_id,
                        pod,
                        parent_router_id,
                        pod_desc,
                    )
                )
            if _pod_coverage_score(pod, network) is not None:
                _append(
                    SpectrumPodCoverageSensor(
                        network_coordinator,
                        equipment_coordinator,
                        entry_id,
                        pod,
                        parent_router_id,
                    )
                )

        if new_entities:
            async_add_entities(new_entities)

    _async_sync_entities()
    entry.async_on_unload(
        equipment_coordinator.async_add_listener(_async_sync_entities)
    )
    entry.async_on_unload(network_coordinator.async_add_listener(_async_sync_entities))
