"""Adds config flow for Brunata Online."""
import logging

import aiohttp
from aiohttp import CookieJar
from homeassistant import config_entries
from homeassistant.core import callback
import voluptuous as vol

from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN, PLATFORMS

_LOGGER: logging.Logger = logging.getLogger(__package__)


class BrunataOnlineFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for brunata_online."""

    VERSION = 1

    def __init__(self):
        self._errors = {}

    async def async_step_user(self, user_input=None):
        self._errors = {}

        if user_input is not None:
            valid = await self._test_credentials(
                user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if valid:
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME], data=user_input
                )
            self._errors["base"] = "auth"
            return await self._show_config_form(user_input)

        return await self._show_config_form(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return BrunataOnlineOptionsFlowHandler()

    async def _show_config_form(self, user_input):
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
            ),
            errors=self._errors,
        )

    async def _test_credentials(self, username: str, password: str) -> bool:
        """Validate credentials by checking Keycloak authentication only.
        Consumer API access is verified later when the integration sets up."""
        from .api.brunata_api.api2 import BrunataApi
        session = aiohttp.ClientSession(
            cookie_jar=CookieJar(unsafe=True, quote_cookie=False)
        )
        try:
            api = BrunataApi(username, password, session)
            return await api._get_tokens()
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("Credential validation failed: %s", err)
            return False
        finally:
            await session.close()


class BrunataOnlineOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow handler for brunata_online."""

    # config_entry is injected automatically by HA — do not set it in __init__

    async def async_step_init(self, user_input=None):
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title=self.config_entry.data.get(CONF_USERNAME), data=user_input
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(x, default=self.config_entry.options.get(x, True)): bool
                    for x in sorted(PLATFORMS)
                }
            ),
        )
