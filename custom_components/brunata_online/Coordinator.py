import logging
from datetime import timedelta, datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.brunata_online.api.brunata_api.client import BrunataClient
from custom_components.brunata_online.api.brunata_api.meter_reader import Meter
from custom_components.brunata_online.api.const import Interval

from .const import (
    DOMAIN,
    SCAN_INTERVAL,
)
from .models import MeterDataSet

_LOGGER: logging.Logger = logging.getLogger(__package__)

class BrunataOnlineDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""
    data = MeterDataSet()

    def __init__(
        self,
        hass: HomeAssistant,
        client: BrunataClient,
        sensors_result: list[Meter]
    ) -> None:
        """Initialize."""
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.api = client
        self.platforms = []
        self.sensors = sensors_result
        #self.data = MeterDataSet()

        q = {
            (s.meter_type, s.value_category)
            for s in sensors_result
        }
        self.queries = [
            {"consumption": value1, "allocation_unit": value2}
            for value1, value2 in q
        ]


    async def _async_update_data(self):
        """Update data via library."""
        try:
            end = datetime.today()
            start = self.last_update_success or (end - timedelta(days=7))

            merged = self.data or MeterDataSet()
            for consumption, unit in self.queries:
                result = await self.api.get_consumption(start, end, consumption, unit, Interval.DAY)

                merged.update_from_api_result(result)

            self.last_update_success = end
            return merged
        except Exception as exception:
            raise UpdateFailed() from exception