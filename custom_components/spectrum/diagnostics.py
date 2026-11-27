from __future__ import annotations

from typing import Any, cast

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import SpectrumConfigEntry

TO_REDACT: frozenset[str] = frozenset(
    {
        "title",
        "unique_id",
        "access_token",
        "refresh_token",
        "id_token",
        "device_verifier",
        "device_uuid",
        "selected_acct",
        "account_id",
        "account_number",
        "account_number_hash",
        "fq_account_id",
        "division_id",
        "billing_id",
        "appointment_id",
        "network_id",
        "network_ids",
        "mac_address",
        "device_mac_address",
        "client_mac",
        "client_macs",
        "serial_number",
        "wan_ipv4",
        "wan_ipv6",
        "lan_ipv4",
        "ip_address",
        "ipv4_address",
        "ipv4_addresses",
        "ipv6_address",
        "ipv6_addresses",
        "primary",
        "secondary",
        "servers",
        "dns_servers",
        "ssid",
        "name",
        "password",
        "display_name",
        "host_name",
        "nickname",
        "client_name",
        "group_name",
        "network_name",
        "network_key",
        "extended_pan_id",
        "pan_id",
        "threat_url",
        "remote_ip",
        "remote_port",
        "username",
        "subject",
        "email",
        "phone",
        "device_macs",
        "usable_ips",
        "subnets",
        "ipv6_prefix",
        "gateway",
        "subnet_mask",
    }
)


def _serialize_network_data(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    serialized = dict(raw)
    if isinstance(serialized.get("security_shields"), dict):
        serialized["security_shields"] = list(serialized["security_shields"].values())
    if isinstance(serialized.get("wifi_coverage"), dict):
        serialized["wifi_coverage"] = list(serialized["wifi_coverage"].values())
    return serialized


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: SpectrumConfigEntry,
) -> dict[str, Any]:
    _ = hass
    runtime = entry.runtime_data
    payload: dict[str, Any] = {
        "entry": entry.as_dict(),
        "network": _serialize_network_data(
            runtime.network.data.to_dict() if runtime.network.data is not None else None
        ),
        "equipment": (
            runtime.equipment.data.to_dict()
            if runtime.equipment.data is not None
            else None
        ),
        "account": runtime.account.data.to_dict()
        if runtime.account.data is not None
        else None,
    }
    return cast(dict[str, Any], async_redact_data(payload, TO_REDACT))
