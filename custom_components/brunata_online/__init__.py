"""Brunata Online integration for Home Assistant."""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp import CookieJar
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import get_last_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from custom_components.brunata_online.api.brunata_api.client import BrunataClient
from .coordinator import BrunataOnlineDataUpdateCoordinator
from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN, PLATFORMS, STARTUP_MESSAGE

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def _statistics_need_reset(hass: HomeAssistant, stat_ids: list[str]) -> bool:
    """Return True if any series has non-monotonic sums or is completely missing.

    get_last_statistics returns rows newest-first.  For consumption stats
    the sum must be non-increasing going from newest→oldest.  A strictly
    increasing value in that direction means a corrupted zero-baseline entry
    was written at the wrong timestamp.

    An empty result means the series was cleared externally; a full reimport
    is needed in that case too.
    """
    instance = get_instance(hass)
    if not instance:
        return False

    for stat_id in stat_ids:
        rows_by_id = await instance.async_add_executor_job(
            get_last_statistics, hass, 10, stat_id, True, {"sum"}
        )
        rows = rows_by_id.get(stat_id, [])

        if not rows:
            _LOGGER.debug("No statistics found for %s — will do full reimport", stat_id)
            return True

        for i in range(len(rows) - 1):
            # rows[i] is newer, rows[i+1] is older → newer sum must be ≥ older sum
            if rows[i]["sum"] < rows[i + 1]["sum"]:
                _LOGGER.warning(
                    "Corrupted statistics in %s: sum %.3f at ts=%d is less than "
                    "sum %.3f at ts=%d — will clear and reimport",
                    stat_id,
                    rows[i]["sum"],
                    rows[i]["start"],
                    rows[i + 1]["sum"],
                    rows[i + 1]["start"],
                )
                return True

    return False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Brunata Online from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    if not hass.data[DOMAIN]:
        _LOGGER.info(STARTUP_MESSAGE)

    session = aiohttp.ClientSession(
        cookie_jar=CookieJar(unsafe=True, quote_cookie=False)
    )

    # Verify we can reach the API and discover meters.  This is the only
    # network call that blocks entry setup — everything else runs in the
    # background task below.
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

    coordinator = BrunataOnlineDataUpdateCoordinator(
        hass, client=client, sensors_result=meters
    )

    # If existing statistics look corrupted, clear them so the background
    # import starts with a clean slate.
    instance = get_instance(hass)
    if instance:
        registry = er.async_get(hass)
        stat_ids = [
            e.entity_id
            for e in registry.entities.get_entries_for_config_entry_id(entry.entry_id)
        ]
        if stat_ids and await _statistics_need_reset(hass, stat_ids):
            _LOGGER.info(
                "Clearing corrupted statistics for %d series — background task will reimport",
                len(stat_ids),
            )
            instance.async_clear_statistics(stat_ids)

    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator, "session": session}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Fetch the full consumption history in the background so it does not
    # delay entity registration or HA startup.  The task is tied to this
    # config entry and is cancelled automatically on unload.
    entry.async_create_background_task(
        hass,
        coordinator.async_import_full_history(),
        name=f"brunata_history_{entry.entry_id}",
    )

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
