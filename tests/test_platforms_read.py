from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest
from aiospectrum import (
    CellularBackup,
    Client,
    ModemHealth,
    SecurityThreat,
    SetTopBox,
    SpeedTestResult,
    ThreadNetworkConfig,
    WifiNetwork,
)
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory, Platform, UnitOfDataRate, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.spectrum.binary_sensor import (
    _is_outage_credit_eligible,
    _parse_problem_status,
    _parse_speed_achievable,
)
from custom_components.spectrum.const import (
    CLIENT_TRACKER_MODE_ALL,
    CLIENT_TRACKER_MODE_CONNECTED_OR_RESERVED,
    CLIENT_TRACKER_MODE_DISABLED,
    DOMAIN,
    OPT_CLIENT_TRACKER_MODE,
)
from custom_components.spectrum.entity import equipment_device_identifier
from custom_components.spectrum.image import build_wifi_qr_payload
from custom_components.spectrum.sensor import (
    _parse_date,
    _parse_timestamp,
    _truncate_state,
)

from .conftest import (
    make_account_state,
    make_appointments_payload,
    make_equipment_state,
    make_mock_client,
    make_modem,
    make_modem_health,
    make_network_state,
    make_outage,
    make_pod,
    make_router,
    make_stb,
    patch_spectrum_client,
    setup_integration,
)


def _entity_by_unique_id(
    hass: HomeAssistant, platform: Platform, unique_id: str
) -> er.RegistryEntry | None:
    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id(platform, DOMAIN, unique_id)
    if entity_id is None:
        return None
    return ent_reg.async_get(entity_id)


