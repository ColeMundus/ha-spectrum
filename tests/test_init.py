from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiospectrum import (
    SpectrumAuthError,
    SpectrumConnectionError,
    SpectrumError,
    SpectrumGraphQLError,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.spectrum import (
    async_remove_config_entry_device,
    async_remove_entry,
    async_setup,
)
from custom_components.spectrum.const import (
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_UUID,
    CONF_DEVICE_VERIFIER,
    CONF_ID_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SELECTED_ACCT,
    DOMAIN,
)
from custom_components.spectrum.coordinator import (
    SpectrumAccountCoordinator,
    SpectrumEquipmentCoordinator,
    SpectrumNetworkCoordinator,
    _async_fetch_appointments,
)
from custom_components.spectrum.entity import (
    account_device_identifier,
    equipment_device_identifier,
    parse_account_identifier,
    parse_equipment_identifier,
)

from .conftest import (
    SYNTHETIC_DEVICE_UUID,
    make_config_entry,
    make_mock_client,
    make_token_set,
    setup_integration,
)


async def test_setup_and_unload_entry_all_platforms(hass: HomeAssistant) -> None:
    assert await async_setup(hass, {}) is True
    entry, _client, _captured = await setup_integration(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not None
    assert entry.runtime_data.equipment.data is not None
    assert entry.runtime_data.network.data is not None
    assert entry.runtime_data.account.data is not None

    ent_reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    platforms_present = {e.domain for e in entries}
    assert {
        "binary_sensor",
        "sensor",
        "switch",
        "button",
        "select",
        "device_tracker",
        "image",
    }.issubset(platforms_present)

    assert not hass.services.has_service(DOMAIN, "factory_reset")
    assert all("factory_reset" not in e.unique_id for e in entries)
    assert all("factory_reset" not in (e.translation_key or "") for e in entries)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_token_rotation_persists_without_reload(hass: HomeAssistant) -> None:
    entry, _client, captured = await setup_integration(hass)
    on_token_update = captured["kwargs"]["on_token_update"]
    rotated = make_token_set(
        access_token="rotated-access-token",
        refresh_token="rotated-refresh-token",
        device_verifier="rotated-verifier",
        selected_acct="rotated-acct",
    )

    with patch.object(
        hass.config_entries,
        "async_reload",
        wraps=hass.config_entries.async_reload,
    ) as reload_spy:
        await on_token_update(rotated)
        await hass.async_block_till_done()

    assert entry.data[CONF_ACCESS_TOKEN] == "rotated-access-token"
    assert entry.data[CONF_REFRESH_TOKEN] == "rotated-refresh-token"
    assert entry.data[CONF_ID_TOKEN] == rotated.id_token
    assert entry.data[CONF_DEVICE_VERIFIER] == "rotated-verifier"
    assert entry.data[CONF_SELECTED_ACCT] == "rotated-acct"
    reload_spy.assert_not_awaited()


async def test_remove_entry_calls_logout_local_and_swallows_errors(
    hass: HomeAssistant,
) -> None:
    entry = make_config_entry(hass)
    auth_client = MagicMock()
    auth_client.logout_local = AsyncMock(return_value=True)

    with patch(
        "custom_components.spectrum.SpectrumAuthClient",
        return_value=auth_client,
    ):
        await async_remove_entry(hass, entry)

    auth_client.logout_local.assert_awaited_once_with(
        entry.data[CONF_ACCESS_TOKEN],
        device_uuid=SYNTHETIC_DEVICE_UUID,
    )

    auth_client_err = MagicMock()
    auth_client_err.logout_local = AsyncMock(side_effect=RuntimeError("network down"))
    with patch(
        "custom_components.spectrum.SpectrumAuthClient",
        return_value=auth_client_err,
    ):
        await async_remove_entry(hass, entry)
    auth_client_err.logout_local.assert_awaited_once()

    empty_entry = make_config_entry(
        hass,
        entry_id="entry-empty-tok",
        unique_id="empty-tok-uid",
    )
    hass.config_entries.async_update_entry(
        empty_entry,
        data={**empty_entry.data, CONF_ACCESS_TOKEN: "", CONF_DEVICE_UUID: ""},
    )
    with patch("custom_components.spectrum.SpectrumAuthClient") as auth_cls:
        await async_remove_entry(hass, empty_entry)
    auth_cls.assert_not_called()


async def test_remove_config_entry_device_active_vs_stale(hass: HomeAssistant) -> None:
    entry, _client, _captured = await setup_integration(hass)
    dev_reg = dr.async_get(hass)

    for ident in (
        account_device_identifier(entry.entry_id),
        equipment_device_identifier(entry.entry_id, "router-1"),
        equipment_device_identifier(entry.entry_id, "modem-1"),
        equipment_device_identifier(entry.entry_id, "pod-1"),
        equipment_device_identifier(entry.entry_id, "stb-1"),
    ):
        device = dev_reg.async_get_device_by_identifier(ident, entry.entry_id)
        assert device is not None
        assert await async_remove_config_entry_device(hass, entry, device) is False

    stale_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={equipment_device_identifier(entry.entry_id, "stale-old-pod")},
        name="Removed Pod",
    )
    assert await async_remove_config_entry_device(hass, entry, stale_device) is True


def test_identifier_parsers() -> None:
    assert parse_account_identifier((DOMAIN, "entry-1:account")) == "entry-1"
    assert parse_account_identifier(("other", "entry-1:account")) is None
    assert parse_account_identifier((DOMAIN, ":account")) is None
    assert parse_equipment_identifier((DOMAIN, "entry-1:equipment:rtr-1")) == (
        "entry-1",
        "rtr-1",
    )
    assert parse_equipment_identifier(("other", "entry-1:equipment:rtr-1")) is None
    assert parse_equipment_identifier((DOMAIN, "entry-1:equipment:")) is None
    assert parse_equipment_identifier((DOMAIN, ":equipment:rtr-1")) is None


def test_entity_combo_and_pod_helpers() -> None:
    from custom_components.spectrum.entity import (
        find_combo_modem,
        find_pod_router_id,
    )

    from .conftest import make_equipment_state, make_pod, make_router

    eq = make_equipment_state(include_pod=False, include_stb=False)
    combo_rtr = make_router(router_id="combo-unmatched", is_combo=True)
    assert find_combo_modem(eq, combo_rtr) is None

    pod_no_net = make_pod(pod_id="pod-nonet", network_id=None)
    assert find_pod_router_id(eq, pod_no_net) == "router-1"

    eq.routers.append(make_router(router_id="router-2", network_id="net-2"))
    assert find_pod_router_id(eq, pod_no_net) is None


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (SpectrumAuthError("Expired"), ConfigEntryAuthFailed),
        (SpectrumConnectionError("Offline"), UpdateFailed),
        (SpectrumGraphQLError("GQL error"), UpdateFailed),
        (SpectrumError("Generic"), UpdateFailed),
    ],
)
async def test_coordinators_error_translation(
    hass: HomeAssistant,
    exc: Exception,
    expected: type[Exception],
) -> None:
    entry = make_config_entry(hass)
    client = make_mock_client()
    client.async_get_network_state = AsyncMock(side_effect=exc)
    client.async_get_equipment_state = AsyncMock(side_effect=exc)
    client.async_get_account_state = AsyncMock(side_effect=exc)

    net_coord = SpectrumNetworkCoordinator(hass, entry, client)
    eq_coord = SpectrumEquipmentCoordinator(hass, entry, client)
    acct_coord = SpectrumAccountCoordinator(hass, entry, client)

    with pytest.raises(expected):
        await net_coord._async_update_data()
    with pytest.raises(expected):
        await eq_coord._async_update_data()
    with pytest.raises(expected):
        await acct_coord._async_update_data()


async def test_fetch_appointments_handles_graphql_and_malformed_payloads() -> None:
    client = make_mock_client()
    client.execute_operation = AsyncMock(side_effect=SpectrumGraphQLError("denied"))
    assert await _async_fetch_appointments(client) == ([], None)

    client.execute_operation = AsyncMock(return_value="not-a-dict")
    assert await _async_fetch_appointments(client) == ([], None)

    client.execute_operation = AsyncMock(return_value={"serviceAppointments": None})
    assert await _async_fetch_appointments(client) == ([], None)
