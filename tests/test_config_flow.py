from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiospectrum import (
    SpectrumAuthError,
    SpectrumConnectionError,
    SpectrumError,
    SpectrumValidationError,
    derive_account_unique_id,
)
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.spectrum.config_flow import _extract_status_account_fallbacks
from custom_components.spectrum.const import (
    CLIENT_TRACKER_MODE_ALL,
    CLIENT_TRACKER_MODE_CONNECTED_OR_RESERVED,
    CLIENT_TRACKER_MODE_DISABLED,
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_UUID,
    CONF_DEVICE_VERIFIER,
    CONF_ID_TOKEN,
    CONF_REDIRECT_URL,
    CONF_REFRESH_TOKEN,
    CONF_SELECTED_ACCT,
    DOMAIN,
    OPT_ACCOUNT_SCAN_INTERVAL,
    OPT_CLIENT_TRACKER_MODE,
    OPT_EQUIPMENT_SCAN_INTERVAL,
    OPT_NETWORK_SCAN_INTERVAL,
)

from .conftest import (
    SYNTHETIC_ACCOUNT_NUMBER,
    SYNTHETIC_DEVICE_UUID,
    SYNTHETIC_DIVISION_ID,
    make_config_entry,
    make_jwt,
    make_mock_client,
    make_token_set,
    patch_spectrum_client,
    setup_integration,
)


def _patch_flow_auth(
    *,
    tokens=None,
    exchange_error: Exception | None = None,
    status_data: dict[str, Any] | None = None,
    status_error: Exception | None = None,
):
    tok = tokens or make_token_set()
    auth_client = MagicMock()
    if exchange_error is not None:
        auth_client.exchange_code = AsyncMock(side_effect=exchange_error)
    else:
        auth_client.exchange_code = AsyncMock(return_value=tok)

    api_client = MagicMock()
    api_client.tokens = tok
    if status_error is not None:
        api_client.execute_operation = AsyncMock(side_effect=status_error)
    else:
        api_client.execute_operation = AsyncMock(
            return_value=status_data if status_data is not None else {}
        )

    return (
        patch(
            "custom_components.spectrum.config_flow.SpectrumAuthClient",
            return_value=auth_client,
        ),
        patch(
            "custom_components.spectrum.config_flow.SpectrumClient",
            return_value=api_client,
        ),
        auth_client,
        api_client,
    )


async def test_user_flow_redirect_url_success(hass: HomeAssistant) -> None:
    tokens = make_token_set()
    p_auth, p_client, auth_mock, _ = _patch_flow_auth(tokens=tokens)
    mock_setup_client = make_mock_client()

    with p_auth, p_client, patch_spectrum_client(mock_setup_client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {}
        login_url = result["description_placeholders"]["login_url"]
        assert "code_challenge=" in login_url
        assert "code_challenge_method=S256" in login_url
        assert "code=" in result["description_placeholders"]["redirect_example"]

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_REDIRECT_URL: "https://www.spectrum.net/callback?code=syn-auth-code-123&state=syn-state"
            },
        )
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Spectrum"
    assert result2["data"][CONF_ACCESS_TOKEN] == tokens.access_token
    assert result2["data"][CONF_REFRESH_TOKEN] == tokens.refresh_token
    assert result2["data"][CONF_ID_TOKEN] == tokens.id_token
    assert result2["data"][CONF_DEVICE_VERIFIER] == tokens.device_verifier
    assert result2["data"][CONF_SELECTED_ACCT] == tokens.selected_acct
    assert result2["result"].unique_id == derive_account_unique_id(tokens.id_token)
    auth_mock.exchange_code.assert_awaited_once()
    assert auth_mock.exchange_code.await_args.args[0] == "syn-auth-code-123"


