from __future__ import annotations

import base64
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiospectrum import (
    DEFAULT_CLIENT_ID,
    AccessSchedule,
    AccountPlan,
    AccountState,
    AdditionalNetwork,
    BillingSummary,
    CellularBackup,
    Client,
    ClientGroup,
    DnsConfig,
    EquipmentConnectionState,
    EquipmentState,
    IpReservation,
    Modem,
    ModemHealth,
    NetworkState,
    Pod,
    PortForward,
    PortForwardCreateResult,
    Router,
    SecurityShield,
    SecurityThreat,
    ServiceAppointment,
    ServiceAppointmentAlert,
    ServiceProblem,
    SetTopBox,
    SpeedTestResult,
    ThreadNetworkConfig,
    TokenSet,
    UpnpConfig,
    WifiCoverage,
    WifiNetwork,
    derive_account_unique_id,
)
from homeassistant.core import HomeAssistant

try:
    from pytest_homeassistant.common import MockConfigEntry
except ModuleNotFoundError:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components
from custom_components.spectrum.const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_DEVICE_UUID,
    CONF_DEVICE_VERIFIER,
    CONF_ID_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SELECTED_ACCT,
    DOMAIN,
)

_COMPONENTS = str(Path(__file__).resolve().parents[1] / "custom_components")
if _COMPONENTS not in custom_components.__path__:
    custom_components.__path__.append(_COMPONENTS)

SYNTHETIC_ACCOUNT_NUMBER = "8345000011223344"
SYNTHETIC_DIVISION_ID = "SYN-DIV-99"
SYNTHETIC_BILLING_ID = "SYN-BILL-7788"
SYNTHETIC_FQ_ACCOUNT_ID = "SYN-FQ-998877"
SYNTHETIC_ACCOUNT_ID = "SYN-ACCT-ID-5544"
SYNTHETIC_SELECTED_ACCT = "SYN-SEL-ACCT-001"
SYNTHETIC_DEVICE_UUID = "00000000-0000-4000-8000-000000000001"
SYNTHETIC_DEVICE_VERIFIER = "syn-device-verifier-secret-999"
SYNTHETIC_ACCESS_TOKEN = "syn-access-token-secret-111"
SYNTHETIC_REFRESH_TOKEN = "syn-refresh-token-secret-222"
SYNTHETIC_NETWORK_ID = "net-syn-001"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    _ = enable_custom_integrations


