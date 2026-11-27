from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import probatio as vol
from aiospectrum import (
    DEFAULT_CLIENT_ID,
    BrowserAuthChallenge,
    SpectrumAuthClient,
    SpectrumAuthError,
    SpectrumClient,
    SpectrumConnectionError,
    SpectrumError,
    SpectrumValidationError,
    TokenSet,
    build_browser_login_url,
    derive_account_unique_id,
    extract_code_from_redirect,
)
from aiospectrum.operations import FETCH_ACCOUNT_STATUS
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CLIENT_TRACKER_MODES,
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_DEVICE_UUID,
    CONF_DEVICE_VERIFIER,
    CONF_ID_TOKEN,
    CONF_REDIRECT_URL,
    CONF_REFRESH_TOKEN,
    CONF_SELECTED_ACCT,
    DEFAULT_ACCOUNT_SCAN_INTERVAL,
    DEFAULT_CLIENT_TRACKER_MODE,
    DEFAULT_EQUIPMENT_SCAN_INTERVAL,
    DEFAULT_NETWORK_SCAN_INTERVAL,
    DOMAIN,
    MAX_ACCOUNT_SCAN_INTERVAL,
    MAX_EQUIPMENT_SCAN_INTERVAL,
    MAX_NETWORK_SCAN_INTERVAL,
    MIN_ACCOUNT_SCAN_INTERVAL,
    MIN_EQUIPMENT_SCAN_INTERVAL,
    MIN_NETWORK_SCAN_INTERVAL,
    OPT_ACCOUNT_SCAN_INTERVAL,
    OPT_CLIENT_TRACKER_MODE,
    OPT_EQUIPMENT_SCAN_INTERVAL,
    OPT_NETWORK_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

_REDIRECT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_REDIRECT_URL): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
    }
)


def _extract_status_account_fallbacks(
    status_data: dict[str, Any],
) -> tuple[str | None, str | None, str | None]:
    viewer = status_data.get("viewer")
    account = viewer.get("account") if isinstance(viewer, dict) else None
    if not isinstance(account, dict):
        return None, None, None
    metadata = account.get("accountMetadata")
    meta = metadata if isinstance(metadata, dict) else {}
    acct_num = str(meta["accountNumber"]).strip() if meta.get("accountNumber") else None
    div_id = str(meta["divisionId"]).strip() if meta.get("divisionId") else None
    fallback_id = str(meta["billingId"]).strip() if meta.get("billingId") else None
    return acct_num or None, div_id or None, fallback_id or None


def _build_entry_data(
    tokens: TokenSet, challenge: BrowserAuthChallenge
) -> dict[str, Any]:
    return {
        CONF_ACCESS_TOKEN: tokens.access_token,
        CONF_REFRESH_TOKEN: tokens.refresh_token,
        CONF_ID_TOKEN: tokens.id_token,
        CONF_DEVICE_UUID: challenge.device_uuid,
        CONF_DEVICE_VERIFIER: tokens.device_verifier,
        CONF_CLIENT_ID: challenge.client_id,
        CONF_SELECTED_ACCT: tokens.selected_acct,
    }


def _interval_selector(minimum: int, maximum: int) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="s",
        )
    )


class SpectrumConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._challenge: BrowserAuthChallenge | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry[Any]) -> SpectrumOptionsFlow:
        _ = config_entry
        return SpectrumOptionsFlow()

    def _get_or_create_challenge(
        self, device_uuid: str | None = None
    ) -> BrowserAuthChallenge:
        if self._challenge is None:
            self._challenge = build_browser_login_url(
                client_id=DEFAULT_CLIENT_ID,
                device_uuid=device_uuid,
            )
        return self._challenge

    async def _async_authenticate(
        self,
        redirect_input: str,
        challenge: BrowserAuthChallenge,
    ) -> tuple[TokenSet, str]:
        code, _state = extract_code_from_redirect(redirect_input)
        session = async_get_clientsession(self.hass)
        auth_client = SpectrumAuthClient(session)
        tokens = await auth_client.exchange_code(
            code,
            code_verifier=challenge.code_verifier,
            client_id=challenge.client_id,
            device_uuid=challenge.device_uuid,
        )
        client = SpectrumClient(
            session,
            tokens,
            device_uuid=challenge.device_uuid,
            client_id=challenge.client_id,
        )
        status_data = await client.execute_operation(
            FETCH_ACCOUNT_STATUS,
            {},
            allow_partial=True,
        )
        active_tokens = client.tokens
        fallback_acct, fallback_div, fallback_id = _extract_status_account_fallbacks(
            status_data if isinstance(status_data, dict) else {}
        )
        unique_id = derive_account_unique_id(
            active_tokens.id_token,
            fallback_account_number=fallback_acct,
            fallback_division_id=fallback_div,
            fallback_id=fallback_id,
        )
        return active_tokens, unique_id

    async def _async_process_submission(
        self,
        redirect_input: str,
        challenge: BrowserAuthChallenge,
    ) -> tuple[TokenSet | None, str | None, dict[str, str]]:
        errors: dict[str, str] = {}
        try:
            extract_code_from_redirect(redirect_input)
        except SpectrumAuthError:
            errors[CONF_REDIRECT_URL] = "invalid_redirect"
            return None, None, errors

        try:
            tokens, unique_id = await self._async_authenticate(
                redirect_input, challenge
            )
        except SpectrumAuthError:
            self._challenge = None
            errors["base"] = "invalid_auth"
            return None, None, errors
        except SpectrumConnectionError, SpectrumValidationError, SpectrumError:
            errors["base"] = "cannot_connect"
            return None, None, errors
        except Exception:
            _LOGGER.exception("Unexpected error during Spectrum authentication")
            errors["base"] = "unknown"
            return None, None, errors

        return tokens, unique_id, errors

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        challenge = self._get_or_create_challenge()

        if user_input is not None:
            tokens, unique_id, errors = await self._async_process_submission(
                str(user_input.get(CONF_REDIRECT_URL, "")),
                challenge,
            )
            if tokens is not None and unique_id is not None:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Spectrum",
                    data=_build_entry_data(tokens, challenge),
                )
            challenge = self._get_or_create_challenge()

        return self.async_show_form(
            step_id="user",
            data_schema=_REDIRECT_SCHEMA,
            errors=errors,
            description_placeholders={
                "login_url": challenge.url,
                "redirect_example": f"{challenge.redirect_uri}?code=...",
            },
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> ConfigFlowResult:
        _ = entry_data
        self._challenge = None
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        existing_device_uuid = (
            str(entry.data[CONF_DEVICE_UUID])
            if entry.data.get(CONF_DEVICE_UUID)
            else None
        )
        challenge = self._get_or_create_challenge(device_uuid=existing_device_uuid)

        if user_input is not None:
            tokens, unique_id, errors = await self._async_process_submission(
                str(user_input.get(CONF_REDIRECT_URL, "")),
                challenge,
            )
            if tokens is not None and unique_id is not None:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=_build_entry_data(tokens, challenge),
                )
            challenge = self._get_or_create_challenge(device_uuid=existing_device_uuid)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_REDIRECT_SCHEMA,
            errors=errors,
            description_placeholders={
                "login_url": challenge.url,
                "redirect_example": f"{challenge.redirect_uri}?code=...",
            },
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        existing_device_uuid = (
            str(entry.data[CONF_DEVICE_UUID])
            if entry.data.get(CONF_DEVICE_UUID)
            else None
        )
        challenge = self._get_or_create_challenge(device_uuid=existing_device_uuid)

        if user_input is not None:
            tokens, unique_id, errors = await self._async_process_submission(
                str(user_input.get(CONF_REDIRECT_URL, "")),
                challenge,
            )
            if tokens is not None and unique_id is not None:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=_build_entry_data(tokens, challenge),
                )
            challenge = self._get_or_create_challenge(device_uuid=existing_device_uuid)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_REDIRECT_SCHEMA,
            errors=errors,
            description_placeholders={
                "login_url": challenge.url,
                "redirect_example": f"{challenge.redirect_uri}?code=...",
            },
        )


class SpectrumOptionsFlow(OptionsFlow):
    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    OPT_NETWORK_SCAN_INTERVAL: int(
                        user_input[OPT_NETWORK_SCAN_INTERVAL]
                    ),
                    OPT_EQUIPMENT_SCAN_INTERVAL: int(
                        user_input[OPT_EQUIPMENT_SCAN_INTERVAL]
                    ),
                    OPT_ACCOUNT_SCAN_INTERVAL: int(
                        user_input[OPT_ACCOUNT_SCAN_INTERVAL]
                    ),
                    OPT_CLIENT_TRACKER_MODE: str(user_input[OPT_CLIENT_TRACKER_MODE]),
                },
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        OPT_NETWORK_SCAN_INTERVAL,
                        default=int(
                            options.get(
                                OPT_NETWORK_SCAN_INTERVAL, DEFAULT_NETWORK_SCAN_INTERVAL
                            )
                        ),
                    ): _interval_selector(
                        MIN_NETWORK_SCAN_INTERVAL, MAX_NETWORK_SCAN_INTERVAL
                    ),
                    vol.Required(
                        OPT_EQUIPMENT_SCAN_INTERVAL,
                        default=int(
                            options.get(
                                OPT_EQUIPMENT_SCAN_INTERVAL,
                                DEFAULT_EQUIPMENT_SCAN_INTERVAL,
                            )
                        ),
                    ): _interval_selector(
                        MIN_EQUIPMENT_SCAN_INTERVAL, MAX_EQUIPMENT_SCAN_INTERVAL
                    ),
                    vol.Required(
                        OPT_ACCOUNT_SCAN_INTERVAL,
                        default=int(
                            options.get(
                                OPT_ACCOUNT_SCAN_INTERVAL, DEFAULT_ACCOUNT_SCAN_INTERVAL
                            )
                        ),
                    ): _interval_selector(
                        MIN_ACCOUNT_SCAN_INTERVAL, MAX_ACCOUNT_SCAN_INTERVAL
                    ),
                    vol.Required(
                        OPT_CLIENT_TRACKER_MODE,
                        default=str(
                            options.get(
                                OPT_CLIENT_TRACKER_MODE, DEFAULT_CLIENT_TRACKER_MODE
                            )
                        ),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(CLIENT_TRACKER_MODES),
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            translation_key="client_tracker_mode",
                        )
                    ),
                }
            ),
        )
