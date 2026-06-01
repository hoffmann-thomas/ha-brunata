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
        self.platforms: list[str] = []
        self.sensors = sensors_result
        self.data = MeterDataSet()
        self._last_data_end: datetime | None = None

        seen = {
            (s.meter_type_code, s.allocation_unit_code)
            for s in sensors_result
        }
        self.queries = [
            {"meter_type_code": type_code, "allocation_unit": alloc_code}
            for type_code, alloc_code in seen
        ]

    async def _async_update_data(self) -> MeterDataSet:
        """Fetch consumption data in 30-day chunks to stay within the API limit."""
        try:
            end = datetime.now(tz=timezone.utc)
            start = self._last_data_end if self._last_data_end is not None else (
                end - timedelta(days=365)
            )

            merged = self.data or MeterDataSet()

            chunk_start = start
            while chunk_start < end:
                chunk_end = min(chunk_start + timedelta(days=_API_CHUNK_DAYS), end)

                for q in self.queries:
                    try:
                        consumption_type = Consumption(q["meter_type_code"])
                    except ValueError:
                        _LOGGER.warning("Unknown meter type code: %s", q["meter_type_code"])
                        continue
                    result = await self.api.get_consumption(
                        chunk_start, chunk_end,
                        consumption_type, q["allocation_unit"],
                        Interval.DAY,
                    )
                    merged.update_from_api_result(result)

                chunk_start = chunk_end

            self._last_data_end = end
            return merged
        except Exception as exc:
            raise UpdateFailed() from exc
