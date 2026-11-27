from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiospectrum import (
    AmbiguousMatchError,
    Client,
    ClientGroup,
    ExternalPortImmutableError,
    SpectrumAuthError,
    SpectrumConnectionError,
    SpectrumError,
    SpectrumValidationError,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from custom_components.spectrum.const import DOMAIN
from custom_components.spectrum.entity import (
    account_device_identifier,
    equipment_device_identifier,
)
from custom_components.spectrum.services import (
    ATTR_ACCESS_POINT_ID,
    ATTR_AUTO_RESERVE,
    ATTR_BROADCAST,
    ATTR_CLIENT,
    ATTR_CLIENTS,
    ATTR_CREATE,
    ATTR_CUSTOM_ENABLED,
    ATTR_DAYS_OF_WEEK,
    ATTR_DEVICE_ID,
    ATTR_ENABLED,
    ATTR_END_TIME,
    ATTR_EXTERNAL_PORT,
    ATTR_GROUP,
    ATTR_ICON_REFERENCE,
    ATTR_INTERNAL_PORT,
    ATTR_IP_ADDRESS,
    ATTR_NAME,
    ATTR_NEW_EXTERNAL_PORT,
    ATTR_NEW_NAME,
    ATTR_OPERATION,
    ATTR_PASSWORD,
    ATTR_PRIMARY,
    ATTR_PROTOCOL,
    ATTR_RECREATE,
    ATTR_SECONDARY,
    ATTR_SSID,
    ATTR_START_TIME,
    ATTR_TARGET,
    ATTR_TARGET_TYPE,
    ATTR_WAIT,
    SERVICE_ADD_PORT_FORWARD,
    SERVICE_CREATE_CLIENT_GROUP,
    SERVICE_DELETE_ADDITIONAL_NETWORK,
    SERVICE_DELETE_CLIENT_GROUP,
    SERVICE_DELETE_IP_RESERVATION,
    SERVICE_DELETE_PORT_FORWARD,
    SERVICE_LIST_PORT_FORWARDS,
    SERVICE_REMOVE_CLIENT,
    SERVICE_RENAME_CLIENT,
    SERVICE_RUN_SPEED_TEST,
    SERVICE_SET_ACCESS_SCHEDULE,
    SERVICE_SET_ADDITIONAL_NETWORK,
    SERVICE_SET_DNS,
    SERVICE_SET_IP_RESERVATION,
    SERVICE_UPDATE_CLIENT_GROUP,
    SERVICE_UPDATE_PORT_FORWARD,
    SERVICE_UPDATE_WIFI,
)

from .conftest import setup_integration

ALL_17_SERVICES = (
    SERVICE_ADD_PORT_FORWARD,
    SERVICE_UPDATE_PORT_FORWARD,
    SERVICE_DELETE_PORT_FORWARD,
    SERVICE_LIST_PORT_FORWARDS,
    SERVICE_SET_IP_RESERVATION,
    SERVICE_DELETE_IP_RESERVATION,
    SERVICE_SET_ACCESS_SCHEDULE,
    SERVICE_SET_DNS,
    SERVICE_UPDATE_WIFI,
    SERVICE_SET_ADDITIONAL_NETWORK,
    SERVICE_DELETE_ADDITIONAL_NETWORK,
    SERVICE_RENAME_CLIENT,
    SERVICE_REMOVE_CLIENT,
    SERVICE_CREATE_CLIENT_GROUP,
    SERVICE_UPDATE_CLIENT_GROUP,
    SERVICE_DELETE_CLIENT_GROUP,
    SERVICE_RUN_SPEED_TEST,
)


def _device_id_for(hass: HomeAssistant, entry_id: str, equipment_id: str | None) -> str:
    dev_reg = dr.async_get(hass)
    ident = (
        account_device_identifier(entry_id)
        if equipment_id is None
        else equipment_device_identifier(entry_id, equipment_id)
    )
    device = dev_reg.async_get_device_by_identifier(ident, entry_id)
    assert device is not None
    return device.id


async def test_all_17_services_registered(hass: HomeAssistant) -> None:
    await setup_integration(hass)
    for service in ALL_17_SERVICES:
        assert hass.services.has_service(DOMAIN, service), f"Missing service: {service}"
    assert not hass.services.has_service(DOMAIN, "factory_reset")


async def test_port_forward_and_ip_reservation_services(hass: HomeAssistant) -> None:
    entry, client, _ = await setup_integration(hass)
    router_dev = _device_id_for(hass, entry.entry_id, "router-1")

    with patch.object(
        entry.runtime_data.network, "async_request_refresh", new_callable=AsyncMock
    ) as net_refresh:
        resp = await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_PORT_FORWARD,
            {
                ATTR_DEVICE_ID: router_dev,
                ATTR_CLIENT: "Synthetic Server",
                ATTR_EXTERNAL_PORT: 8080,
                ATTR_INTERNAL_PORT: 80,
                ATTR_PROTOCOL: "tcp",
                ATTR_NAME: "Web",
                ATTR_IP_ADDRESS: "192.0.2.101",
                ATTR_AUTO_RESERVE: True,
            },
            blocking=True,
            return_response=True,
        )
        assert resp is not None
        assert resp["auto_reserved"] is True
        client.async_add_port_forward.assert_awaited_once_with(
            "router-1",
            "00:00:5E:00:53:11",
            external_port=8080,
            internal_port=80,
            protocol="TCP",
            name="Web",
            ip_address="192.0.2.101",
            auto_reserve=True,
        )

        resp_upd = await hass.services.async_call(
            DOMAIN,
            SERVICE_UPDATE_PORT_FORWARD,
            {
                ATTR_DEVICE_ID: [router_dev],
                ATTR_CLIENT: "00:00:5E:00:53:11",
                ATTR_EXTERNAL_PORT: 8080,
                ATTR_INTERNAL_PORT: 8081,
                ATTR_PROTOCOL: "TCP_AND_UDP",
                ATTR_NAME: "Web2",
                ATTR_NEW_EXTERNAL_PORT: 8443,
                ATTR_RECREATE: True,
            },
            blocking=True,
            return_response=True,
        )
        assert resp_upd is not None
        client.async_update_port_forward.assert_awaited_once_with(
            "router-1",
            "00:00:5E:00:53:11",
            8080,
            internal_port=8081,
            protocol="TCP_AND_UDP",
            name="Web2",
            new_external_port=8443,
            recreate=True,
        )

        client.async_update_port_forward.side_effect = ExternalPortImmutableError(
            current_port=8080, requested_port=8443
        )
        with pytest.raises(ServiceValidationError) as exc_info:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_UPDATE_PORT_FORWARD,
                {
                    ATTR_DEVICE_ID: router_dev,
                    ATTR_CLIENT: "00:00:5E:00:53:11",
                    ATTR_EXTERNAL_PORT: 8080,
                    ATTR_NEW_EXTERNAL_PORT: 8443,
                    ATTR_RECREATE: False,
                },
                blocking=True,
            )
        assert exc_info.value.translation_key == "external_port_immutable"
        client.async_update_port_forward.side_effect = None

        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_PORT_FORWARD,
            {
                ATTR_DEVICE_ID: router_dev,
                ATTR_CLIENT: "00:00:5E:00:53:FF",
                ATTR_EXTERNAL_PORT: 8080,
            },
            blocking=True,
        )
        client.async_delete_port_forward.assert_awaited_once_with(
            "router-1", "00005e0053ff", 8080
        )

        listed_all = await hass.services.async_call(
            DOMAIN,
            SERVICE_LIST_PORT_FORWARDS,
            {ATTR_DEVICE_ID: router_dev},
            blocking=True,
            return_response=True,
        )
        assert listed_all is not None
        assert len(listed_all["port_forwards"]) == 1

        listed_filtered = await hass.services.async_call(
            DOMAIN,
            SERVICE_LIST_PORT_FORWARDS,
            {ATTR_DEVICE_ID: router_dev, ATTR_CLIENT: "Synthetic Server"},
            blocking=True,
            return_response=True,
        )
        assert listed_filtered is not None
        assert len(listed_filtered["port_forwards"]) == 1
        client.async_list_port_forwards.assert_awaited_once_with(
            "router-1", "00:00:5E:00:53:11"
        )

        saved_net = entry.runtime_data.network.data
        entry.runtime_data.network.data = None
        listed_empty = await hass.services.async_call(
            DOMAIN,
            SERVICE_LIST_PORT_FORWARDS,
            {ATTR_DEVICE_ID: router_dev},
            blocking=True,
            return_response=True,
        )
        assert listed_empty == {"port_forwards": []}
        entry.runtime_data.network.data = saved_net

        res_resp = await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_IP_RESERVATION,
            {
                ATTR_DEVICE_ID: router_dev,
                ATTR_CLIENT: "Synthetic Server",
                ATTR_IP_ADDRESS: "192.0.2.150",
            },
            blocking=True,
            return_response=True,
        )
        assert res_resp is not None
        client.async_set_ip_reservation.assert_awaited_once_with(
            "router-1", "00:00:5E:00:53:11", "192.0.2.150"
        )

        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_IP_RESERVATION,
            {
                ATTR_DEVICE_ID: router_dev,
                ATTR_CLIENT: "Synthetic Server",
            },
            blocking=True,
        )
        client.async_delete_ip_reservation.assert_awaited_once_with(
            "router-1", "00:00:5E:00:53:11"
        )
        assert net_refresh.await_count == 5