def make_jwt(claims: dict[str, Any] | None = None) -> str:
    payload_claims = (
        claims
        if claims is not None
        else {
            "acct_num": SYNTHETIC_ACCOUNT_NUMBER,
            "divisionId": SYNTHETIC_DIVISION_ID,
            "username": "synthetic-user-alpha",
            "sub": "sub-synthetic-001",
            "exp": 4102444800,
        }
    )
    header = (
        base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}')
        .rstrip(b"=")
        .decode("ascii")
    )
    body = (
        base64.urlsafe_b64encode(
            json.dumps(payload_claims, separators=(",", ":")).encode("utf-8")
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    return f"{header}.{body}.syn-signature"


def make_token_set(
    *,
    access_token: str = SYNTHETIC_ACCESS_TOKEN,
    refresh_token: str = SYNTHETIC_REFRESH_TOKEN,
    id_token: str | None = None,
    device_verifier: str | None = SYNTHETIC_DEVICE_VERIFIER,
    selected_acct: str | None = SYNTHETIC_SELECTED_ACCT,
) -> TokenSet:
    return TokenSet(
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=make_jwt() if id_token is None else id_token,
        device_verifier=device_verifier,
        selected_acct=selected_acct,
    )


def make_entry_data(
    tokens: TokenSet | None = None,
    *,
    device_uuid: str = SYNTHETIC_DEVICE_UUID,
    client_id: str = DEFAULT_CLIENT_ID,
) -> dict[str, Any]:
    tok = tokens or make_token_set()
    return {
        CONF_ACCESS_TOKEN: tok.access_token,
        CONF_REFRESH_TOKEN: tok.refresh_token,
        CONF_ID_TOKEN: tok.id_token,
        CONF_DEVICE_UUID: device_uuid,
        CONF_DEVICE_VERIFIER: tok.device_verifier,
        CONF_CLIENT_ID: client_id,
        CONF_SELECTED_ACCT: tok.selected_acct,
    }


def make_config_entry(
    hass: HomeAssistant,
    *,
    tokens: TokenSet | None = None,
    unique_id: str | None = None,
    title: str = "Spectrum Synthetic Home",
    options: dict[str, Any] | None = None,
    entry_id: str = "entry-syn-001",
) -> MockConfigEntry:
    tok = tokens or make_token_set()
    resolved_uid = unique_id or derive_account_unique_id(tok.id_token)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        unique_id=resolved_uid,
        entry_id=entry_id,
        data=make_entry_data(tok),
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


def make_router(
    *,
    router_id: str = "router-1",
    network_id: str | None = SYNTHETIC_NETWORK_ID,
    is_combo: bool = False,
    nickname: str | None = "Living Room Router",
    model: str | None = "SAX2V1S",
    with_subsystems: bool = True,
    mac_address: str = "00:00:5E:00:53:01",
    serial_number: str = "SYN-RTR-0001",
) -> Router:
    if with_subsystems:
        return Router(
            id=router_id,
            nickname=nickname,
            model=model,
            equipment_type="ROUTER",
            manufacturer="Spectrum",
            firmware_version="2.4.1-syn",
            serial_number=serial_number,
            mac_address=mac_address,
            wan_ipv4="198.51.100.10",
            wan_ipv6="2001:db8::10",
            lan_ipv4="192.0.2.1",
            connection_state="CONNECTED",
            is_online=True,
            supported_features=[
                "REBOOT",
                "SPEED_TEST",
                "SMART_STEERING",
                "UPDATE_UPNP",
                "GET_UPNP",
                "GET_DNS",
                "UPDATE_DNS",
                "GET_ACCESS_POINTS",
                "ADDITIONAL_NETWORK",
                "THREAD_NETWORK",
                "WIFI_COVERAGE",
                "CELLULAR_NETWORK_BACKUP",
            ],
            network_id=network_id,
            is_combo=is_combo,
            dns=DnsConfig(
                mode="CUSTOM",
                primary="192.0.2.53",
                secondary="198.51.100.53",
                servers=["192.0.2.53", "198.51.100.53"],
                is_custom=True,
            ),
            upnp=UpnpConfig(enabled=True),
            security_shield=SecurityShield(
                enabled=True,
                listen_only=False,
                status="ENABLED",
                network_id=network_id,
                threats=[],
            ),
            wifi_networks=[
                WifiNetwork(
                    id="ap-primary-1",
                    name="SyntheticWiFi-Main",
                    password="SynMainPassword!42",
                    band="5GHZ",
                    bands=["2.4GHZ", "5GHZ", "6GHZ"],
                    security_mode="WPA3",
                    enabled=True,
                    broadcast_hidden=False,
                    is_primary=True,
                    access_point_id="ap-primary-1",
                )
            ],
            additional_networks=[
                AdditionalNetwork(
                    id="add-net-1",
                    name="SyntheticWiFi-Guest",
                    password="SynGuestPassword!99",
                    band="2.4GHZ",
                    security_mode="WPA2",
                    enabled=True,
                    broadcast=True,
                    isolated=True,
                    expires_at="2026-12-31T23:59:59Z",
                )
            ],
            hotspot_status="OPTED_IN",
            hotspot_enabled=True,
            thread_radio_state="ENABLED",
            thread_enabled=True,
            thread_network=ThreadNetworkConfig(
                radio_state="ENABLED",
                enabled=True,
                source="ROUTER",
                channel=15,
                extended_pan_id="1122334455667788",
                network_key="00112233445566778899AABBCCDDEEFF",
                network_name="SynThreadNet",
                pan_id="ABCD",
                is_default_config=False,
            ),
            cellular_backup=CellularBackup(
                activation_status="ACTIVE",
                power_source_in_use="AC",
                is_ready=True,
                is_in_use=False,
                signal_strength=4,
                data_usage_current=1.5,
                data_usage_limit=10.0,
                data_usage_unit="GB",
                failover_status="STANDBY",
                last_failover_start="2026-10-01T12:00:00Z",
            ),
        )
    return Router(
        id=router_id,
        nickname=nickname,
        model=model,
        equipment_type="ROUTER",
        manufacturer="Spectrum",
        firmware_version="1.0.0-min",
        serial_number=serial_number,
        mac_address=mac_address,
        wan_ipv4="198.51.100.20",
        wan_ipv6=None,
        lan_ipv4="192.0.2.1",
        connection_state="CONNECTED",
        is_online=True,
        supported_features=[],
        network_id=network_id,
        is_combo=is_combo,
        dns=None,
        upnp=None,
        security_shield=None,
        wifi_networks=[],
        additional_networks=[],
        hotspot_status="NOT_SUPPORTED",
        hotspot_enabled=None,
        thread_radio_state=None,
        thread_enabled=None,
        thread_network=None,
        cellular_backup=None,
    )


def make_modem(
    *,
    modem_id: str = "modem-1",
    is_combo: bool = False,
    nickname: str | None = "Office Modem",
    model: str | None = "EU2251",
    supported_features: list[str] | None = None,
    mac_address: str = "00:00:5E:00:53:02",
    serial_number: str = "SYN-MDM-0001",
    is_online: bool | None = True,
) -> Modem:
    return Modem(
        id=modem_id,
        nickname=nickname,
        model=model,
        equipment_type="MODEM",
        manufacturer="Spectrum",
        firmware_version="3.1.0-syn",
        serial_number=serial_number,
        mac_address=mac_address,
        wan_ipv4="203.0.113.10",
        wan_ipv6="2001:db8::20",
        lan_ipv4="192.0.2.2",
        docsis_capability="DOCSIS 3.1",
        connection_state="CONNECTED",
        is_online=is_online,
        supported_features=(
            ["REBOOT", "SPEED_TEST"]
            if supported_features is None
            else supported_features
        ),
        network_id=SYNTHETIC_NETWORK_ID,
        is_combo=is_combo,
    )


def make_combo_router_and_modem(
    *,
    combo_id: str = "combo-1",
    with_subsystems: bool = True,
) -> tuple[Router, Modem]:
    router = make_router(
        router_id=combo_id,
        is_combo=True,
        nickname="Combo Gateway",
        model="CGM4981",
        with_subsystems=with_subsystems,
        mac_address="00:00:5E:00:53:05",
        serial_number="SYN-CMB-0001",
    )
    modem = make_modem(
        modem_id=combo_id,
        is_combo=True,
        nickname="Combo Gateway",
        model="CGM4981",
        mac_address="00:00:5E:00:53:06",
        serial_number="SYN-CMB-0001",
    )
    return router, modem


def make_pod(
    *,
    pod_id: str = "pod-1",
    network_id: str | None = SYNTHETIC_NETWORK_ID,
    nickname: str | None = "Upstairs Pod",
    mac_address: str = "00:00:5E:00:53:03",
    serial_number: str = "SYN-POD-0001",
    coverage_score: str | None = "GOOD",
) -> Pod:
    return Pod(
        id=pod_id,
        nickname=nickname,
        model="SX1V1S",
        equipment_type="POD",
        manufacturer="Spectrum",
        firmware_version="1.2.3-syn",
        serial_number=serial_number,
        mac_address=mac_address,
        lan_ipv4="192.0.2.30",
        ipv6_address="2001:db8::30",
        connected_client_count=3,
        backhaul_connection_type="WIFI",
        backhaul_frequency="5GHZ",
        connection_state="CONNECTED",
        is_online=True,
        coverage_score=coverage_score,
        network_id=network_id,
    )


def make_stb(
    *,
    stb_id: str = "stb-1",
    nickname: str | None = "Living Room STB",
    mac_address: str = "00:00:5E:00:53:04",
    serial_number: str = "SYN-STB-0001",
    is_online: bool | None = True,
) -> SetTopBox:
    return SetTopBox(
        id=stb_id,
        nickname=nickname,
        model="SPECTRUM-210",
        equipment_type="STB",
        manufacturer="Spectrum",
        firmware_version="4.5.0-syn",
        serial_number=serial_number,
        mac_address=mac_address,
        lan_ipv4="192.0.2.40",
        is_worldbox=True,
        connection_state="CONNECTED" if is_online else "DISCONNECTED",
        is_online=is_online,
    )


def make_modem_health(
    *,
    equipment_id: str = "modem-1",
    t4_timeouts_threshold_reached: bool | None = False,
    ethernet_port_speed_healthy: bool | None = True,
    speed_achievable: bool | None = True,
    speed_achievable_status: str | None = "ACHIEVABLE",
    hot_listed_status: str | None = "NOT_HOTLISTED",
    quarantine_status: str | None = "NOT_QUARANTINED",
) -> ModemHealth:
    return ModemHealth(
        equipment_id=equipment_id,
        t4_timeouts_threshold_reached=t4_timeouts_threshold_reached,
        ethernet_port_speed_healthy=ethernet_port_speed_healthy,
        speed_achievable=speed_achievable,
        speed_achievable_status=speed_achievable_status,
        hot_listed_status=hot_listed_status,
        quarantine_status=quarantine_status,
    )


def make_outage(
    *,
    active: bool = True,
    evt_id: str = "EVT-SYN-001",
    credit_eligibility: str | None = "ELIGIBLE",
    credit_enrolled: bool | None = True,
    callback_eligible: bool | None = True,
    message: str = "Fiber maintenance affecting area",
    short_message: str = "Service outage in your area",
) -> ServiceProblem:
    return ServiceProblem(
        evt_id=evt_id,
        pid="PID-SYN-001",
        originating_system="SYN-OSS",
        problem_type="OUTAGE",
        services_affected=["INTERNET", "TV"],
        time_raised="2026-11-26T10:00:00Z",
        estimated_resolution="2026-11-26T16:00:00Z",
        etr_modified=False,
        message=message,
        short_message=short_message,
        status="ACTIVE" if active else "RESOLVED",
        is_callback_eligible=callback_eligible,
        outage_credit_eligibility=credit_eligibility,
        is_outage_credit_enrolled=credit_enrolled,
        is_outage_callback_enrolled=False,
        outage_cause_type="COMMERCIAL_POWER",
        outage_detail="Power loss at node",
    )


def make_appointment(
    *,
    appointment_id: str = "APPT-SYN-001",
    start_time: str = "2026-12-01T14:00:00Z",
    end_time: str = "2026-12-01T15:00:00Z",
    status: str = "SCHEDULED",
    past_end_time: bool = False,
) -> ServiceAppointment:
    return ServiceAppointment(
        appointment_id=appointment_id,
        status=status,
        work_order_type="INSTALL",
        order_class="RESIDENTIAL",
        reason_codes=["NEW_MODEM"],
        start_time=start_time,
        end_time=end_time,
        timezone="UTC",
        held=False,
        past_end_time=past_end_time,
        creation_date_time="2026-11-25T09:00:00Z",
    )


def make_appointment_alert(
    *,
    active: bool = True,
) -> ServiceAppointmentAlert:
    return ServiceAppointmentAlert(
        sro_type="REPAIR" if active else None,
        alert_status="ACTIVE" if active else None,
        appointment_status="CONFIRMED" if active else None,
        appointment_id="APPT-SYN-001" if active else None,
        timezone="UTC" if active else None,
        program_type="TECH_DISPATCH" if active else None,
    )


def make_billing_summary() -> BillingSummary:
    return BillingSummary(
        account_id=SYNTHETIC_ACCOUNT_ID,
        fq_account_id=SYNTHETIC_FQ_ACCOUNT_ID,
        account_status="ACTIVE",
        account_type="RESIDENTIAL",
        biller_account_status="ACTIVE",
        is_seasonal_suspend=False,
        current_balance=89.99,
        current_balance_minus_pending=89.99,
        total_amount_due=89.99,
        past_due_amount=0.0,
        pending_payment_amount=10.0,
        due_date="2026-12-15",
        monetary_status="CURRENT",
        is_delinquent=False,
        delinquency_status="CURRENT",
        autopay_enabled=True,
        autopay_locked=False,
        autopay_suspended=False,
        payment_date_for_autopay="2026-12-14",
        upcoming_autopay_charge_amount=89.99,
        debit_date="2026-12-14",
        last_payment_amount=84.99,
        next_cycle_date="12/20/2026",
        division_id=SYNTHETIC_DIVISION_ID,
        billing_id=SYNTHETIC_BILLING_ID,
        account_number_hash="a1b2c3d4e5f60718293a4b5c6d7e8f90",
    )


def make_account_plan() -> AccountPlan:
    return AccountPlan(
        tv_provisioned=False,
        internet_provisioned=True,
        voice_provisioned=False,
        wifi_provisioned=True,
        secure_connect_provisioned=True,
        mobile_provisioned=False,
        internet_plan_name="Spectrum Internet Gig",
        subscribed_download_mbps=1000.0,
        subscribed_upload_mbps=35.0,
    )


def make_speed_test(
    *,
    equipment_id: str | None = "router-1",
    download_mbps: float = 942.5,
    upload_mbps: float = 38.2,
    latency_ms: float = 11.4,
    jitter_ms: float = 1.8,
    packet_loss_pct: float = 0.0,
    timestamp: str = "2026-11-26T15:30:00Z",
    status: str = "COMPLETED",
) -> SpeedTestResult:
    return SpeedTestResult(
        id="st-syn-001",
        status=status,
        download_mbps=download_mbps,
        upload_mbps=upload_mbps,
        latency_ms=latency_ms,
        jitter_ms=jitter_ms,
        packet_loss_pct=packet_loss_pct,
        timestamp=timestamp,
        server_location="Synthetic-DC-01",
        equipment_id=equipment_id,
    )


def make_wifi_coverage(
    *,
    network_id: str = SYNTHETIC_NETWORK_ID,
    pod_id: str = "pod-1",
) -> WifiCoverage:
    return WifiCoverage(
        network_id=network_id,
        client_count=4,
        pod_count=1,
        client_scores={"client-1": "EXCELLENT"},
        pod_scores={pod_id: "GOOD"},
        overall_historical_score="EXCELLENT",
        historical_pod_scores={pod_id: "GOOD"},
        last_checked_timestamp="2026-11-26T15:00:00Z",
        recommendation="NONE",
    )


def make_threat(
    *,
    client_id: str = "client-1",
    client_mac: str = "00:00:5E:00:53:11",
) -> SecurityThreat:
    return SecurityThreat(
        threat_id="threat-syn-001",
        action="BLOCKED",
        customer_action="NONE",
        description="Synthetic malicious domain blocked",
        client_id=client_id,
        client_mac=client_mac,
        started_at="2026-11-26T14:00:00Z",
        level="HIGH",
        threat_type="MALWARE",
        severity="HIGH",
        threat_url="http://malware.example.invalid/payload",
        remote_port=4444,
        remote_ip="203.0.113.99",
    )


def make_clients(
    *,
    router_id: str = "router-1",
    network_id: str = SYNTHETIC_NETWORK_ID,
) -> tuple[list[Client], list[IpReservation], list[PortForward], list[ClientGroup]]:
    pf1 = PortForward(
        id="pf-1",
        name="Web Server",
        protocol="TCP",
        external_port=8443,
        internal_port=443,
        ip_address="192.0.2.101",
        mac_address="00:00:5E:00:53:11",
        reservation_id="res-1",
        client_id="client-1",
        client_name="Synthetic Server",
        enabled=True,
    )
    res1 = IpReservation(
        id="res-1",
        mac_address="00:00:5E:00:53:11",
        ip_address="192.0.2.101",
        router_id=router_id,
        client_id="client-1",
        client_name="Synthetic Server",
        port_forwards=[pf1],
    )
    res3 = IpReservation(
        id="res-3",
        mac_address="00:00:5E:00:53:13",
        ip_address="192.0.2.103",
        router_id=router_id,
        client_id="client-3",
        client_name="Synthetic Printer",
        port_forwards=[],
    )
    c1 = Client(
        id="client-1",
        mac_address="00:00:5E:00:53:11",
        router_id=router_id,
        network_id=network_id,
        display_name="Synthetic Server",
        host_name="syn-server.local",
        nickname="Main Server",
        connected=True,
        connection_type="ETHERNET",
        ssid="SyntheticWiFi-Main",
        wifi_radio_frequency="5GHZ",
        signal_strength=-42,
        ipv4_addresses=["192.0.2.101"],
        ipv6_addresses=["2001:db8::101"],
        ip_reservation=res1,
        paused=False,
        group_id="grp-1",
        group_name="Lab Group",
        group_ids=["grp-1"],
    )
    c2 = Client(
        id="client-2",
        mac_address="00:00:5E:00:53:12",
        router_id=router_id,
        network_id=network_id,
        display_name="Synthetic Guest Phone",
        host_name="syn-phone.local",
        connected=False,
        connection_type="WIFI",
        ipv4_addresses=["192.0.2.102"],
        ip_reservation=None,
        paused=False,
    )
    c3 = Client(
        id="client-3",
        mac_address="00:00:5E:00:53:13",
        router_id=router_id,
        network_id=network_id,
        display_name="Synthetic Printer",
        host_name="syn-printer.local",
        connected=False,
        connection_type="WIFI",
        ipv4_addresses=["192.0.2.103"],
        ip_reservation=res3,
        paused=False,
    )
    c4 = Client(
        id="client-4",
        mac_address="00:00:5E:00:53:14",
        router_id=router_id,
        network_id=network_id,
        display_name="Synthetic Tablet",
        host_name="syn-tablet.local",
        connected=True,
        connection_type="WIFI",
        ipv4_addresses=["192.0.2.104"],
        ip_reservation=None,
        paused=True,
        pause_until="2026-11-27T08:00:00Z",
        group_id="grp-2",
        group_name="Kids Group",
        group_ids=["grp-2"],
        access_schedules=[
            AccessSchedule(
                id="sched-1",
                name="Bedtime Schedule",
                enabled=True,
                days=["MONDAY", "TUESDAY"],
                start_time="21:00",
                end_time="07:00",
            )
        ],
    )
    g1 = ClientGroup(
        id="grp-1",
        name="Lab Group",
        router_id=router_id,
        client_count=1,
        client_ids=["client-1"],
        client_macs=["00:00:5E:00:53:11"],
        paused=False,
    )
    g2 = ClientGroup(
        id="grp-2",
        name="Kids Group",
        router_id=router_id,
        client_count=1,
        client_ids=["client-4"],
        client_macs=["00:00:5E:00:53:14"],
        paused=True,
        pause_reason="BEDTIME",
        pause_until="2026-11-27T08:00:00Z",
    )
    return [c1, c2, c3, c4], [res1, res3], [pf1], [g1, g2]


def make_equipment_state(
    *,
    combo: bool = False,
    with_subsystems: bool = True,
    include_pod: bool = True,
    include_stb: bool = True,
    include_modem_health: bool = True,
    include_speed_test: bool = True,
) -> EquipmentState:
    if combo:
        router, modem = make_combo_router_and_modem(with_subsystems=with_subsystems)
    else:
        router = make_router(with_subsystems=with_subsystems)
        modem = make_modem()
    pods = [make_pod()] if include_pod else []
    stbs = [make_stb()] if include_stb else []
    health = (
        {modem.id: make_modem_health(equipment_id=modem.id)}
        if include_modem_health
        else {}
    )
    speed_history = (
        [make_speed_test(equipment_id=router.id)] if include_speed_test else []
    )
    conn_states = {
        router.id: EquipmentConnectionState(
            equipment_id=router.id, status="CONNECTED", is_online=True
        ),
        modem.id: EquipmentConnectionState(
            equipment_id=modem.id, status="CONNECTED", is_online=True
        ),
    }
    return EquipmentState(
        routers=[router],
        modems=[modem],
        pods=pods,
        set_top_boxes=stbs,
        connection_states=conn_states,
        modem_health=health,
        speed_test_history=speed_history,
    )


def make_network_state(
    *,
    router_id: str = "router-1",
    with_threats: bool = True,
    with_coverage: bool = True,
    with_shield: bool = True,
) -> NetworkState:
    clients, reservations, port_forwards, groups = make_clients(router_id=router_id)
    threats = [make_threat()] if with_threats else []
    shields = (
        {
            SYNTHETIC_NETWORK_ID: SecurityShield(
                enabled=True,
                listen_only=False,
                status="ENABLED",
                network_id=SYNTHETIC_NETWORK_ID,
                threats=threats,
            )
        }
        if with_shield
        else {}
    )
    coverage = {SYNTHETIC_NETWORK_ID: make_wifi_coverage()} if with_coverage else {}
    return NetworkState(
        network_ids=[SYNTHETIC_NETWORK_ID],
        security_shields=shields,
        threats=threats,
        clients=clients,
        client_groups=groups,
        ip_reservations=reservations,
        port_forwards=port_forwards,
        wifi_coverage=coverage,
    )


def make_account_state(
    *,
    with_billing: bool = True,
) -> AccountState:
    return AccountState(
        unique_id=derive_account_unique_id(make_jwt()),
        billing_summary=make_billing_summary() if with_billing else None,
        plan=make_account_plan(),
    )


def make_appointments_payload(
    *,
    include_appointment: bool = True,
    include_alert: bool = True,
) -> dict[str, Any]:
    return {
        "serviceAppointments": {
            "alert": (
                {
                    "sroType": "REPAIR",
                    "alertStatus": "ACTIVE",
                    "appointmentStatus": "CONFIRMED",
                    "appointmentId": "APPT-SYN-001",
                    "serviceAddressTimeZone": "UTC",
                    "programType": "TECH_DISPATCH",
                }
                if include_alert
                else None
            ),
            "appointments": (
                [
                    {
                        "apptId": "APPT-SYN-001",
                        "status": "SCHEDULED",
                        "workOrderType": "INSTALL",
                        "orderClass": "RESIDENTIAL",
                        "reasonCodes": ["NEW_MODEM"],
                        "schedule": {
                            "startTime": "2026-12-01T14:00:00Z",
                            "endTime": "2026-12-01T15:00:00Z",
                            "timezone": "UTC",
                        },
                        "held": False,
                        "pastEndTime": False,
                        "creationDateTime": "2026-11-25T09:00:00Z",
                    }
                ]
                if include_appointment
                else []
            ),
        }
    }


def make_mock_client(
    *,
    equipment_state: EquipmentState | None = None,
    network_state: NetworkState | None = None,
    account_state: AccountState | None = None,
    outages: list[ServiceProblem] | None = None,
    appointments_payload: dict[str, Any] | None = None,
) -> MagicMock:
    eq = equipment_state if equipment_state is not None else make_equipment_state()
    router_id = eq.routers[0].id if eq.routers else "router-1"
    net = (
        network_state
        if network_state is not None
        else make_network_state(router_id=router_id)
    )
    acct = account_state if account_state is not None else make_account_state()
    outage_list = outages if outages is not None else [make_outage(active=True)]
    appt_raw = (
        appointments_payload
        if appointments_payload is not None
        else make_appointments_payload()
    )

    client = MagicMock()
    client.tokens = make_token_set()
    client.async_get_equipment_state = AsyncMock(return_value=eq)
    client.async_get_network_state = AsyncMock(return_value=net)
    client.async_get_account_state = AsyncMock(return_value=acct)
    client.async_get_outages = AsyncMock(return_value=outage_list)
    client.execute_operation = AsyncMock(return_value=appt_raw)

    client.async_set_security_shield = AsyncMock(return_value=None)
    client.async_set_upnp = AsyncMock(return_value=None)
    client.async_set_hotspot = AsyncMock(return_value=None)
    client.async_set_thread_radio = AsyncMock(return_value=None)
    client.async_set_group_pause = AsyncMock(return_value=None)
    client.async_set_client_pause = AsyncMock(return_value=None)
    client.async_reboot_equipment = AsyncMock(return_value=None)
    client.async_initiate_speed_test = AsyncMock(
        return_value=make_speed_test(equipment_id=router_id)
    )
    client.async_run_speed_test = AsyncMock(
        return_value=make_speed_test(equipment_id=router_id)
    )
    client.async_smart_steer = AsyncMock(return_value=None)
    client.async_update_pod_led = AsyncMock(return_value={"mode": "LOCATE"})

    pf = PortForward(
        id="pf-new",
        name="Synthetic PF",
        protocol="TCP",
        external_port=8080,
        internal_port=80,
        ip_address="192.0.2.101",
        mac_address="00:00:5E:00:53:11",
    )
    res = IpReservation(
        id="res-new",
        mac_address="00:00:5E:00:53:11",
        ip_address="192.0.2.101",
        router_id=router_id,
    )
    client.async_add_port_forward = AsyncMock(
        return_value=PortForwardCreateResult(
            port_forward=pf,
            ip_reservation=res,
            auto_reserved=True,
        )
    )
    client.async_update_port_forward = AsyncMock(return_value=pf)
    client.async_delete_port_forward = AsyncMock(return_value=None)
    client.async_list_port_forwards = AsyncMock(return_value=[pf])
    client.async_set_ip_reservation = AsyncMock(return_value=res)
    client.async_delete_ip_reservation = AsyncMock(return_value=None)
    client.async_set_group_schedule = AsyncMock(
        return_value=AccessSchedule(
            id="sched-grp-1",
            name="Weeknights",
            enabled=True,
            days=["MONDAY"],
            start_time="21:00",
            end_time="07:00",
        )
    )
    client.async_set_client_schedule = AsyncMock(
        return_value=AccessSchedule(
            id="sched-cli-1",
            name="Study Time",
            enabled=True,
            days=["TUESDAY"],
            start_time="18:00",
            end_time="20:00",
        )
    )
    client.async_set_dns = AsyncMock(
        return_value=DnsConfig(
            mode="CUSTOM",
            primary="192.0.2.53",
            secondary="198.51.100.53",
            servers=["192.0.2.53", "198.51.100.53"],
            is_custom=True,
        )
    )
    client.async_update_wifi = AsyncMock(return_value=None)
    client.async_create_additional_network = AsyncMock(
        return_value=AdditionalNetwork(
            id="add-new",
            name="SynGuestNew",
            password="SynGuestPassword!2",
            broadcast=True,
            enabled=True,
        )
    )
    client.async_update_additional_network = AsyncMock(
        return_value=AdditionalNetwork(
            id="add-1",
            name="SynGuestUpdated",
            password="SynGuestPassword!3",
            broadcast=False,
            enabled=True,
        )
    )
    client.async_delete_additional_network = AsyncMock(return_value=None)
    client.async_rename_client = AsyncMock(return_value=None)
    client.async_remove_client = AsyncMock(return_value=None)
    client.async_create_client_group = AsyncMock(
        return_value=ClientGroup(
            id="grp-new",
            name="New Group",
            router_id=router_id,
            client_count=1,
            client_ids=["client-1"],
        )
    )
    client.async_update_client_group = AsyncMock(
        return_value=ClientGroup(
            id="grp-1",
            name="Renamed Group",
            router_id=router_id,
            client_count=1,
            client_ids=["client-1"],
        )
    )
    client.async_delete_client_group = AsyncMock(return_value=None)
    return client


@contextmanager
def patch_spectrum_client(mock_client: MagicMock):
    captured: dict[str, Any] = {}

    def _factory(*args: Any, **kwargs: Any) -> MagicMock:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return mock_client

    with patch("custom_components.spectrum.SpectrumClient", side_effect=_factory):
        yield captured


async def setup_integration(
    hass: HomeAssistant,
    *,
    entry: MockConfigEntry | None = None,
    mock_client: MagicMock | None = None,
    options: dict[str, Any] | None = None,
    combo: bool = False,
    with_subsystems: bool = True,
) -> tuple[MockConfigEntry, MagicMock, dict[str, Any]]:
    config_entry = entry or make_config_entry(hass, options=options)
    if mock_client is None:
        eq = make_equipment_state(combo=combo, with_subsystems=with_subsystems)
        router_id = eq.routers[0].id
        net = make_network_state(
            router_id=router_id,
            with_threats=with_subsystems,
            with_coverage=with_subsystems,
            with_shield=with_subsystems,
        )
        client = make_mock_client(equipment_state=eq, network_state=net)
    else:
        client = mock_client

    with patch_spectrum_client(client) as captured:
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    return config_entry, client, captured