async def test_full_read_platforms_account_router_modem_pod_stb(
    hass: HomeAssistant,
) -> None:
    entry, _client, _ = await setup_integration(hass)
    eid = entry.entry_id

    expected_binary = {
        f"{eid}:account:active_outage": ("on", BinarySensorDeviceClass.PROBLEM),
        f"{eid}:account:outage_credit_eligible": ("on", None),
        f"{eid}:account:outage_credit_enrolled": ("on", None),
        f"{eid}:account:outage_callback_eligible": ("on", None),
        f"{eid}:account:service_appointment_alert": (
            "on",
            BinarySensorDeviceClass.PROBLEM,
        ),
        f"{eid}:account:autopay": ("on", None),
        f"{eid}:account:delinquent": ("off", BinarySensorDeviceClass.PROBLEM),
        f"{eid}:account:seasonal_suspend": ("off", None),
        f"{eid}:router:router-1:connectivity": (
            "on",
            BinarySensorDeviceClass.CONNECTIVITY,
        ),
        f"{eid}:router:router-1:security_shield_threats_active": (
            "on",
            BinarySensorDeviceClass.SAFETY,
        ),
        f"{eid}:router:router-1:additional_network_active": (
            "on",
            BinarySensorDeviceClass.CONNECTIVITY,
        ),
        f"{eid}:router:router-1:cellular_backup_ready": (
            "on",
            BinarySensorDeviceClass.CONNECTIVITY,
        ),
        f"{eid}:router:router-1:cellular_backup_in_use": (
            "off",
            BinarySensorDeviceClass.RUNNING,
        ),
        f"{eid}:modem:modem-1:connectivity": (
            "on",
            BinarySensorDeviceClass.CONNECTIVITY,
        ),
        f"{eid}:modem:modem-1:t4_timeout_threshold": (
            "off",
            BinarySensorDeviceClass.PROBLEM,
        ),
        f"{eid}:modem:modem-1:ethernet_port_speed_healthy": (
            "on",
            BinarySensorDeviceClass.CONNECTIVITY,
        ),
        f"{eid}:modem:modem-1:speed_achievable": ("on", None),
        f"{eid}:modem:modem-1:hot_listed": ("off", BinarySensorDeviceClass.PROBLEM),
        f"{eid}:modem:modem-1:quarantined": ("off", BinarySensorDeviceClass.PROBLEM),
        f"{eid}:pod:pod-1:connectivity": ("on", BinarySensorDeviceClass.CONNECTIVITY),
        f"{eid}:stb:stb-1:connectivity": ("on", BinarySensorDeviceClass.CONNECTIVITY),
    }

    for uid, (expected_state, expected_dc) in expected_binary.items():
        reg = _entity_by_unique_id(hass, Platform.BINARY_SENSOR, uid)
        assert reg is not None, f"Missing binary_sensor {uid}"
        state = hass.states.get(reg.entity_id)
        assert state is not None
        assert state.state == expected_state, f"{uid} expected {expected_state}"
        if expected_dc is not None:
            assert state.attributes.get("device_class") == expected_dc

    outage_reg = _entity_by_unique_id(
        hass, Platform.BINARY_SENSOR, f"{eid}:account:active_outage"
    )
    assert outage_reg is not None
    outage_state = hass.states.get(outage_reg.entity_id)
    assert outage_state is not None
    assert outage_state.attributes["outage_count"] == 1
    assert outage_state.attributes["event_ids"] == ["EVT-SYN-001"]

    expected_sensors = {
        f"{eid}:account:outage_count": "1",
        f"{eid}:account:outage_status": "ACTIVE",
        f"{eid}:account:outage_message": "Service outage in your area",
        f"{eid}:account:outage_services_affected": "INTERNET, TV",
        f"{eid}:account:outage_estimated_resolution": "2026-11-26T16:00:00Z",
        f"{eid}:account:outage_cause": "COMMERCIAL_POWER",
        f"{eid}:account:service_appointment_status": "SCHEDULED",
        f"{eid}:account:service_appointment_count": "1",
        f"{eid}:account:current_balance": "89.99",
        f"{eid}:account:amount_due": "89.99",
        f"{eid}:account:past_due_amount": "0.0",
        f"{eid}:account:pending_payment": "10.0",
        f"{eid}:account:last_payment": "84.99",
        f"{eid}:account:upcoming_autopay_charge": "89.99",
        f"{eid}:account:payment_due_date": "2026-12-15",
        f"{eid}:account:next_autopay_date": "2026-12-14",
        f"{eid}:account:next_billing_cycle": "2026-12-20",
        f"{eid}:account:account_status": "ACTIVE",
        f"{eid}:account:monetary_status": "CURRENT",
        f"{eid}:account:internet_plan": "Spectrum Internet Gig",
        f"{eid}:account:subscribed_download_speed": "1000.0",
        f"{eid}:account:subscribed_upload_speed": "35.0",
        f"{eid}:account:provisioned_services": "internet, wifi, secure_connect",
        f"{eid}:router:router-1:connected_clients": "2",
        f"{eid}:router:router-1:total_clients": "4",
        f"{eid}:router:router-1:paused_clients": "1",
        f"{eid}:router:router-1:ip_reservation_count": "2",
        f"{eid}:router:router-1:port_forward_count": "1",
        f"{eid}:router:router-1:security_shield_threats": "1",
        f"{eid}:router:router-1:wifi_coverage_score": "EXCELLENT",
        f"{eid}:router:router-1:wan_ipv4": "198.51.100.10",
        f"{eid}:router:router-1:wan_ipv6": "2001:db8::10",
        f"{eid}:router:router-1:lan_ipv4": "192.0.2.1",
        f"{eid}:router:router-1:dns_mode": "CUSTOM",
        f"{eid}:router:router-1:primary_dns": "192.0.2.53",
        f"{eid}:router:router-1:secondary_dns": "198.51.100.53",
        f"{eid}:router:router-1:wifi_network_name": "SyntheticWiFi-Main",
        f"{eid}:router:router-1:additional_network_name": "SyntheticWiFi-Guest",
        f"{eid}:router:router-1:connection_state": "CONNECTED",
        f"{eid}:router:router-1:speed_test_download": "942.5",
        f"{eid}:router:router-1:speed_test_upload": "38.2",
        f"{eid}:router:router-1:speed_test_latency": "11.4",
        f"{eid}:router:router-1:speed_test_jitter": "1.8",
        f"{eid}:router:router-1:speed_test_packet_loss": "0.0",
        f"{eid}:router:router-1:speed_test_status": "COMPLETED",
        f"{eid}:router:router-1:cellular_backup_signal": "4",
        f"{eid}:router:router-1:cellular_backup_usage": "1.5",
        f"{eid}:router:router-1:cellular_backup_status": "STANDBY",
        f"{eid}:router:router-1:thread_channel": "15",
        f"{eid}:modem:modem-1:modem_connection_state": "CONNECTED",
        f"{eid}:modem:modem-1:docsis_capability": "DOCSIS 3.1",
        f"{eid}:pod:pod-1:pod_connected_clients": "3",
        f"{eid}:pod:pod-1:pod_backhaul_type": "WIFI",
        f"{eid}:pod:pod-1:pod_backhaul_frequency": "5GHZ",
        f"{eid}:pod:pod-1:pod_coverage_score": "GOOD",
    }

    for uid, expected_val in expected_sensors.items():
        reg = _entity_by_unique_id(hass, Platform.SENSOR, uid)
        assert reg is not None, f"Missing sensor {uid}"
        state = hass.states.get(reg.entity_id)
        assert state is not None
        assert state.state == expected_val, f"{uid}: {state.state} != {expected_val}"

    bal_reg = _entity_by_unique_id(
        hass, Platform.SENSOR, f"{eid}:account:current_balance"
    )
    assert bal_reg is not None
    bal_state = hass.states.get(bal_reg.entity_id)
    assert bal_state is not None
    assert bal_state.attributes["unit_of_measurement"] == "USD"
    assert bal_state.attributes["device_class"] == SensorDeviceClass.MONETARY

    dl_reg = _entity_by_unique_id(
        hass, Platform.SENSOR, f"{eid}:router:router-1:speed_test_download"
    )
    assert dl_reg is not None
    dl_state = hass.states.get(dl_reg.entity_id)
    assert dl_state is not None
    assert (
        dl_state.attributes["unit_of_measurement"] == UnitOfDataRate.MEGABITS_PER_SECOND
    )

    lat_reg = _entity_by_unique_id(
        hass, Platform.SENSOR, f"{eid}:router:router-1:speed_test_latency"
    )
    assert lat_reg is not None
    lat_state = hass.states.get(lat_reg.entity_id)
    assert lat_state is not None
    assert lat_state.attributes["unit_of_measurement"] == UnitOfTime.MILLISECONDS

    usage_reg = _entity_by_unique_id(
        hass, Platform.SENSOR, f"{eid}:router:router-1:cellular_backup_usage"
    )
    assert usage_reg is not None
    usage_state = hass.states.get(usage_reg.entity_id)
    assert usage_state is not None
    assert usage_state.attributes["unit_of_measurement"] == "GB"
    assert usage_state.attributes["limit"] == 10.0

    appt_reg = _entity_by_unique_id(
        hass, Platform.SENSOR, f"{eid}:account:next_service_appointment"
    )
    assert appt_reg is not None
    appt_state = hass.states.get(appt_reg.entity_id)
    assert appt_state is not None
    assert appt_state.attributes["device_class"] == SensorDeviceClass.TIMESTAMP