async def test_user_flow_bare_code_and_status_fallback(hass: HomeAssistant) -> None:
    empty_claims_jwt = make_jwt({"exp": 4102444800})
    tokens = make_token_set(id_token=empty_claims_jwt)
    status_data = {
        "viewer": {
            "account": {
                "accountMetadata": {
                    "accountNumber": SYNTHETIC_ACCOUNT_NUMBER,
                    "divisionId": SYNTHETIC_DIVISION_ID,
                    "billingId": "SYN-BILL-999",
                }
            }
        }
    }
    p_auth, p_client, auth_mock, _ = _patch_flow_auth(
        tokens=tokens, status_data=status_data
    )
    mock_setup_client = make_mock_client()

    with p_auth, p_client, patch_spectrum_client(mock_setup_client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_REDIRECT_URL: "bare-auth-code-xyz"},
        )
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    expected_uid = derive_account_unique_id(
        empty_claims_jwt,
        fallback_account_number=SYNTHETIC_ACCOUNT_NUMBER,
        fallback_division_id=SYNTHETIC_DIVISION_ID,
        fallback_id="SYN-BILL-999",
    )
    assert result2["result"].unique_id == expected_uid
    assert auth_mock.exchange_code.await_args.args[0] == "bare-auth-code-xyz"


def test_extract_status_account_fallbacks_non_dict() -> None:
    assert _extract_status_account_fallbacks({}) == (None, None, None)
    assert _extract_status_account_fallbacks({"viewer": "bad"}) == (None, None, None)
    assert _extract_status_account_fallbacks({"viewer": {"account": "bad"}}) == (
        None,
        None,
        None,
    )


async def test_user_flow_already_configured_aborts(hass: HomeAssistant) -> None:
    tokens = make_token_set()
    make_config_entry(hass, tokens=tokens)
    p_auth, p_client, _, _ = _patch_flow_auth(tokens=tokens)

    with p_auth, p_client:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_REDIRECT_URL: "syn-code-dup"},
        )

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("redirect_input", "exchange_error", "expected_field", "expected_error"),
    [
        (
            "https://www.spectrum.net/callback?state=no-code-here",
            None,
            CONF_REDIRECT_URL,
            "invalid_redirect",
        ),
        (
            "syn-code-bad-auth",
            SpectrumAuthError("Invalid grant"),
            "base",
            "invalid_auth",
        ),
        (
            "syn-code-conn-err",
            SpectrumConnectionError("Timeout"),
            "base",
            "cannot_connect",
        ),
        (
            "syn-code-val-err",
            SpectrumValidationError("Missing claim"),
            "base",
            "cannot_connect",
        ),
        (
            "syn-code-gen-err",
            SpectrumError("API failure"),
            "base",
            "cannot_connect",
        ),
        (
            "syn-code-unexpected",
            RuntimeError("Boom"),
            "base",
            "unknown",
        ),
    ],
)
async def test_user_flow_errors_and_recovery(
    hass: HomeAssistant,
    redirect_input: str,
    exchange_error: Exception | None,
    expected_field: str,
    expected_error: str,
) -> None:
    tokens = make_token_set()
    p_auth, p_client, auth_mock, _ = _patch_flow_auth(
        tokens=tokens, exchange_error=exchange_error
    )

    with p_auth, p_client:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        original_url = result["description_placeholders"]["login_url"]
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_REDIRECT_URL: redirect_input},
        )

        assert result2["type"] is FlowResultType.FORM
        assert result2["errors"] == {expected_field: expected_error}
        if expected_error == "invalid_auth":
            assert result2["description_placeholders"]["login_url"] != original_url

        auth_mock.exchange_code = AsyncMock(return_value=tokens)
        with patch_spectrum_client(make_mock_client()):
            result3 = await hass.config_entries.flow.async_configure(
                result2["flow_id"],
                {CONF_REDIRECT_URL: "syn-code-recovered"},
            )
            await hass.async_block_till_done()

    assert result3["type"] is FlowResultType.CREATE_ENTRY


async def test_reauth_flow_success_and_invalid_auth(hass: HomeAssistant) -> None:
    old_tokens = make_token_set(access_token="old-access", refresh_token="old-refresh")
    entry = make_config_entry(hass, tokens=old_tokens)

    new_tokens = make_token_set(access_token="new-access", refresh_token="new-refresh")
    p_auth, p_client, auth_mock, _ = _patch_flow_auth(
        tokens=new_tokens,
        exchange_error=SpectrumAuthError("Expired code"),
    )

    with p_auth, p_client, patch_spectrum_client(make_mock_client()):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert SYNTHETIC_DEVICE_UUID in result["description_placeholders"]["login_url"]

        result_err = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_REDIRECT_URL: "syn-reauth-bad"},
        )
        assert result_err["type"] is FlowResultType.FORM
        assert result_err["errors"] == {"base": "invalid_auth"}

        auth_mock.exchange_code = AsyncMock(return_value=new_tokens)
        result_ok = await hass.config_entries.flow.async_configure(
            result_err["flow_id"],
            {CONF_REDIRECT_URL: "syn-reauth-good"},
        )
        await hass.async_block_till_done()

    assert result_ok["type"] is FlowResultType.ABORT
    assert result_ok["reason"] == "reauth_successful"
    assert entry.data[CONF_ACCESS_TOKEN] == "new-access"
    assert entry.data[CONF_REFRESH_TOKEN] == "new-refresh"
    assert entry.data[CONF_DEVICE_UUID] == SYNTHETIC_DEVICE_UUID