async def test_schedule_dns_wifi_additional_client_group_and_speed_test_services(
    hass: HomeAssistant,
) -> None:
    entry, client, _ = await setup_integration(hass)
    router_dev = _device_id_for(hass, entry.entry_id, "router-1")
    modem_dev = _device_id_for(hass, entry.entry_id, "modem-1")

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
        sched_cli = await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ACCESS_SCHEDULE,
            {
                ATTR_DEVICE_ID: router_dev,
                ATTR_TARGET_TYPE: "client",
                ATTR_TARGET: "Synthetic Server",
                ATTR_NAME: "Study Time",
                ATTR_START_TIME: "18:00:00",
                ATTR_END_TIME: "20:00",
                ATTR_DAYS_OF_WEEK: ["tue"],
                ATTR_ENABLED: True,
                ATTR_CREATE: True,
            },
            blocking=True,
            return_response=True,
        )
        assert sched_cli is not None
        client.async_set_client_schedule.assert_awaited_once_with(
            "router-1",
            "client-1",
            name="Study Time",
            start_time="18:00",
            end_time="20:00",
            days_of_week=["TUE"],
            enabled=True,
            create=True,
        )

        sched_grp = await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ACCESS_SCHEDULE,
            {
                ATTR_DEVICE_ID: router_dev,
                ATTR_TARGET_TYPE: "client_group",
                ATTR_TARGET: "Kids Group",
                ATTR_NAME: "Bedtime",
                ATTR_START_TIME: "21:00",
                ATTR_END_TIME: "07:00:00",
                ATTR_DAYS_OF_WEEK: ["MON"],
                ATTR_ENABLED: True,
                ATTR_CREATE: False,
            },
            blocking=True,
            return_response=True,
        )
        assert sched_grp is not None
        client.async_set_group_schedule.assert_awaited_once_with(
            "router-1",
            "grp-2",
            name="Bedtime",
            start_time="21:00",
            end_time="07:00",
            days_of_week=["MON"],
            enabled=True,
            create=False,
        )

        dns_resp = await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_DNS,
            {
                ATTR_DEVICE_ID: router_dev,
                ATTR_CUSTOM_ENABLED: True,
                ATTR_PRIMARY: "192.0.2.53",
                ATTR_SECONDARY: "198.51.100.53",
            },
            blocking=True,
            return_response=True,
        )
        assert dns_resp is not None
        client.async_set_dns.assert_awaited_once_with(
            "router-1",
            custom_enabled=True,
            primary="192.0.2.53",
            secondary="198.51.100.53",
        )

        await hass.services.async_call(
            DOMAIN,
            SERVICE_UPDATE_WIFI,
            {
                ATTR_DEVICE_ID: router_dev,
                ATTR_SSID: "SynNewSSID",
                ATTR_PASSWORD: "SynNewPassword!1",
                ATTR_ACCESS_POINT_ID: "ap-primary-1",
            },
            blocking=True,
        )
        client.async_update_wifi.assert_awaited_once_with(
            "router-1",
            "SynNewSSID",
            "SynNewPassword!1",
            access_point_id="ap-primary-1",
        )

        add_upd = await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ADDITIONAL_NETWORK,
            {
                ATTR_DEVICE_ID: router_dev,
                ATTR_SSID: "SynGuestUpdated",
                ATTR_PASSWORD: "SynGuestPassword!3",
                ATTR_BROADCAST: False,
                ATTR_CREATE: False,
            },
            blocking=True,
            return_response=True,
        )
        assert add_upd is not None
        client.async_update_additional_network.assert_awaited_once()

        add_create = await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ADDITIONAL_NETWORK,
            {
                ATTR_DEVICE_ID: router_dev,
                ATTR_SSID: "SynGuestCreated",
                ATTR_PASSWORD: "SynGuestPassword!4",
                ATTR_BROADCAST: True,
                ATTR_CREATE: True,
            },
            blocking=True,
            return_response=True,
        )
        assert add_create is not None
        client.async_create_additional_network.assert_awaited_once()

        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_ADDITIONAL_NETWORK,
            {ATTR_DEVICE_ID: router_dev},
            blocking=True,
        )
        client.async_delete_additional_network.assert_awaited_once_with("router-1")

        await hass.services.async_call(
            DOMAIN,
            SERVICE_RENAME_CLIENT,
            {
                ATTR_DEVICE_ID: router_dev,
                ATTR_CLIENT: "Synthetic Server",
                ATTR_NEW_NAME: "Renamed Server",
                ATTR_ICON_REFERENCE: "SERVER",
            },
            blocking=True,
        )
        client.async_rename_client.assert_awaited_once_with(
            "router-1", "client-1", "Renamed Server", icon_reference="SERVER"
        )

        await hass.services.async_call(
            DOMAIN,
            SERVICE_REMOVE_CLIENT,
            {ATTR_DEVICE_ID: router_dev, ATTR_CLIENT: "Synthetic Guest Phone"},
            blocking=True,
        )
        client.async_remove_client.assert_awaited_once_with("router-1", "client-2")

        grp_create = await hass.services.async_call(
            DOMAIN,
            SERVICE_CREATE_CLIENT_GROUP,
            {
                ATTR_DEVICE_ID: router_dev,
                ATTR_NAME: "New Group",
                ATTR_CLIENTS: ["Synthetic Server"],
            },
            blocking=True,
            return_response=True,
        )
        assert grp_create is not None
        client.async_create_client_group.assert_awaited_once_with(
            "router-1", "New Group", ["client-1"]
        )

        grp_upd = await hass.services.async_call(
            DOMAIN,
            SERVICE_UPDATE_CLIENT_GROUP,
            {
                ATTR_DEVICE_ID: router_dev,
                ATTR_GROUP: "Lab Group",
                ATTR_NAME: "Renamed Group",
                ATTR_CLIENTS: ["Synthetic Server"],
            },
            blocking=True,
            return_response=True,
        )
        assert grp_upd is not None
        client.async_update_client_group.assert_awaited_once_with(
            "router-1", "grp-1", "Renamed Group", ["client-1"]
        )

        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_CLIENT_GROUP,
            {ATTR_DEVICE_ID: router_dev, ATTR_GROUP: "Lab Group"},
            blocking=True,
        )
        client.async_delete_client_group.assert_awaited_once_with("router-1", "grp-1")

        st_router = await hass.services.async_call(
            DOMAIN,
            SERVICE_RUN_SPEED_TEST,
            {ATTR_DEVICE_ID: router_dev, ATTR_WAIT: False},
            blocking=True,
            return_response=True,
        )
        assert st_router is not None
        client.async_run_speed_test.assert_awaited_with(
            "router-1", operation="ROUTER", wait=False
        )

        st_modem = await hass.services.async_call(
            DOMAIN,
            SERVICE_RUN_SPEED_TEST,
            {ATTR_DEVICE_ID: modem_dev, ATTR_WAIT: True},
            blocking=True,
            return_response=True,
        )
        assert st_modem is not None
        client.async_run_speed_test.assert_awaited_with(
            "modem-1", operation="MODEM_DOWNLOAD", wait=True
        )

        await hass.services.async_call(
            DOMAIN,
            SERVICE_RUN_SPEED_TEST,
            {
                ATTR_DEVICE_ID: modem_dev,
                ATTR_OPERATION: "MODEM_UPLOAD",
                ATTR_WAIT: False,
            },
            blocking=True,
        )
        client.async_run_speed_test.assert_awaited_with(
            "modem-1", operation="MODEM_UPLOAD", wait=False
        )

        assert net_refresh.await_count == 7
        assert eq_refresh.await_count == 8