async def test_password_sensors_disabled_by_default_and_value_when_enabled(
    hass: HomeAssistant,
) -> None:
    entry, client, _ = await setup_integration(hass)
    ent_reg = er.async_get(hass)
    eid = entry.entry_id

    for key in (
        "wifi_password",
        "additional_network_password",
        "firmware_version",
    ):
        uid = f"{eid}:router:router-1:{key}"
        reg = _entity_by_unique_id(hass, Platform.SENSOR, uid)
        assert reg is not None
        assert reg.entity_category is EntityCategory.DIAGNOSTIC
        assert reg.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert hass.states.get(reg.entity_id) is None

        ent_reg.async_update_entity(reg.entity_id, disabled_by=None)

    with patch_spectrum_client(client):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    for key, expected_secret in (
        ("wifi_password", "SynMainPassword!42"),
        ("additional_network_password", "SynGuestPassword!99"),
        ("firmware_version", "2.4.1-syn"),
    ):
        reg = _entity_by_unique_id(
            hass, Platform.SENSOR, f"{eid}:router:router-1:{key}"
        )
        assert reg is not None
        state = hass.states.get(reg.entity_id)
        assert state is not None
        assert state.state == expected_secret


async def test_combo_router_modem_shares_single_ha_device(
    hass: HomeAssistant,
) -> None:
    entry, _client, _ = await setup_integration(hass, combo=True)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    eid = entry.entry_id

    devices = dr.async_entries_for_config_entry(dev_reg, eid)
    combo_device = dev_reg.async_get_device_by_identifier(
        equipment_device_identifier(eid, "combo-1"), eid
    )
    assert combo_device is not None
    assert len(devices) == 4

    router_conn = _entity_by_unique_id(
        hass, Platform.BINARY_SENSOR, f"{eid}:router:combo-1:connectivity"
    )
    modem_conn = _entity_by_unique_id(
        hass, Platform.BINARY_SENSOR, f"{eid}:modem:combo-1:connectivity"
    )
    modem_docsis = _entity_by_unique_id(
        hass, Platform.SENSOR, f"{eid}:modem:combo-1:docsis_capability"
    )
    assert router_conn is not None
    assert modem_conn is not None
    assert modem_docsis is not None
    assert modem_conn.translation_key == "modem_connectivity"
    assert router_conn.device_id == combo_device.id
    assert modem_conn.device_id == combo_device.id
    assert modem_docsis.device_id == combo_device.id

    all_combo_entities = er.async_entries_for_device(ent_reg, combo_device.id)
    assert any(
        e.unique_id == f"{eid}:router:combo-1:wan_ipv4" for e in all_combo_entities
    )
    assert any(
        e.unique_id == f"{eid}:modem:combo-1:docsis_capability"
        for e in all_combo_entities
    )


async def test_supported_only_creation_omits_absent_subsystems(
    hass: HomeAssistant,
) -> None:
    eq = make_equipment_state(
        with_subsystems=False,
        include_pod=False,
        include_stb=False,
        include_modem_health=False,
        include_speed_test=False,
    )
    net = make_network_state(
        router_id="router-1",
        with_threats=False,
        with_coverage=False,
        with_shield=False,
    )
    acct = make_account_state(with_billing=False)
    client = make_mock_client(
        equipment_state=eq,
        network_state=net,
        account_state=acct,
        outages=[make_outage(active=False)],
        appointments_payload=make_appointments_payload(
            include_appointment=False, include_alert=False
        ),
    )
    entry, _, _ = await setup_integration(hass, mock_client=client)
    eid = entry.entry_id

    for absent_binary in (
        f"{eid}:router:router-1:security_shield_threats_active",
        f"{eid}:router:router-1:additional_network_active",
        f"{eid}:router:router-1:cellular_backup_ready",
        f"{eid}:router:router-1:cellular_backup_in_use",
        f"{eid}:modem:modem-1:t4_timeout_threshold",
        f"{eid}:modem:modem-1:hot_listed",
    ):
        assert _entity_by_unique_id(hass, Platform.BINARY_SENSOR, absent_binary) is None

    for absent_sensor in (
        f"{eid}:router:router-1:security_shield_threats",
        f"{eid}:router:router-1:wifi_coverage_score",
        f"{eid}:router:router-1:dns_mode",
        f"{eid}:router:router-1:wifi_network_name",
        f"{eid}:router:router-1:wifi_password",
        f"{eid}:router:router-1:additional_network_name",
        f"{eid}:router:router-1:additional_network_password",
        f"{eid}:router:router-1:speed_test_download",
        f"{eid}:router:router-1:cellular_backup_signal",
        f"{eid}:router:router-1:thread_channel",
    ):
        assert _entity_by_unique_id(hass, Platform.SENSOR, absent_sensor) is None

    assert (
        _entity_by_unique_id(hass, Platform.IMAGE, f"{eid}:router:router-1:wifi_qr")
        is None
    )
    assert (
        _entity_by_unique_id(
            hass, Platform.IMAGE, f"{eid}:router:router-1:additional_network_qr"
        )
        is None
    )


