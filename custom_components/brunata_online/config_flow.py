"""Config flow for Brunata Online."""
from __future__ import annotations

import logging

import aiohttp
from aiohttp import CookieJar
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
import voluptuous as vol

from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN

_LOGGER: logging.Logger = logging.getLogger(__package__)


class BrunataOnlineFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Brunata Online."""

    VERSION = 1

    def __init__(self) -> None:
        self._errors: dict[str, str] = {}

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        self._errors = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]

            # Prevent duplicate entries for the same Brunata account.
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()

            if await self._test_credentials(username, user_input[CONF_PASSWORD]):
                return self.async_create_entry(title=username, data=user_input)
            self._errors["base"] = "auth"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
            ),
            errors=self._errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> BrunataOnlineOptionsFlowHandler:
        return BrunataOnlineOptionsFlowHandler()

    async def _test_credentials(self, username: str, password: str) -> bool:
        """Validate by attempting Keycloak authentication — does not check consumer access."""
        from .api.brunata_api.api2 import BrunataApi
        session = aiohttp.ClientSession(
            cookie_jar=CookieJar(unsafe=True, quote_cookie=False)
        )
        try:
            api = BrunataApi(username, password, session)
            return await api._get_tokens()
        except Exception as err:
            _LOGGER.debug("Credential validation failed: %s", err)
            return False
        finally:
            await session.close()


class BrunataOnlineOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow — currently no user-configurable options."""

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        return await self.async_step_user()

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=self.config_entry.data.get(CONF_USERNAME, ""),
                data=user_input,
            )
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))
