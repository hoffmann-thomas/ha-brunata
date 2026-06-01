"""
Custom integration to integrate Brunata Online with Home Assistant.

For more details about this integration, please refer to
https://github.com/YukiElectronics/ha-brunata
"""

import asyncio
import logging

import aiohttp
from aiohttp import CookieJar
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.brunata_online.api.brunata_api.client import BrunataClient
from .coordinator import BrunataOnlineDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__package__)

from .const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
    PLATFORMS,
    SCAN_INTERVAL,
    STARTUP_MESSAGE,
)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up this integration using YAML is not supported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up this integration using UI."""
    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})
        _LOGGER.info(STARTUP_MESSAGE)

    username = entry.data.get(CONF_USERNAME)
    password = entry.data.get(CONF_PASSWORD)

    # Create a dedicated session with an unsafe cookie jar — required for the
    # Brunata B2C auth flow which uses cross-domain cookies. HA's shared session
    # cannot be customised, so we own this session and close it on unload.
    session = aiohttp.ClientSession(
        cookie_jar=CookieJar(unsafe=True, quote_cookie=False)
    )

    client = BrunataClient(username, password, session, "en")
    sensors = await client.get_meters()

    coordinator = BrunataOnlineDataUpdateCoordinator(hass, client=client, sensors_result=sensors)
    await coordinator.async_refresh()

    if not coordinator.last_update_success:
        await session.close()
        raise ConfigEntryNotReady

    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator, "session": session}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.add_update_listener(async_reload_entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: BrunataOnlineDataUpdateCoordinator = entry_data["coordinator"]
    session: aiohttp.ClientSession = entry_data["session"]

    unloaded = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, platform)
                for platform in PLATFORMS
                if platform in coordinator.platforms
            ]
        )
    )
    if unloaded:
        await session.close()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
