from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiospectrum import (
    Client,
    ClientGroup,
    SpectrumAuthError,
    SpectrumError,
)
from homeassistant.components.button import ButtonDeviceClass
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.spectrum.const import DOMAIN
from custom_components.spectrum.entity import equipment_device_identifier

from .conftest import (
    SYNTHETIC_NETWORK_ID,
    make_equipment_state,
    make_mock_client,
    make_modem,
    make_network_state,
    make_pod,
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


async def test_router_switches_toggle_refresh_and_errors(hass: HomeAssistant) -> None:
    entry, client, _ = await setup_integration(hass)
    eid = entry.entry_id

    shield_reg = _entity_by_unique_id(
        hass, Platform.SWITCH, f"{eid}:security_shield:router-1"
    )
    upnp_reg = _entity_by_unique_id(hass, Platform.SWITCH, f"{eid}:upnp:router-1")
    hotspot_reg = _entity_by_unique_id(hass, Platform.SWITCH, f"{eid}:hotspot:router-1")
    thread_reg = _entity_by_unique_id(
        hass, Platform.SWITCH, f"{eid}:thread_radio:router-1"
    )
    assert shield_reg is not None
    assert upnp_reg is not None
    assert hotspot_reg is not None
    assert thread_reg is not None

    for reg in (shield_reg, upnp_reg, hotspot_reg, thread_reg):
        assert reg.entity_category is EntityCategory.CONFIG
        assert hass.states.get(reg.entity_id).state == "on"

    with (
        patch.object(
            entry.runtime_data.network, "async_request_refresh", new_callable=AsyncMock
        ) as net_refresh,
        patch.object(
            entry.runtime_data.equipment,
            "async_request_refresh",
            new_callable=AsyncMock,
        ) as eq_refresh,
    ):
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": shield_reg.entity_id},
            blocking=True,
        )
        client.async_set_security_shield.assert_awaited_with(
            SYNTHETIC_NETWORK_ID, False
        )
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": shield_reg.entity_id},
            blocking=True,
        )
        client.async_set_security_shield.assert_awaited_with(SYNTHETIC_NETWORK_ID, True)

        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": upnp_reg.entity_id}, blocking=True
        )
        client.async_set_upnp.assert_awaited_with("router-1", False)
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": upnp_reg.entity_id}, blocking=True
        )
        client.async_set_upnp.assert_awaited_with("router-1", True)

        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": hotspot_reg.entity_id}, blocking=True
        )
        client.async_set_hotspot.assert_awaited_with(SYNTHETIC_NETWORK_ID, False)
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": hotspot_reg.entity_id}, blocking=True
        )
        client.async_set_hotspot.assert_awaited_with(SYNTHETIC_NETWORK_ID, True)

        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": thread_reg.entity_id}, blocking=True
        )
        client.async_set_thread_radio.assert_awaited_with("router-1", False)
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": thread_reg.entity_id}, blocking=True
        )
        client.async_set_thread_radio.assert_awaited_with("router-1", True)

        assert net_refresh.await_count == 2
        assert eq_refresh.await_count == 6

    client.async_set_upnp.side_effect = SpectrumAuthError("expired")
    with (
        patch.object(entry, "async_start_reauth") as reauth_spy,
        pytest.raises(HomeAssistantError) as auth_err,
    ):
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": upnp_reg.entity_id}, blocking=True
        )
    assert auth_err.value.translation_key == "auth_failed"
    reauth_spy.assert_called_once_with(hass)

    client.async_set_upnp.side_effect = SpectrumError("failed")
    with pytest.raises(HomeAssistantError) as api_err:
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": upnp_reg.entity_id}, blocking=True
        )
    assert api_err.value.translation_key == "api_error"
    client.async_set_upnp.side_effect = None


