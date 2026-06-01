"""Brunata Online integration for Home Assistant."""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp import CookieJar
from homeassistant.components.recorder import get_instance
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from custom_components.brunata_online.api.brunata_api.client import BrunataClient
from .coordinator import BrunataOnlineDataUpdateCoordinator
from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN, PLATFORMS, STARTUP_MESSAGE

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """YAML setup is not supported; only UI config entries."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Brunata Online from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    if not hass.data[DOMAIN]:
        _LOGGER.info(STARTUP_MESSAGE)

    session = aiohttp.ClientSession(
        cookie_jar=CookieJar(unsafe=True, quote_cookie=False)
    )

    try:
        client = BrunataClient(
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            session,
            "en",
        )
        meters = await client.get_meters()
    except Exception as err:
        await session.close()
        raise ConfigEntryNotReady(f"Failed to connect to Brunata: {err}") from err

    coordinator = BrunataOnlineDataUpdateCoordinator(hass, client=client, sensors_result=meters)
    await coordinator.async_config_entry_first_refresh()

    # Clear any stale or corrupted statistics before entities are added.
    # The recorder dependency guarantees get_instance() is non-None here.
    # We clear on every startup so that a fresh, correct import always follows.
    # The import itself only inserts completed Danish days (fromDate + 24h < now),
    # so the re-import is fast and produces no partial-day entries.
    instance = get_instance(hass)
    if instance:
        registry = er.async_get(hass)
        stale_stat_ids = [
            entity.entity_id
            for entity in registry.entities.get_entries_for_config_entry_id(entry.entry_id)
        ]
        if stale_stat_ids:
            _LOGGER.debug("Clearing statistics for %d entities before reimport", len(stale_stat_ids))
            instance.async_clear_statistics(stale_stat_ids)

    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator, "session": session}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    entry_data: dict = hass.data[DOMAIN].get(entry.entry_id, {})
    session: aiohttp.ClientSession = entry_data.get("session")

    unloaded = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, platform)
                for platform in PLATFORMS
            ]
        )
    )

    if unloaded:
        if session:
            await session.close()
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)