async def test_dynamic_equipment_discovery_adds_entities(hass: HomeAssistant) -> None:
    eq = make_equipment_state(include_pod=False, include_stb=False)
    client = make_mock_client(equipment_state=eq)
    entry, _, _ = await setup_integration(hass, mock_client=client)
    eid = entry.entry_id

    assert (
        _entity_by_unique_id(
            hass, Platform.BINARY_SENSOR, f"{eid}:pod:pod-2:connectivity"
        )
        is None
    )
    assert (
        _entity_by_unique_id(
            hass, Platform.BINARY_SENSOR, f"{eid}:stb:stb-2:connectivity"
        )
        is None
    )

    eq.pods.append(make_pod(pod_id="pod-2"))
    eq.set_top_boxes.append(make_stb(stb_id="stb-2", is_online=False))
    await entry.runtime_data.equipment.async_refresh()
    await hass.async_block_till_done()

    pod_reg = _entity_by_unique_id(
        hass, Platform.BINARY_SENSOR, f"{eid}:pod:pod-2:connectivity"
    )
    stb_reg = _entity_by_unique_id(
        hass, Platform.BINARY_SENSOR, f"{eid}:stb:stb-2:connectivity"
    )
    assert pod_reg is not None
    assert stb_reg is not None
    assert hass.states.get(stb_reg.entity_id).state == "off"
    assert (
        _entity_by_unique_id(
            hass, Platform.SENSOR, f"{eid}:pod:pod-2:pod_connected_clients"
        )
        is not None
    )


async def test_image_qr_disabled_default_png_caching_and_refresh(
    hass: HomeAssistant,
) -> None:
    entry, client, _ = await setup_integration(hass)
    ent_reg = er.async_get(hass)
    eid = entry.entry_id

    wifi_qr_uid = f"{eid}:router:router-1:wifi_qr"
    add_qr_uid = f"{eid}:router:router-1:additional_network_qr"

    wifi_reg = _entity_by_unique_id(hass, Platform.IMAGE, wifi_qr_uid)
    add_reg = _entity_by_unique_id(hass, Platform.IMAGE, add_qr_uid)
    assert wifi_reg is not None
    assert add_reg is not None
    assert wifi_reg.entity_category is EntityCategory.DIAGNOSTIC
    assert wifi_reg.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert add_reg.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    ent_reg.async_update_entity(wifi_reg.entity_id, disabled_by=None)
    ent_reg.async_update_entity(add_reg.entity_id, disabled_by=None)

    with patch_spectrum_client(client):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    image_component = hass.data["entity_components"]["image"]
    wifi_entity = image_component.get_entity(wifi_reg.entity_id)
    add_entity = image_component.get_entity(add_reg.entity_id)
    assert wifi_entity is not None
    assert add_entity is not None
    assert wifi_entity.content_type == "image/png"

    with patch(
        "custom_components.spectrum.image._render_qr_png",
        wraps=__import__(
            "custom_components.spectrum.image", fromlist=["_render_qr_png"]
        )._render_qr_png,
    ) as render_spy:
        png_1 = await wifi_entity.async_image()
        png_2 = await wifi_entity.async_image()
        add_png = await add_entity.async_image()

    assert png_1 is not None
    assert png_1.startswith(b"\x89PNG\r\n\x1a\n")
    assert png_1 == png_2
    assert add_png is not None
    assert add_png.startswith(b"\x89PNG\r\n\x1a\n")
    assert render_spy.call_count == 2

    prev_updated = wifi_entity.image_last_updated
    eq = entry.runtime_data.equipment.data.equipment
    eq.routers[0].wifi_networks[0] = WifiNetwork(
        id="ap-primary-1",
        name="SyntheticWiFi-Rotated",
        password="RotatedPassword!99",
        security_mode="WPA3",
        is_primary=True,
    )
    await entry.runtime_data.equipment.async_refresh()
    await hass.async_block_till_done()

    assert wifi_entity.image_last_updated is not None
    assert prev_updated is not None
    assert wifi_entity.image_last_updated >= prev_updated
    png_3 = await wifi_entity.async_image()
    assert png_3 is not None
    assert png_3.startswith(b"\x89PNG\r\n\x1a\n")
    assert png_3 != png_1


def test_wifi_qr_payload_escaping_and_security_modes() -> None:
    payload = build_wifi_qr_payload(
        r'My;Home:WiFi,"Net"\1',
        r"P@ss;word:,\"123",
        "WPA3",
    )
    assert (
        payload == r"WIFI:T:WPA;S:My\;Home\:WiFi\,\"Net\"\\1;P:P@ss\;word\:\,\\\"123;;"
    )
    assert build_wifi_qr_payload("OpenNet", "", "NONE") == "WIFI:T:nopass;S:OpenNet;;"
    assert (
        build_wifi_qr_payload("OldNet", "12345", "WEP-64")
        == "WIFI:T:WEP;S:OldNet;P:12345;;"
    )