async def test_client_group_and_client_pause_switches_dynamic_lifecycle(
    hass: HomeAssistant,
) -> None:
    entry, client, _ = await setup_integration(hass)
    ent_reg = er.async_get(hass)
    eid = entry.entry_id

    grp1_uid = f"{eid}:client_group_pause:router-1:grp-1"
    grp2_uid = f"{eid}:client_group_pause:router-1:grp-2"
    grp1_reg = _entity_by_unique_id(hass, Platform.SWITCH, grp1_uid)
    grp2_reg = _entity_by_unique_id(hass, Platform.SWITCH, grp2_uid)
    assert grp1_reg is not None and grp2_reg is not None
    assert grp1_reg.disabled_by is None
    assert grp2_reg.disabled_by is None
    assert hass.states.get(grp1_reg.entity_id).state == "off"
    grp2_state = hass.states.get(grp2_reg.entity_id)
    assert grp2_state is not None
    assert grp2_state.state == "on"
    assert grp2_state.attributes["pause_reason"] == "BEDTIME"
    assert grp2_state.attributes["pause_until"] == "2026-11-27T08:00:00Z"

    cli1_uid = f"{eid}:client_pause:router-1:00005e005311"
    cli4_uid = f"{eid}:client_pause:router-1:00005e005314"
    cli1_reg = _entity_by_unique_id(hass, Platform.SWITCH, cli1_uid)
    cli4_reg = _entity_by_unique_id(hass, Platform.SWITCH, cli4_uid)
    assert cli1_reg is not None and cli4_reg is not None
    assert cli1_reg.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert cli4_reg.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    ent_reg.async_update_entity(cli1_reg.entity_id, disabled_by=None)
    ent_reg.async_update_entity(cli4_reg.entity_id, disabled_by=None)
    with patch_spectrum_client(client):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get(cli1_reg.entity_id).state == "off"
    cli4_state = hass.states.get(cli4_reg.entity_id)
    assert cli4_state is not None
    assert cli4_state.state == "on"
    assert cli4_state.attributes["client_id"] == "client-4"
    assert cli4_state.attributes["pause_until"] == "2026-11-27T08:00:00Z"

    with patch.object(
        entry.runtime_data.network, "async_request_refresh", new_callable=AsyncMock
    ) as net_refresh:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": grp1_reg.entity_id}, blocking=True
        )
        client.async_set_group_pause.assert_awaited_with(
            "router-1", "grp-1", paused=True
        )
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": grp2_reg.entity_id}, blocking=True
        )
        client.async_set_group_pause.assert_awaited_with(
            "router-1", "grp-2", paused=False
        )

        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": cli1_reg.entity_id}, blocking=True
        )
        client.async_set_client_pause.assert_awaited_with(
            "router-1", "client-1", paused=True
        )
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": cli4_reg.entity_id}, blocking=True
        )
        client.async_set_client_pause.assert_awaited_with(
            "router-1", "client-4", paused=False
        )
        assert net_refresh.await_count == 4

    net = entry.runtime_data.network.data
    net.client_groups.append(
        ClientGroup(
            id="grp-3", name="Dynamic Group", router_id="router-1", paused=False
        )
    )
    net.clients.append(
        Client(
            id="client-6",
            mac_address="00:00:5E:00:53:16",
            router_id="router-1",
            display_name="Dynamic Client",
            connected=True,
            paused=False,
        )
    )
    await entry.runtime_data.network.async_refresh()
    await hass.async_block_till_done()

    dyn_grp_uid = f"{eid}:client_group_pause:router-1:grp-3"
    dyn_cli_uid = f"{eid}:client_pause:router-1:00005e005316"
    assert _entity_by_unique_id(hass, Platform.SWITCH, dyn_grp_uid) is not None
    assert _entity_by_unique_id(hass, Platform.SWITCH, dyn_cli_uid) is not None

    net.client_groups = [g for g in net.client_groups if g.id != "grp-3"]
    net.clients = [c for c in net.clients if c.id != "client-6"]
    await entry.runtime_data.network.async_refresh()
    await hass.async_block_till_done()

    assert _entity_by_unique_id(hass, Platform.SWITCH, dyn_grp_uid) is None
    assert _entity_by_unique_id(hass, Platform.SWITCH, dyn_cli_uid) is None


async def test_switches_and_buttons_supported_only_omits_unsupported(
    hass: HomeAssistant,
) -> None:
    eq = make_equipment_state(
        with_subsystems=False, include_pod=False, include_stb=False
    )
    eq.modems = [make_modem(modem_id="modem-min", supported_features=[])]
    net = make_network_state(
        router_id="router-1", with_threats=False, with_coverage=False, with_shield=False
    )
    client = make_mock_client(equipment_state=eq, network_state=net)
    entry, _, _ = await setup_integration(hass, mock_client=client)
    eid = entry.entry_id

    for uid in (
        f"{eid}:security_shield:router-1",
        f"{eid}:upnp:router-1",
        f"{eid}:hotspot:router-1",
        f"{eid}:thread_radio:router-1",
    ):
        assert _entity_by_unique_id(hass, Platform.SWITCH, uid) is None

    for uid in (
        f"{eid}:reboot:router-1",
        f"{eid}:run_speed_test:router-1",
        f"{eid}:smart_steer:router-1",
        f"{eid}:reboot:modem-min",
        f"{eid}:run_modem_speed_test:modem-min",
    ):
        assert _entity_by_unique_id(hass, Platform.BUTTON, uid) is None

    assert (
        _entity_by_unique_id(hass, Platform.BUTTON, f"{eid}:account:refresh")
        is not None
    )