async def test_service_target_resolution_and_error_handling(
    hass: HomeAssistant,
) -> None:
    entry, client, _ = await setup_integration(hass)
    router_dev = _device_id_for(hass, entry.entry_id, "router-1")
    account_dev = _device_id_for(hass, entry.entry_id, None)
    pod_dev = _device_id_for(hass, entry.entry_id, "pod-1")

    with pytest.raises(ServiceValidationError) as exc_multi:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_ADDITIONAL_NETWORK,
            {ATTR_DEVICE_ID: [router_dev, pod_dev]},
            blocking=True,
        )
    assert exc_multi.value.translation_key == "invalid_input"

    with pytest.raises(ServiceValidationError) as exc_missing:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_ADDITIONAL_NETWORK,
            {ATTR_DEVICE_ID: "nonexistent-device-id"},
            blocking=True,
        )
    assert exc_missing.value.translation_key == "device_not_found"

    with pytest.raises(ServiceValidationError) as exc_not_eq:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_ADDITIONAL_NETWORK,
            {ATTR_DEVICE_ID: account_dev},
            blocking=True,
        )
    assert exc_not_eq.value.translation_key == "not_equipment_device"

    with pytest.raises(ServiceValidationError) as exc_not_router:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_ADDITIONAL_NETWORK,
            {ATTR_DEVICE_ID: pod_dev},
            blocking=True,
        )
    assert exc_not_router.value.translation_key == "not_router_device"

    with pytest.raises(ServiceValidationError) as exc_not_st:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RUN_SPEED_TEST,
            {ATTR_DEVICE_ID: pod_dev},
            blocking=True,
        )
    assert exc_not_st.value.translation_key == "not_speed_test_device"

    with pytest.raises(ServiceValidationError) as exc_unknown_client:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_IP_RESERVATION,
            {ATTR_DEVICE_ID: router_dev, ATTR_CLIENT: "Does Not Exist"},
            blocking=True,
        )
    assert exc_unknown_client.value.translation_key == "invalid_input"

    entry.runtime_data.network.data.clients.append(
        Client(id=None, mac_address="00:00:5E:00:53:99", display_name="NoIdClient")
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REMOVE_CLIENT,
            {ATTR_DEVICE_ID: router_dev, ATTR_CLIENT: "NoIdClient"},
            blocking=True,
        )

    entry.runtime_data.network.data.client_groups.append(
        ClientGroup(id="", name="NoIdGroup", router_id="router-1")
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_CLIENT_GROUP,
            {ATTR_DEVICE_ID: router_dev, ATTR_GROUP: "NoIdGroup"},
            blocking=True,
        )

    for exc, expected_key in (
        (SpectrumValidationError("bad input"), "invalid_input"),
        (AmbiguousMatchError("ambiguous"), "invalid_input"),
    ):
        client.async_delete_additional_network.side_effect = exc
        with pytest.raises(ServiceValidationError) as sv_err:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_DELETE_ADDITIONAL_NETWORK,
                {ATTR_DEVICE_ID: router_dev},
                blocking=True,
            )
        assert sv_err.value.translation_key == expected_key

    client.async_delete_additional_network.side_effect = SpectrumAuthError("expired")
    with (
        patch.object(entry, "async_start_reauth") as reauth_spy,
        pytest.raises(HomeAssistantError) as auth_err,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_ADDITIONAL_NETWORK,
            {ATTR_DEVICE_ID: router_dev},
            blocking=True,
        )
    assert auth_err.value.translation_key == "auth_failed"
    reauth_spy.assert_called_once_with(hass)

    client.async_delete_additional_network.side_effect = SpectrumConnectionError("down")
    with pytest.raises(HomeAssistantError) as conn_err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_ADDITIONAL_NETWORK,
            {ATTR_DEVICE_ID: router_dev},
            blocking=True,
        )
    assert conn_err.value.translation_key == "connection_failed"

    client.async_delete_additional_network.side_effect = SpectrumError("boom")
    with pytest.raises(HomeAssistantError) as api_err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_ADDITIONAL_NETWORK,
            {ATTR_DEVICE_ID: router_dev},
            blocking=True,
        )
    assert api_err.value.translation_key == "api_error"
    client.async_delete_additional_network.side_effect = None

    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError) as unloaded_err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_ADDITIONAL_NETWORK,
            {ATTR_DEVICE_ID: router_dev},
            blocking=True,
        )
    assert unloaded_err.value.translation_key == "entry_not_loaded"
