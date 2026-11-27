from __future__ import annotations

import json
from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.spectrum.diagnostics import (
    _serialize_network_data,
    async_get_config_entry_diagnostics,
)

from .conftest import (
    SYNTHETIC_ACCESS_TOKEN,
    SYNTHETIC_ACCOUNT_ID,
    SYNTHETIC_ACCOUNT_NUMBER,
    SYNTHETIC_BILLING_ID,
    SYNTHETIC_DEVICE_UUID,
    SYNTHETIC_DEVICE_VERIFIER,
    SYNTHETIC_DIVISION_ID,
    SYNTHETIC_FQ_ACCOUNT_ID,
    SYNTHETIC_NETWORK_ID,
    SYNTHETIC_REFRESH_TOKEN,
    SYNTHETIC_SELECTED_ACCT,
    make_config_entry,
    make_token_set,
    setup_integration,
)


def _walk_all_strings(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.append(str(key))
            found.extend(_walk_all_strings(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_all_strings(item))
    elif node is not None:
        found.append(str(node))
    return found


async def test_diagnostics_exhaustive_nested_redaction(hass: HomeAssistant) -> None:
    tokens = make_token_set()
    entry = make_config_entry(
        hass,
        tokens=tokens,
        title="Synthetic Secret Title 99",
    )
    await setup_integration(hass, entry=entry)

    diag = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(diag)
    all_values = _walk_all_strings(diag)

    sensitive_values = [
        "Synthetic Secret Title 99",
        entry.unique_id or "",
        SYNTHETIC_ACCESS_TOKEN,
        SYNTHETIC_REFRESH_TOKEN,
        tokens.id_token,
        SYNTHETIC_DEVICE_VERIFIER,
        SYNTHETIC_DEVICE_UUID,
        SYNTHETIC_SELECTED_ACCT,
        SYNTHETIC_ACCOUNT_NUMBER,
        SYNTHETIC_DIVISION_ID,
        SYNTHETIC_BILLING_ID,
        SYNTHETIC_FQ_ACCOUNT_ID,
        SYNTHETIC_ACCOUNT_ID,
        "a1b2c3d4e5f60718293a4b5c6d7e8f90",
        SYNTHETIC_NETWORK_ID,
        "00:00:5E:00:53:01",
        "00:00:5E:00:53:02",
        "00:00:5E:00:53:03",
        "00:00:5E:00:53:04",
        "00:00:5E:00:53:11",
        "00:00:5E:00:53:12",
        "00:00:5E:00:53:13",
        "00:00:5E:00:53:14",
        "SYN-RTR-0001",
        "SYN-MDM-0001",
        "SYN-POD-0001",
        "SYN-STB-0001",
        "198.51.100.10",
        "2001:db8::10",
        "192.0.2.1",
        "203.0.113.10",
        "2001:db8::20",
        "192.0.2.2",
        "192.0.2.30",
        "2001:db8::30",
        "192.0.2.40",
        "192.0.2.53",
        "198.51.100.53",
        "192.0.2.101",
        "2001:db8::101",
        "192.0.2.102",
        "192.0.2.103",
        "192.0.2.104",
        "SyntheticWiFi-Main",
        "SynMainPassword!42",
        "SyntheticWiFi-Guest",
        "SynGuestPassword!99",
        "Synthetic Server",
        "syn-server.local",
        "Main Server",
        "Synthetic Guest Phone",
        "Synthetic Printer",
        "Synthetic Tablet",
        "Lab Group",
        "Kids Group",
        "Bedtime Schedule",
        "Living Room Router",
        "Office Modem",
        "Upstairs Pod",
        "Living Room STB",
        "00112233445566778899AABBCCDDEEFF",
        "1122334455667788",
        "SynThreadNet",
        "ABCD",
        "http://malware.example.invalid/payload",
        "203.0.113.99",
        "APPT-SYN-001",
    ]

    for secret in sensitive_values:
        assert secret, "Test secret must be non-empty"
        assert secret not in serialized, (
            f"Sensitive value leaked in diagnostics: {secret}"
        )
        assert all(secret not in val for val in all_values)

    assert diag["entry"]["title"] == "**REDACTED**"
    assert diag["entry"]["unique_id"] == "**REDACTED**"
    assert diag["equipment"]["equipment"]["routers"][0]["model"] == "SAX2V1S"
    assert isinstance(diag["network"]["security_shields"], list)
    assert isinstance(diag["network"]["wifi_coverage"], list)
    assert diag["account"]["plan"]["internet_plan_name"] == "Spectrum Internet Gig"


async def test_diagnostics_none_coordinator_data(hass: HomeAssistant) -> None:
    entry, _client, _captured = await setup_integration(hass)
    entry.runtime_data.network.data = None
    entry.runtime_data.equipment.data = None
    entry.runtime_data.account.data = None

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["network"] is None
    assert diag["equipment"] is None
    assert diag["account"] is None
    assert _serialize_network_data(None) is None