async def test_buttons_account_router_standalone_modem_and_errors(
    hass: HomeAssistant,
) -> None:
    entry, client, _ = await setup_integration(hass)
    eid = entry.entry_id

    refresh_reg = _entity_by_unique_id(hass, Platform.BUTTON, f"{eid}:account:refresh")
    rtr_reboot = _entity_by_unique_id(hass, Platform.BUTTON, f"{eid}:reboot:router-1")
    rtr_speed = _entity_by_unique_id(
        hass, Platform.BUTTON, f"{eid}:run_speed_test:router-1"
    )
    rtr_steer = _entity_by_unique_id(
        hass, Platform.BUTTON, f"{eid}:smart_steer:router-1"
    )
    mdm_reboot = _entity_by_unique_id(hass, Platform.BUTTON, f"{eid}:reboot:modem-1")
    mdm_speed = _entity_by_unique_id(
        hass, Platform.BUTTON, f"{eid}:run_modem_speed_test:modem-1"
    )

    assert all(
        reg is not None
        for reg in (
            refresh_reg,
            rtr_reboot,
            rtr_speed,
            rtr_steer,
            mdm_reboot,
            mdm_speed,
        )
    )
    assert refresh_reg.entity_category is EntityCategory.DIAGNOSTIC
    assert rtr_reboot.entity_category is EntityCategory.CONFIG
    assert hass.states.get(rtr_reboot.entity_id).attributes["device_class"] == (
        ButtonDeviceClass.RESTART
    )

    with (
        patch.object(
            entry.runtime_data.equipment,
            "async_request_refresh",
            new_callable=AsyncMock,
        ) as eq_refresh,
        patch.object(
            entry.runtime_data.network, "async_request_refresh", new_callable=AsyncMock
        ) as net_refresh,
        patch.object(
            entry.runtime_data.account, "async_request_refresh", new_callable=AsyncMock
        ) as acct_refresh,
    ):
        await hass.services.async_call(
            "button", "press", {"entity_id": refresh_reg.entity_id}, blocking=True
        )
        eq_refresh.assert_awaited_once()
        net_refresh.assert_awaited_once()
        acct_refresh.assert_awaited_once()

        await hass.services.async_call(
            "button", "press", {"entity_id": rtr_reboot.entity_id}, blocking=True
        )
        client.async_reboot_equipment.assert_awaited_with("router-1")

        await hass.services.async_call(
            "button", "press", {"entity_id": rtr_speed.entity_id}, blocking=True
        )
        client.async_initiate_speed_test.assert_awaited_with(
            "router-1", operation="ROUTER"
        )

        await hass.services.async_call(
            "button", "press", {"entity_id": rtr_steer.entity_id}, blocking=True
        )
        client.async_smart_steer.assert_awaited_with("router-1")

        await hass.services.async_call(
            "button", "press", {"entity_id": mdm_reboot.entity_id}, blocking=True
        )
        client.async_reboot_equipment.assert_awaited_with("modem-1")

        await hass.services.async_call(
            "button", "press", {"entity_id": mdm_speed.entity_id}, blocking=True
        )
        client.async_initiate_speed_test.assert_awaited_with(
            "modem-1", operation="MODEM_DOWNLOAD"
        )

        assert eq_refresh.await_count == 6

    client.async_reboot_equipment.side_effect = SpectrumAuthError("expired")
    with (
        patch.object(entry, "async_start_reauth") as reauth_spy,
        pytest.raises(HomeAssistantError) as auth_err,
    ):
        await hass.services.async_call(
            "button", "press", {"entity_id": rtr_reboot.entity_id}, blocking=True
        )
    assert auth_err.value.translation_key == "auth_failed"
    reauth_spy.assert_called_once_with(hass)

    client.async_reboot_equipment.side_effect = SpectrumError("failed")
    with pytest.raises(HomeAssistantError) as api_err:
        await hass.services.async_call(
            "button", "press", {"entity_id": rtr_reboot.entity_id}, blocking=True
        )
    assert api_err.value.translation_key == "api_error"
    client.async_reboot_equipment.side_effect = None


