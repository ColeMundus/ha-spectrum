from __future__ import annotations

import asyncio
import logging

from aiospectrum import (
    DEFAULT_CLIENT_ID,
    SpectrumAuthClient,
    SpectrumClient,
    TokenSet,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_DEVICE_UUID,
    CONF_DEVICE_VERIFIER,
    CONF_ID_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SELECTED_ACCT,
    DEFAULT_ACCOUNT_SCAN_INTERVAL,
    DEFAULT_EQUIPMENT_SCAN_INTERVAL,
    DEFAULT_NETWORK_SCAN_INTERVAL,
    DOMAIN,
    OPT_ACCOUNT_SCAN_INTERVAL,
    OPT_EQUIPMENT_SCAN_INTERVAL,
    OPT_NETWORK_SCAN_INTERVAL,
    PLATFORMS,
)
from .coordinator import (
    SpectrumAccountCoordinator,
    SpectrumConfigEntry,
    SpectrumEquipmentCoordinator,
    SpectrumNetworkCoordinator,
    SpectrumRuntimeData,
)
from .entity import (
    account_device_identifier,
    async_register_equipment_devices,
    equipment_device_identifier,
)
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    _ = config
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SpectrumConfigEntry) -> bool:
    async_register_services(hass)

    tokens = TokenSet(
        access_token=str(entry.data[CONF_ACCESS_TOKEN]),
        refresh_token=str(entry.data[CONF_REFRESH_TOKEN]),
        id_token=str(entry.data.get(CONF_ID_TOKEN) or ""),
        device_verifier=(
            str(entry.data[CONF_DEVICE_VERIFIER])
            if entry.data.get(CONF_DEVICE_VERIFIER) is not None
            else None
        ),
        selected_acct=(
            str(entry.data[CONF_SELECTED_ACCT])
            if entry.data.get(CONF_SELECTED_ACCT) is not None
            else None
        ),
    )
    device_uuid = str(entry.data[CONF_DEVICE_UUID])
    client_id = str(entry.data.get(CONF_CLIENT_ID) or DEFAULT_CLIENT_ID)
    token_lock = asyncio.Lock()

    async def _async_on_token_update(new_tokens: TokenSet) -> None:
        async with token_lock:
            updated_data = {
                **entry.data,
                CONF_ACCESS_TOKEN: new_tokens.access_token,
                CONF_REFRESH_TOKEN: new_tokens.refresh_token,
                CONF_ID_TOKEN: new_tokens.id_token,
                CONF_DEVICE_VERIFIER: new_tokens.device_verifier,
                CONF_SELECTED_ACCT: new_tokens.selected_acct,
            }
            hass.config_entries.async_update_entry(entry, data=updated_data)

    client = SpectrumClient(
        async_get_clientsession(hass),
        tokens,
        device_uuid=device_uuid,
        client_id=client_id,
        on_token_update=_async_on_token_update,
    )

    network_interval = int(
        entry.options.get(OPT_NETWORK_SCAN_INTERVAL, DEFAULT_NETWORK_SCAN_INTERVAL)
    )
    equipment_interval = int(
        entry.options.get(OPT_EQUIPMENT_SCAN_INTERVAL, DEFAULT_EQUIPMENT_SCAN_INTERVAL)
    )
    account_interval = int(
        entry.options.get(OPT_ACCOUNT_SCAN_INTERVAL, DEFAULT_ACCOUNT_SCAN_INTERVAL)
    )

    network = SpectrumNetworkCoordinator(hass, entry, client, network_interval)
    equipment = SpectrumEquipmentCoordinator(hass, entry, client, equipment_interval)
    account = SpectrumAccountCoordinator(hass, entry, client, account_interval)

    await equipment.async_config_entry_first_refresh()
    await network.async_config_entry_first_refresh()
    await account.async_config_entry_first_refresh()

    entry.runtime_data = SpectrumRuntimeData(
        client=client,
        network=network,
        equipment=equipment,
        account=account,
        token_lock=token_lock,
    )

    if equipment.data is not None:
        async_register_equipment_devices(hass, entry.entry_id, equipment.data.equipment)

    last_options = dict(entry.options)

    async def _async_update_listener(
        hass_inner: HomeAssistant,
        updated_entry: SpectrumConfigEntry,
    ) -> None:
        nonlocal last_options
        current_options = dict(updated_entry.options)
        if current_options != last_options:
            last_options = current_options
            await hass_inner.config_entries.async_reload(updated_entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SpectrumConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SpectrumConfigEntry) -> None:
    access_token = str(entry.data.get(CONF_ACCESS_TOKEN) or "")
    if not access_token:
        return
    device_uuid = (
        str(entry.data[CONF_DEVICE_UUID]) if entry.data.get(CONF_DEVICE_UUID) else None
    )
    auth_client = SpectrumAuthClient(async_get_clientsession(hass))
    try:
        await auth_client.logout_local(access_token, device_uuid=device_uuid)
    except Exception:
        _LOGGER.debug(
            "Best-effort Spectrum local logout failed during entry removal",
            exc_info=True,
        )


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: SpectrumConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    _ = hass
    active_identifiers: set[tuple[str, str]] = {
        account_device_identifier(entry.entry_id)
    }
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None and runtime.equipment.data is not None:
        eq = runtime.equipment.data.equipment
        for router in eq.routers:
            if router.id:
                active_identifiers.add(
                    equipment_device_identifier(entry.entry_id, router.id)
                )
        for modem in eq.modems:
            if modem.id:
                active_identifiers.add(
                    equipment_device_identifier(entry.entry_id, modem.id)
                )
        for pod in eq.pods:
            if pod.id:
                active_identifiers.add(
                    equipment_device_identifier(entry.entry_id, pod.id)
                )
        for stb in eq.set_top_boxes:
            if stb.id:
                active_identifiers.add(
                    equipment_device_identifier(entry.entry_id, stb.id)
                )
    return not bool(device_entry.identifiers & active_identifiers)