@pytest.mark.parametrize(
    ("mode", "expected_enabled_macs"),
    [
        (
            CLIENT_TRACKER_MODE_CONNECTED_OR_RESERVED,
            {"00005e005311", "00005e005313", "00005e005314"},
        ),
        (
            CLIENT_TRACKER_MODE_ALL,
            {"00005e005311", "00005e005312", "00005e005313", "00005e005314"},
        ),
        (CLIENT_TRACKER_MODE_DISABLED, set()),
    ],
)
async def test_device_tracker_modes_attachment_and_no_per_client_devices(
    hass: HomeAssistant,
    mode: str,
    expected_enabled_macs: set[str],
) -> None:
    entry, _client, _ = await setup_integration(
        hass,
        options={OPT_CLIENT_TRACKER_MODE: mode},
    )
    eid = entry.entry_id
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    devices = dr.async_entries_for_config_entry(dev_reg, eid)
    assert len(devices) == 5
    router_device = dev_reg.async_get_device_by_identifier(
        equipment_device_identifier(eid, "router-1"), eid
    )
    assert router_device is not None

    trackers = [
        e
        for e in er.async_entries_for_config_entry(ent_reg, eid)
        if e.domain == Platform.DEVICE_TRACKER
    ]
    assert len(trackers) == 4
    for reg in trackers:
        assert reg.device_id == router_device.id
        mac_key = reg.unique_id.rsplit(":", 1)[-1]
        if mac_key in expected_enabled_macs:
            assert reg.disabled_by is None
        else:
            assert reg.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    if mode == CLIENT_TRACKER_MODE_CONNECTED_OR_RESERVED:
        srv_reg = _entity_by_unique_id(
            hass, Platform.DEVICE_TRACKER, f"{eid}:client_tracker:router-1:00005e005311"
        )
        prt_reg = _entity_by_unique_id(
            hass, Platform.DEVICE_TRACKER, f"{eid}:client_tracker:router-1:00005e005313"
        )
        assert srv_reg is not None and prt_reg is not None
        srv_state = hass.states.get(srv_reg.entity_id)
        prt_state = hass.states.get(prt_reg.entity_id)
        assert srv_state is not None and prt_state is not None
        assert srv_state.state == "home"
        assert srv_state.attributes["source_type"] == "router"
        assert srv_state.attributes["ip"] == "192.0.2.101"
        assert srv_state.attributes["host_name"] == "syn-server.local"
        assert srv_state.attributes["reserved_ip"] == "192.0.2.101"
        assert "Lab Group" in srv_state.attributes["group_names"]
        assert prt_state.state == "not_home"


async def test_device_tracker_dynamic_add_disconnect_and_stale_removal(
    hass: HomeAssistant,
) -> None:
    entry, _client, _ = await setup_integration(hass)
    eid = entry.entry_id
    net = entry.runtime_data.network.data

    net.clients.append(
        Client(
            id="client-5",
            mac_address="00:00:5E:00:53:15",
            router_id="router-1",
            display_name="Synthetic Laptop",
            host_name="syn-laptop.local",
            connected=True,
            ipv4_addresses=["192.0.2.105"],
        )
    )
    await entry.runtime_data.network.async_refresh()
    await hass.async_block_till_done()

    new_uid = f"{eid}:client_tracker:router-1:00005e005315"
    new_reg = _entity_by_unique_id(hass, Platform.DEVICE_TRACKER, new_uid)
    assert new_reg is not None
    assert hass.states.get(new_reg.entity_id).state == "home"

    net.clients[-1].connected = False
    await entry.runtime_data.network.async_refresh()
    await hass.async_block_till_done()
    assert _entity_by_unique_id(hass, Platform.DEVICE_TRACKER, new_uid) is not None
    assert hass.states.get(new_reg.entity_id).state == "not_home"

    net.clients = [c for c in net.clients if c.id != "client-5"]
    await entry.runtime_data.network.async_refresh()
    await hass.async_block_till_done()
    assert _entity_by_unique_id(hass, Platform.DEVICE_TRACKER, new_uid) is None


def test_read_platform_parsing_helpers() -> None:
    assert _is_outage_credit_eligible(None) is False
    assert _is_outage_credit_eligible("NOT_ELIGIBLE") is False
    assert _is_outage_credit_eligible("INELIGIBLE") is False
    assert _is_outage_credit_eligible("ELIGIBLE") is True
    assert _is_outage_credit_eligible("YES") is True

    assert _parse_problem_status(None, frozenset({"CLEAR"})) is None
    assert _parse_problem_status("NOT_FLAGGED", frozenset()) is False
    assert _parse_problem_status("FLAGGED", frozenset({"CLEAR"})) is True

    assert _parse_speed_achievable(ModemHealth(speed_achievable=False)) is False
    assert _parse_speed_achievable(ModemHealth(speed_achievable_status="PASS")) is True
    assert (
        _parse_speed_achievable(ModemHealth(speed_achievable_status="FAILED")) is False
    )
    assert (
        _parse_speed_achievable(ModemHealth(speed_achievable_status="UNKNOWN")) is None
    )
    assert _parse_speed_achievable(ModemHealth()) is None

    assert _truncate_state(None) is None
    long_text = "x" * 300
    truncated = _truncate_state(long_text)
    assert truncated is not None and len(truncated) == 255 and truncated.endswith("…")

    assert _parse_timestamp(None) is None
    assert _parse_timestamp("   ") is None
    assert _parse_timestamp("invalid") is None
    assert _parse_timestamp("2026-11-26T12:00:00") == datetime(
        2026, 11, 26, 12, 0, tzinfo=UTC
    )
    assert _parse_timestamp("1700000000") == datetime.fromtimestamp(1700000000, tz=UTC)
    assert _parse_timestamp("1700000000000") == datetime.fromtimestamp(
        1700000000, tz=UTC
    )

    assert _parse_date(None) is None
    assert _parse_date("  ") is None
    assert _parse_date("2026-12-15") == date(2026, 12, 15)
    assert _parse_date("2026-12-15T10:00:00Z") == date(2026, 12, 15)
    assert _parse_date("12/15/26") == date(2026, 12, 15)
    assert _parse_date("13/45/2026") is None
    assert _parse_date("not-a-date") is None