async def test_buttons_combo_router_and_modem_on_single_device(
    hass: HomeAssistant,
) -> None:
    entry, client, _ = await setup_integration(hass, combo=True)
    eid = entry.entry_id
    dev_reg = dr.async_get(hass)
    combo_dev = dev_reg.async_get_device_by_identifier(
        equipment_device_identifier(eid, "combo-1"), eid
    )
    assert combo_dev is not None

    rtr_reboot = _entity_by_unique_id(hass, Platform.BUTTON, f"{eid}:reboot:combo-1")
    mdm_reboot = _entity_by_unique_id(
        hass, Platform.BUTTON, f"{eid}:reboot_modem:combo-1"
    )
    mdm_speed = _entity_by_unique_id(
        hass, Platform.BUTTON, f"{eid}:run_modem_speed_test:combo-1"
    )
    assert rtr_reboot is not None
    assert mdm_reboot is not None
    assert mdm_speed is not None
    assert rtr_reboot.device_id == combo_dev.id
    assert mdm_reboot.device_id == combo_dev.id
    assert mdm_speed.device_id == combo_dev.id
    assert mdm_reboot.translation_key == "reboot_modem"

    await hass.services.async_call(
        "button", "press", {"entity_id": mdm_reboot.entity_id}, blocking=True
    )
    client.async_reboot_equipment.assert_awaited_with("combo-1")


async def test_select_pod_led_mode_state_errors_and_dynamic_discovery(
    hass: HomeAssistant,
) -> None:
    entry, client, _ = await setup_integration(hass)
    eid = entry.entry_id

    pod_sel = _entity_by_unique_id(hass, Platform.SELECT, f"{eid}:pod_led_mode:pod-1")
    assert pod_sel is not None
    assert pod_sel.entity_category is EntityCategory.CONFIG
    state = hass.states.get(pod_sel.entity_id)
    assert state is not None
    assert state.state == "normal"
    assert state.attributes["options"] == ["normal", "locate"]

    with patch.object(
        entry.runtime_data.equipment, "async_request_refresh", new_callable=AsyncMock
    ) as eq_refresh:
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": pod_sel.entity_id, "option": "locate"},
            blocking=True,
        )
        client.async_update_pod_led.assert_awaited_once_with("pod-1", "LOCATE")
        eq_refresh.assert_awaited_once()
        assert hass.states.get(pod_sel.entity_id).state == "locate"

        client.async_update_pod_led.return_value = {}
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": pod_sel.entity_id, "option": "normal"},
            blocking=True,
        )
        assert hass.states.get(pod_sel.entity_id).state == "normal"

    client.async_update_pod_led.side_effect = SpectrumAuthError("expired")
    with (
        patch.object(entry, "async_start_reauth") as reauth_spy,
        pytest.raises(HomeAssistantError) as auth_err,
    ):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": pod_sel.entity_id, "option": "locate"},
            blocking=True,
        )
    assert auth_err.value.translation_key == "auth_failed"
    reauth_spy.assert_called_once_with(hass)

    client.async_update_pod_led.side_effect = SpectrumError("failed")
    with pytest.raises(HomeAssistantError) as api_err:
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": pod_sel.entity_id, "option": "locate"},
            blocking=True,
        )
    assert api_err.value.translation_key == "api_error"
    client.async_update_pod_led.side_effect = None

    entry.runtime_data.equipment.data.equipment.pods.append(make_pod(pod_id="pod-2"))
    await entry.runtime_data.equipment.async_refresh()
    await hass.async_block_till_done()

    pod2_sel = _entity_by_unique_id(hass, Platform.SELECT, f"{eid}:pod_led_mode:pod-2")
    assert pod2_sel is not None
    assert hass.states.get(pod2_sel.entity_id).state == "normal"

    select_entity = hass.data["entity_components"]["select"].get_entity(
        pod2_sel.entity_id
    )
    assert select_entity is not None
    entry.runtime_data.equipment.data.equipment.pods[-1].led_mode = "LOCATE"
    assert select_entity.current_option == "locate"
    entry.runtime_data.equipment.data.equipment.pods[-1].led_mode = "BOGUS"
    assert select_entity.current_option is None

    entry.runtime_data.equipment.data.equipment.pods = []
    assert select_entity.available is False
    assert select_entity.current_option is None


