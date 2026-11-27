from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from aiospectrum import (
    AccountState,
    EquipmentState,
    NetworkState,
    ServiceAppointment,
    ServiceAppointmentAlert,
    ServiceProblem,
    SpectrumAuthError,
    SpectrumClient,
    SpectrumConnectionError,
    SpectrumError,
    SpectrumGraphQLError,
    SpeedTestResult,
)
from aiospectrum.operations import FETCH_AUTH_APPOINTMENTS
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_ACCOUNT_SCAN_INTERVAL,
    DEFAULT_EQUIPMENT_SCAN_INTERVAL,
    DEFAULT_NETWORK_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class EquipmentBundle:
    equipment: EquipmentState
    outages: list[ServiceProblem] = field(default_factory=list)
    appointments: list[ServiceAppointment] = field(default_factory=list)
    appointment_alert: ServiceAppointmentAlert | None = None
    speed_test_history: list[SpeedTestResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "equipment": self.equipment.to_dict(),
            "outages": [item.to_dict() for item in self.outages],
            "appointments": [item.to_dict() for item in self.appointments],
            "appointment_alert": (
                self.appointment_alert.to_dict()
                if self.appointment_alert is not None
                else None
            ),
            "speed_test_history": [item.to_dict() for item in self.speed_test_history],
        }


async def _async_fetch_appointments(
    client: SpectrumClient,
) -> tuple[list[ServiceAppointment], ServiceAppointmentAlert | None]:
    try:
        appt_data = await client.execute_operation(
            FETCH_AUTH_APPOINTMENTS,
            {},
            allow_partial=True,
        )
    except SpectrumGraphQLError:
        return [], None

    if not isinstance(appt_data, dict):
        return [], None

    sa_block = appt_data.get("serviceAppointments")
    if not isinstance(sa_block, dict):
        return [], None

    appointment_alert = ServiceAppointmentAlert.from_api(sa_block.get("alert"))
    raw_appointments = sa_block.get("appointments")
    appointments = [
        ServiceAppointment.from_api(item)
        for item in (raw_appointments if isinstance(raw_appointments, list) else [])
        if isinstance(item, dict)
    ]
    return appointments, appointment_alert


class SpectrumNetworkCoordinator(DataUpdateCoordinator[NetworkState]):
    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SpectrumConfigEntry,
        client: SpectrumClient,
        scan_interval: int = DEFAULT_NETWORK_SCAN_INTERVAL,
    ) -> None:
        self.client = client
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_network",
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> NetworkState:
        try:
            return await self.client.async_get_network_state(
                include_wifi_coverage=True,
                include_historical_coverage=True,
                include_client_groups=True,
            )
        except SpectrumAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from err
        except (SpectrumConnectionError, SpectrumGraphQLError, SpectrumError) as err:
            raise UpdateFailed(
                f"Unable to update Spectrum network state: {err}",
                translation_domain=DOMAIN,
                translation_key="connection_failed",
                translation_placeholders={"detail": str(err)},
            ) from err


class SpectrumEquipmentCoordinator(DataUpdateCoordinator[EquipmentBundle]):
    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SpectrumConfigEntry,
        client: SpectrumClient,
        scan_interval: int = DEFAULT_EQUIPMENT_SCAN_INTERVAL,
    ) -> None:
        self.client = client
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_equipment",
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> EquipmentBundle:
        try:
            equipment = await self.client.async_get_equipment_state(
                include_connection_state=True,
                include_set_top_boxes=True,
                include_modem_health=True,
                include_speed_test_history=True,
            )
            outages = await self.client.async_get_outages()
            appointments, appointment_alert = await _async_fetch_appointments(
                self.client
            )
            return EquipmentBundle(
                equipment=equipment,
                outages=outages,
                appointments=appointments,
                appointment_alert=appointment_alert,
                speed_test_history=list(equipment.speed_test_history),
            )
        except SpectrumAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from err
        except (SpectrumConnectionError, SpectrumGraphQLError, SpectrumError) as err:
            raise UpdateFailed(
                f"Unable to update Spectrum equipment state: {err}",
                translation_domain=DOMAIN,
                translation_key="connection_failed",
                translation_placeholders={"detail": str(err)},
            ) from err


class SpectrumAccountCoordinator(DataUpdateCoordinator[AccountState]):
    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SpectrumConfigEntry,
        client: SpectrumClient,
        scan_interval: int = DEFAULT_ACCOUNT_SCAN_INTERVAL,
    ) -> None:
        self.client = client
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_account",
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> AccountState:
        try:
            return await self.client.async_get_account_state()
        except SpectrumAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from err
        except (SpectrumConnectionError, SpectrumGraphQLError, SpectrumError) as err:
            raise UpdateFailed(
                f"Unable to update Spectrum account state: {err}",
                translation_domain=DOMAIN,
                translation_key="connection_failed",
                translation_placeholders={"detail": str(err)},
            ) from err


@dataclass
class SpectrumRuntimeData:
    client: SpectrumClient
    network: SpectrumNetworkCoordinator
    equipment: SpectrumEquipmentCoordinator
    account: SpectrumAccountCoordinator
    token_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


type SpectrumConfigEntry = ConfigEntry[SpectrumRuntimeData]