async def test_multi_router_scoping_in_read_platforms(hass: HomeAssistant) -> None:
    r1 = make_router(router_id="router-1", network_id="net-1")
    r2 = make_router(
        router_id="router-2",
        network_id="net-2",
        mac_address="00:00:5E:00:53:21",
        serial_number="SYN-RTR-0002",
    )
    m1 = make_modem(modem_id="modem-1")
    eq = make_equipment_state(include_pod=False, include_stb=False)
    eq.routers = [r1, r2]
    eq.modems = [m1]
    eq.modem_health = {m1.id: make_modem_health(equipment_id=m1.id)}
    net = make_network_state(router_id="router-1")
    client = make_mock_client(equipment_state=eq, network_state=net)
    entry, _, _ = await setup_integration(hass, mock_client=client)
    eid = entry.entry_id

    r1_pf = _entity_by_unique_id(
        hass, Platform.SENSOR, f"{eid}:router:router-1:port_forward_count"
    )
    r2_pf = _entity_by_unique_id(
        hass, Platform.SENSOR, f"{eid}:router:router-2:port_forward_count"
    )
    assert r1_pf is not None and r2_pf is not None
    assert hass.states.get(r1_pf.entity_id).state == "1"
    assert hass.states.get(r2_pf.entity_id).state == "0"


async def test_device_tracker_no_orphan_client_devices_and_zero_frame_warnings(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="homeassistant.helpers.frame"):
        entry, _client, _ = await setup_integration(
            hass,
            options={OPT_CLIENT_TRACKER_MODE: CLIENT_TRACKER_MODE_ALL},
        )

    frame_warnings = [
        record
        for record in caplog.records
        if record.name == "homeassistant.helpers.frame"
        and record.levelno >= logging.WARNING
    ]
    assert frame_warnings == []

    eid = entry.entry_id
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    devices = dr.async_entries_for_config_entry(dev_reg, eid)
    assert len(devices) == 5
    for device in devices:
        assert device.identifiers, f"Unexpected connection-only device: {device.name}"
        device_entities = er.async_entries_for_device(
            ent_reg, device.id, include_disabled_entities=True
        )
        assert device_entities, f"Unexpected empty 0-entity device: {device.name}"

    router_device = dev_reg.async_get_device_by_identifier(
        equipment_device_identifier(eid, "router-1"), eid
    )
    assert router_device is not None
    tracker_comp = hass.data["entity_components"]["device_tracker"]
    srv_reg = _entity_by_unique_id(
        hass, Platform.DEVICE_TRACKER, f"{eid}:client_tracker:router-1:00005e005311"
    )
    assert srv_reg is not None
    assert srv_reg.device_id == router_device.id
    tracker_entity = tracker_comp.get_entity(srv_reg.entity_id)
    assert tracker_entity is not None
    assert tracker_entity.mac_address == "00:00:5E:00:53:11"
    assert tracker_entity.ip_address == "192.0.2.101"
    assert tracker_entity.hostname == "syn-server.local"