async def test_switches_edge_cases_and_fallbacks(hass: HomeAssistant) -> None:
    from aiospectrum import SecurityShield, ThreadNetworkConfig, UpnpConfig

    from custom_components.spectrum.switch import (
        _resolve_client_router_id,
        _resolve_group_router_id,
        _resolve_router_network_id,
        _router_device_info_for_id,
        _supports_hotspot,
        _supports_security_shield,
        _supports_thread_radio,
        _supports_upnp,
    )

    from .conftest import make_router

    r_unsup = make_router(router_id="r-unsup", with_subsystems=False)
    r_unsup.feature_support = {
        "threadNetwork": "NOT_SUPPORTED",
        "updateUpnpConfiguration": "NOT_SUPPORTED",
    }
    r_unsup.thread_network = ThreadNetworkConfig()
    r_unsup.upnp = UpnpConfig(enabled=None)
    r_unsup.hotspot_status = "UNKNOWN_BECAUSE_NOT_IMPLEMENTED"
    r_unsup.hotspot_enabled = True
    r_unsup.security_shield = SecurityShield(
        enabled=None,
        status="UNKNOWN_BECAUSE_NOT_IMPLEMENTED",
        network_id=SYNTHETIC_NETWORK_ID,
    )
    assert _supports_thread_radio(r_unsup) is False
    assert _supports_upnp(r_unsup) is False
    assert _supports_hotspot(r_unsup, None, None) is False
    assert _supports_security_shield(r_unsup, None, None) is False

    eq = make_equipment_state(include_pod=False, include_stb=False)
    net = make_network_state(router_id="router-1")
    r_no_net = make_router(router_id="router-1", network_id=None)
    assert _resolve_router_network_id(r_no_net, eq, net) == SYNTHETIC_NETWORK_ID
    r_no_net.security_shield = None
    assert _resolve_router_network_id(r_no_net, eq, net) == SYNTHETIC_NETWORK_ID
    net_only_shield = make_network_state(router_id="router-1")
    net_only_shield.network_ids = []
    assert (
        _resolve_router_network_id(r_no_net, eq, net_only_shield)
        == SYNTHETIC_NETWORK_ID
    )
    assert _resolve_router_network_id(r_no_net, eq, None) is None

    r_hs = make_router(router_id="router-1", network_id=SYNTHETIC_NETWORK_ID)
    r_hs.hotspot_status = None
    r_hs.hotspot_enabled = True
    assert _supports_hotspot(r_hs, eq, net) is True
    assert _supports_hotspot(r_no_net, eq, None) is False

    assert (
        _resolve_group_router_id(ClientGroup(id="g", router_id=None), eq) == "router-1"
    )
    assert _resolve_group_router_id(ClientGroup(id="g", router_id=None), None) is None
    assert (
        _resolve_client_router_id(
            Client(router_id=None, network_id=SYNTHETIC_NETWORK_ID), eq
        )
        == "router-1"
    )
    assert (
        _resolve_client_router_id(Client(router_id=None, network_id=None), eq)
        == "router-1"
    )
    assert (
        _resolve_client_router_id(Client(router_id=None, network_id=None), None) is None
    )
    assert _router_device_info_for_id("entry-x", None, "missing-rtr") is not None

    entry, _client, _ = await setup_integration(hass)
    eid = entry.entry_id
    sw_comp = hass.data["entity_components"]["switch"]

    shield_reg = _entity_by_unique_id(
        hass, Platform.SWITCH, f"{eid}:security_shield:router-1"
    )
    hotspot_reg = _entity_by_unique_id(hass, Platform.SWITCH, f"{eid}:hotspot:router-1")
    thread_reg = _entity_by_unique_id(
        hass, Platform.SWITCH, f"{eid}:thread_radio:router-1"
    )
    upnp_reg = _entity_by_unique_id(hass, Platform.SWITCH, f"{eid}:upnp:router-1")
    grp1_reg = _entity_by_unique_id(
        hass, Platform.SWITCH, f"{eid}:client_group_pause:router-1:grp-1"
    )
    assert all(
        r is not None for r in (shield_reg, hotspot_reg, thread_reg, upnp_reg, grp1_reg)
    )

    thread_ent = sw_comp.get_entity(thread_reg.entity_id)
    entry.runtime_data.equipment.data.equipment.routers[0].thread_enabled = None
    assert thread_ent.is_on is True
    entry.runtime_data.equipment.data.equipment.routers[0].thread_network = None
    assert thread_ent.is_on is None

    shield_ent = sw_comp.get_entity(shield_reg.entity_id)
    hotspot_ent = sw_comp.get_entity(hotspot_reg.entity_id)
    upnp_ent = sw_comp.get_entity(upnp_reg.entity_id)
    grp_ent = sw_comp.get_entity(grp1_reg.entity_id)

    entry.runtime_data.equipment.data = None
    entry.runtime_data.network.data = None
    assert upnp_ent.is_on is None
    assert thread_ent.is_on is None
    assert grp_ent.is_on is None
    assert grp_ent.extra_state_attributes is None
    with pytest.raises(HomeAssistantError):
        await shield_ent.async_turn_on()
    with pytest.raises(HomeAssistantError):
        await hotspot_ent.async_turn_on()
