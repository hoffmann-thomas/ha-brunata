from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.brunata_online.api.brunata_api.client import BrunataClient
from custom_components.brunata_online.api.brunata_api.meter_reader import Meter
from custom_components.brunata_online.api.const import Interval, Consumption

from .const import DOMAIN, SCAN_INTERVAL
from .models import MeterDataSet

# Brunata Online launched digital metering around 2015; earlier dates are safe —
# the API returns null consumption for periods before a meter was registered.
_HISTORY_EPOCH = datetime(2015, 1, 1, tzinfo=timezone.utc)

_LOGGER: logging.Logger = logging.getLogger(__package__)

# The Brunata v2 API returns at most ~31 days per request.
_API_CHUNK_DAYS = 30


class BrunataOnlineDataUpdateCoordinator(DataUpdateCoordinator[MeterDataSet]):
    """Coordinator that fetches consumption data from the Brunata API."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: BrunataClient,
        sensors_result: list[Meter],
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.api = client
        self.sensors = sensors_result
        self.data = MeterDataSet()
        self._last_data_end: datetime | None = None
        self._history_import_complete = False

        seen = {(s.meter_type_code, s.allocation_unit_code) for s in sensors_result}
        self.queries = [
            {"meter_type_code": type_code, "allocation_unit": alloc_code}
            for type_code, alloc_code in seen
        ]

    async def _async_update_data(self) -> MeterDataSet:
        """Fetch new consumption data since the last update (one chunk, incremental)."""
        try:
            end = datetime.now(tz=timezone.utc)
            # On first scheduled poll, fetch only the most recent window.
            # Full history is imported separately via async_import_full_history.
            start = (
                self._last_data_end
                if self._last_data_end is not None
                else end - timedelta(days=_API_CHUNK_DAYS)
            )

            merged = self.data or MeterDataSet()

            for q in self.queries:
                try:
                    consumption_type = Consumption(q["meter_type_code"])
                except ValueError:
                    _LOGGER.warning("Unknown meter type code: %s", q["meter_type_code"])
                    continue
                result = await self.api.get_consumption(
                    start,
                    end,
                    consumption_type,
                    q["allocation_unit"],
                    Interval.DAY,
                )
                merged.update_from_api_result(result)

            self._last_data_end = end
            return merged
        except Exception as exc:
            raise UpdateFailed() from exc

    async def async_import_full_history(self) -> None:
        """Fetch the complete consumption history from 2015 to now.

        Runs as a background task after entity setup so it does not block init.
        Notifies all listeners when done so entities can import the statistics.
        """
        _LOGGER.info(
            "Brunata: starting full history import from %s", _HISTORY_EPOCH.date()
        )
        try:
            end = datetime.now(tz=timezone.utc)
            merged = self.data or MeterDataSet()

            chunk_start = _HISTORY_EPOCH
            while chunk_start < end:
                chunk_end = min(chunk_start + timedelta(days=_API_CHUNK_DAYS), end)

                for q in self.queries:
                    try:
                        consumption_type = Consumption(q["meter_type_code"])
                    except ValueError:
                        _LOGGER.warning(
                            "Unknown meter type code: %s", q["meter_type_code"]
                        )
                        continue
                    try:
                        result = await self.api.get_consumption(
                            chunk_start,
                            chunk_end,
                            consumption_type,
                            q["allocation_unit"],
                            Interval.DAY,
                        )
                        merged.update_from_api_result(result)
                    except Exception:
                        _LOGGER.warning(
                            "Brunata: failed to fetch chunk %s–%s, skipping",
                            chunk_start.date(),
                            chunk_end.date(),
                        )

                chunk_start = chunk_end

            # Mark where we left off so the next periodic poll is incremental.
            self._last_data_end = end
            _LOGGER.info("Brunata: full history import complete")
            # Allow statistics imports to proceed now that the baseline is ready.
            self._history_import_complete = True
            # Notify entities so they trigger their statistics import.
            self.async_set_updated_data(merged)
        finally:
            # Ensure live polls are never blocked permanently if the fetch fails.
            self._history_import_complete = True