async def test_read_platforms_edge_cases_and_missing_data(hass: HomeAssistant) -> None:
    from custom_components.spectrum.device_tracker import (
        _client_device_info,
        _client_group_names,
        _client_key,
        _client_router_id,
        _format_client_mac,
        _resolve_client_router,
    )
    from custom_components.spectrum.image import _primary_wifi_credentials
    from custom_components.spectrum.sensor import _next_appointment, _primary_wifi

    assert _client_key(Client(id="cid-only", mac_address="")) == "cid-only"
    assert _client_key(Client(id=None, mac_address="")) is None
    assert _format_client_mac(Client(mac_address="not-a-mac")) == "not-a-mac"
    assert _format_client_mac(Client(mac_address="")) is None
    assert _client_device_info("entry-x", "account", None)["identifiers"] == {
        (DOMAIN, "entry-x:account")
    }
    assert _client_device_info("entry-x", "orphan-rtr", None)["identifiers"] == {
        (DOMAIN, "entry-x:equipment:orphan-rtr")
    }

    r1 = make_router(router_id="r1", network_id="n1")
    r2 = make_router(router_id="r2", network_id="n2")
    eq_multi = make_equipment_state(include_pod=False, include_stb=False)
    eq_multi.routers = [r1, r2]
    assert _resolve_client_router(Client(network_id="n2"), eq_multi) == r2
    assert _resolve_client_router(Client(network_id=None), eq_multi) is None
    assert _resolve_client_router(Client(), None) is None
    assert _client_router_id(Client(router_id="orphan-rtr"), eq_multi) == "orphan-rtr"
    assert _client_router_id(Client(), eq_multi) == "account"

    net = make_network_state(router_id="r1")
    mac_only_client = Client(mac_address="00:00:5E:00:53:11", group_name=None)
    assert _client_group_names(mac_only_client, None) == []
    assert "Lab Group" in _client_group_names(mac_only_client, net)

    non_primary_rtr = make_router(with_subsystems=False)
    non_primary_rtr.wifi_networks = [
        WifiNetwork(name="SecondarySSID", password="pw", is_primary=False)
    ]
    assert _primary_wifi_credentials(non_primary_rtr) == ("SecondarySSID", "pw", "WPA")
    assert _primary_wifi(non_primary_rtr) is not None

    entry, client, _ = await setup_integration(hass)
    eid = entry.entry_id

    eq = entry.runtime_data.equipment.data.equipment
    eq.routers[0].is_online = None
    eq.routers[0].connection_state = None
    eq.modems[0].is_online = None
    eq.modems[0].connection_state = None
    await entry.runtime_data.equipment.async_refresh()
    await hass.async_block_till_done()

    rtr_conn = _entity_by_unique_id(
        hass, Platform.BINARY_SENSOR, f"{eid}:router:router-1:connectivity"
    )
    assert rtr_conn is not None
    assert hass.states.get(rtr_conn.entity_id).state == "on"

    from custom_components.spectrum.coordinator import EquipmentBundle

    bundle_no_start = EquipmentBundle(
        equipment=eq,
        appointments=[
            __import__("aiospectrum").ServiceAppointment(
                appointment_id="a-nostart", start_time=None, status="OPEN"
            )
        ],
    )
    assert _next_appointment(bundle_no_start) is not None

    ent_reg = er.async_get(hass)
    wifi_qr_reg = _entity_by_unique_id(
        hass, Platform.IMAGE, f"{eid}:router:router-1:wifi_qr"
    )
    assert wifi_qr_reg is not None
    ent_reg.async_update_entity(wifi_qr_reg.entity_id, disabled_by=None)
    with patch_spectrum_client(client):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    image_entity = hass.data["entity_components"]["image"].get_entity(
        wifi_qr_reg.entity_id
    )
    assert image_entity is not None

    entry.runtime_data.equipment.data = None
    entry.runtime_data.network.data = None
    entry.runtime_data.account.data = None
    entry.runtime_data.equipment.async_update_listeners()
    entry.runtime_data.network.async_update_listeners()
    entry.runtime_data.account.async_update_listeners()
    await hass.async_block_till_done()

    assert await image_entity.async_image() is None
    assert hass.states.get(rtr_conn.entity_id).state == "unavailable"

    tracker_reg = _entity_by_unique_id(
        hass, Platform.DEVICE_TRACKER, f"{eid}:client_tracker:router-1:00005e005311"
    )
    assert tracker_reg is not None
    assert hass.states.get(tracker_reg.entity_id).state == "unavailable"


async def test_live_router_omits_unsupported_thread_lan_signal_and_cleans_sentinel(
    hass: HomeAssistant,
) -> None:
    router = make_router(router_id="router-live", with_subsystems=False)
    router.feature_support = {
        "threadNetwork": "NOT_SUPPORTED",
        "cellularNetworkBackup": "SUPPORTED",
        "updateUpnpConfiguration": "SUPPORTED",
        "updateDnsConfiguration": "SUPPORTED",
    }
    router.thread_network = ThreadNetworkConfig()
    router.thread_enabled = None
    router.thread_radio_state = None
    router.lan_ipv4 = None
    router.cellular_backup = CellularBackup(
        activation_status="ACTIVE",
        is_ready=True,
        is_in_use=False,
        signal_strength=None,
        data_usage_current=0.0,
        data_usage_limit=10.0,
        data_usage_unit="GB",
        failover_status="UNKNOWN_BECAUSE_NOT_IMPLEMENTED",
    )
    eq = make_equipment_state(
        with_subsystems=False,
        include_pod=False,
        include_stb=False,
        include_modem_health=False,
        include_speed_test=False,
    )
    eq.routers = [router]
    net = make_network_state(
        router_id="router-live",
        with_threats=False,
        with_coverage=False,
        with_shield=False,
    )
    client = make_mock_client(equipment_state=eq, network_state=net)
    entry, _, _ = await setup_integration(hass, mock_client=client)
    eid = entry.entry_id

    assert (
        _entity_by_unique_id(hass, Platform.SWITCH, f"{eid}:thread_radio:router-live")
        is None
    )
    assert (
        _entity_by_unique_id(
            hass, Platform.SENSOR, f"{eid}:router:router-live:thread_channel"
        )
        is None
    )
    assert (
        _entity_by_unique_id(
            hass, Platform.SENSOR, f"{eid}:router:router-live:lan_ipv4"
        )
        is None
    )
    assert (
        _entity_by_unique_id(
            hass, Platform.SENSOR, f"{eid}:router:router-live:cellular_backup_signal"
        )
        is None
    )

    cb_status_reg = _entity_by_unique_id(
        hass, Platform.SENSOR, f"{eid}:router:router-live:cellular_backup_status"
    )
    assert cb_status_reg is not None
    cb_status_state = hass.states.get(cb_status_reg.entity_id)
    assert cb_status_state is not None
    assert cb_status_state.state == "ACTIVE"

    router.lan_ipv4 = "192.0.2.1"
    router.cellular_backup.signal_strength = 3
    await entry.runtime_data.equipment.async_refresh()
    await hass.async_block_till_done()

    lan_reg = _entity_by_unique_id(
        hass, Platform.SENSOR, f"{eid}:router:router-live:lan_ipv4"
    )
    sig_reg = _entity_by_unique_id(
        hass, Platform.SENSOR, f"{eid}:router:router-live:cellular_backup_signal"
    )
    assert lan_reg is not None
    assert sig_reg is not None
    assert hass.states.get(lan_reg.entity_id).state == "192.0.2.1"
    assert hass.states.get(sig_reg.entity_id).state == "3"


