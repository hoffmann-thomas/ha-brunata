import logging
from datetime import timedelta, datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.brunata_online.api.brunata_api.client import BrunataClient
from custom_components.brunata_online.api.brunata_api.meter_reader import Meter
from custom_components.brunata_online.api.const import Interval, Consumption

from .const import (
    DOMAIN,
    SCAN_INTERVAL,
)
from .models import MeterDataSet

_LOGGER: logging.Logger = logging.getLogger(__package__)

# The Brunata v2 API returns at most ~31 days per request.
_API_CHUNK_DAYS = 30


class BrunataOnlineDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: BrunataClient,
        sensors_result: list[Meter]
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.api = client
        self.platforms = []
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

    async def _async_update_data(self):
        """Fetch consumption data in 30-day chunks to respect the API's response limit."""
        try:
            end = datetime.today()
            start = self._last_data_end if self._last_data_end is not None else (end - timedelta(days=365))

            merged = self.data or MeterDataSet()

            # Iterate through 30-day chunks so we never miss data
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
        except Exception as exception:
            raise UpdateFailed() from exception