async def test_reauth_flow_account_mismatch_aborts(hass: HomeAssistant) -> None:
    entry = make_config_entry(hass, tokens=make_token_set())
    other_jwt = make_jwt({"acct_num": "9999888877776666", "divisionId": "OTHER-DIV"})
    other_tokens = make_token_set(id_token=other_jwt)
    p_auth, p_client, _, _ = _patch_flow_auth(tokens=other_tokens)

    with p_auth, p_client:
        result = await entry.start_reauth_flow(hass)
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_REDIRECT_URL: "syn-wrong-account-code"},
        )

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "wrong_account"


async def test_reconfigure_flow_success_and_error(hass: HomeAssistant) -> None:
    old_tokens = make_token_set(access_token="old-reconf")
    entry = make_config_entry(hass, tokens=old_tokens)
    new_tokens = make_token_set(access_token="new-reconf")
    p_auth, p_client, auth_mock, _ = _patch_flow_auth(
        tokens=new_tokens,
        exchange_error=SpectrumConnectionError("Offline"),
    )

    with p_auth, p_client, patch_spectrum_client(make_mock_client()):
        result = await entry.start_reconfigure_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        result_err = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_REDIRECT_URL: "syn-reconf-fail"},
        )
        assert result_err["type"] is FlowResultType.FORM
        assert result_err["errors"] == {"base": "cannot_connect"}

        auth_mock.exchange_code = AsyncMock(return_value=new_tokens)
        result_ok = await hass.config_entries.flow.async_configure(
            result_err["flow_id"],
            {CONF_REDIRECT_URL: "syn-reconf-ok"},
        )
        await hass.async_block_till_done()

    assert result_ok["type"] is FlowResultType.ABORT
    assert result_ok["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ACCESS_TOKEN] == "new-reconf"


async def test_reconfigure_flow_account_mismatch_aborts(hass: HomeAssistant) -> None:
    entry = make_config_entry(hass, tokens=make_token_set())
    other_jwt = make_jwt({"acct_num": "1111222233334444", "divisionId": "OTHER-DIV"})
    other_tokens = make_token_set(id_token=other_jwt)
    p_auth, p_client, _, _ = _patch_flow_auth(tokens=other_tokens)

    with p_auth, p_client:
        result = await entry.start_reconfigure_flow(hass)
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_REDIRECT_URL: "syn-reconf-wrong-acct"},
        )

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "wrong_account"


@pytest.mark.parametrize(
    "tracker_mode",
    [
        CLIENT_TRACKER_MODE_CONNECTED_OR_RESERVED,
        CLIENT_TRACKER_MODE_ALL,
        CLIENT_TRACKER_MODE_DISABLED,
    ],
)
async def test_options_flow_updates_all_options_and_reloads(
    hass: HomeAssistant,
    tracker_mode: str,
) -> None:
    entry, mock_client, _ = await setup_integration(hass)

    with (
        patch_spectrum_client(mock_client),
        patch.object(
            hass.config_entries,
            "async_reload",
            wraps=hass.config_entries.async_reload,
        ) as reload_spy,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"

        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                OPT_NETWORK_SCAN_INTERVAL: 120,
                OPT_EQUIPMENT_SCAN_INTERVAL: 600,
                OPT_ACCOUNT_SCAN_INTERVAL: 7200,
                OPT_CLIENT_TRACKER_MODE: tracker_mode,
            },
        )
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[OPT_NETWORK_SCAN_INTERVAL] == 120
    assert entry.options[OPT_EQUIPMENT_SCAN_INTERVAL] == 600
    assert entry.options[OPT_ACCOUNT_SCAN_INTERVAL] == 7200
    assert entry.options[OPT_CLIENT_TRACKER_MODE] == tracker_mode
    reload_spy.assert_awaited_once_with(entry.entry_id)