async def test_unsupported_set_top_box_creates_no_entity_or_device(
    hass: HomeAssistant,
) -> None:
    unsupported_stb = SetTopBox(
        id="stb-unsup",
        nickname="Xumo Stream Box",
        model="ESST11AEI",
        equipment_type="STB",
        manufacturer="Xumo",
        mac_address="00:00:5E:00:53:44",
        connection_state=None,
        is_online=None,
        feature_support={"connectionState": "NOT_SUPPORTED"},
    )
    eq = make_equipment_state(include_pod=False, include_stb=False)
    eq.set_top_boxes = [unsupported_stb]
    client = make_mock_client(equipment_state=eq)
    entry, _, _ = await setup_integration(hass, mock_client=client)
    eid = entry.entry_id

    assert (
        _entity_by_unique_id(
            hass, Platform.BINARY_SENSOR, f"{eid}:stb:stb-unsup:connectivity"
        )
        is None
    )
    dev_reg = dr.async_get(hass)
    assert (
        dev_reg.async_get_device_by_identifier(
            equipment_device_identifier(eid, "stb-unsup"), eid
        )
        is None
    )


async def test_security_shield_threats_active_only_counts_unended_threats(
    hass: HomeAssistant,
) -> None:
    eq = make_equipment_state(include_pod=False, include_stb=False)
    net = make_network_state(router_id="router-1", with_threats=False)
    historical_threats = [
        SecurityThreat(
            threat_id="threat-hist-1",
            action="BLOCKED",
            client_id="client-1",
            started_at="2026-11-26T12:00:00Z",
            ended_at="2026-11-26T12:05:00Z",
        ),
        SecurityThreat(
            threat_id="threat-hist-2",
            action="BLOCKED",
            client_id="client-1",
            started_at="2026-11-26T13:00:00Z",
            ended_at="2026-11-26T13:05:00Z",
        ),
    ]
    net.threats = list(historical_threats)
    for shield in net.security_shields.values():
        shield.threats = list(historical_threats)

    client = make_mock_client(equipment_state=eq, network_state=net)
    entry, _, _ = await setup_integration(hass, mock_client=client)
    eid = entry.entry_id

    threat_reg = _entity_by_unique_id(
        hass,
        Platform.BINARY_SENSOR,
        f"{eid}:router:router-1:security_shield_threats_active",
    )
    assert threat_reg is not None
    state = hass.states.get(threat_reg.entity_id)
    assert state is not None
    assert state.state == "off"
    assert state.attributes["active_threats"] == 0
    assert state.attributes["total_threats"] == 2

    active_threat = SecurityThreat(
        threat_id="threat-live-1",
        action="BLOCKED",
        client_id="client-1",
        started_at="2026-11-26T14:00:00Z",
        ended_at=None,
    )
    for shield in net.security_shields.values():
        shield.threats.append(active_threat)
    net.threats.append(active_threat)
    await entry.runtime_data.network.async_refresh()
    await hass.async_block_till_done()

    state_active = hass.states.get(threat_reg.entity_id)
    assert state_active is not None
    assert state_active.state == "on"
    assert state_active.attributes["active_threats"] == 1
    assert state_active.attributes["total_threats"] == 3


async def test_speed_test_sensors_create_only_populated_metrics_and_add_dynamically(
    hass: HomeAssistant,
) -> None:
    eq = make_equipment_state(
        include_pod=False, include_stb=False, include_speed_test=False
    )
    partial_speed_test = SpeedTestResult(
        id="st-partial-1",
        status="COMPLETED",
        download_mbps=910.0,
        upload_mbps=36.5,
        latency_ms=None,
        jitter_ms=None,
        packet_loss_pct=None,
        timestamp="2026-11-26T15:30:00Z",
        equipment_id="router-1",
    )
    eq.speed_test_history = [partial_speed_test]
    client = make_mock_client(equipment_state=eq)
    entry, _, _ = await setup_integration(hass, mock_client=client)
    eid = entry.entry_id

    for present_key in (
        "speed_test_download",
        "speed_test_upload",
        "speed_test_last_run",
        "speed_test_status",
    ):
        assert (
            _entity_by_unique_id(
                hass, Platform.SENSOR, f"{eid}:router:router-1:{present_key}"
            )
            is not None
        )

    for omitted_key in (
        "speed_test_latency",
        "speed_test_jitter",
        "speed_test_packet_loss",
    ):
        assert (
            _entity_by_unique_id(
                hass, Platform.SENSOR, f"{eid}:router:router-1:{omitted_key}"
            )
            is None
        )

    partial_speed_test.latency_ms = 12.5
    partial_speed_test.jitter_ms = 1.2
    partial_speed_test.packet_loss_pct = 0.1
    await entry.runtime_data.equipment.async_refresh()
    await hass.async_block_till_done()

    for key, expected in (
        ("speed_test_latency", "12.5"),
        ("speed_test_jitter", "1.2"),
        ("speed_test_packet_loss", "0.1"),
    ):
        reg = _entity_by_unique_id(
            hass, Platform.SENSOR, f"{eid}:router:router-1:{key}"
        )
        assert reg is not None
        assert hass.states.get(reg.entity_id).state == expected
