import logging
from datetime import datetime

from custom_components.brunata_online import BrunataClientConfiguration
from custom_components.brunata_online.api.brunata_api.api2 import BrunataApi
from custom_components.brunata_online.api.brunata_api.meter_reader import MeterReader, ConsumptionReading, Meter
from custom_components.brunata_online.api.const import Interval, Consumption

logging.basicConfig(level=logging.DEBUG)
_LOGGER: logging.Logger = logging.getLogger(__package__)
TIMEOUT = 10

class BrunataClient:

    def __init__(self, configuration: BrunataClientConfiguration) -> None:
        self.api = BrunataApi(configuration.username, configuration.password, configuration.session)
        self.configuration = configuration
        self.meter_reader = MeterReader()

    async def connect(self):
        await self._update_metadata(self.configuration.locale)

    async def _update_metadata(self, locale: str = "en"):
        mapping_config = await self.api.get_mapping_configuration(locale)
        if mapping_config.is_error():
            _LOGGER.error("Failed to retrieve mapping configuration for locale %s", locale)

        allocation_units = await self.api.get_allocation_units()
        if allocation_units.is_error():
            _LOGGER.error("Failed to retrieve allocation units")

        meters = await self.api.get_meters()
        if meters.is_error():
            _LOGGER.error("Failed to retrieve meters")

        self.meter_reader.configure_metadata(mapping_config.value, allocation_units.value, meters.value)


    async def _get_consumption(self, start_date: datetime, end_date: datetime, _type: Consumption, unit: str,
                              interval: Interval):
        result = await self.api.get_consumption(start_date, end_date, _type, unit, interval)
        return result.value

    async def get_consumption(self, start_date: datetime, end_date: datetime, _type: Consumption, unit: str,
                              interval: Interval) -> list[ConsumptionReading]:
        if not self.meter_reader.is_configured():
            await self.connect()
        consumption = await self.api.get_consumption(start_date, end_date, _type, unit, interval)
        if consumption.is_error():
            _LOGGER.error("Failed to retrieve consumption")
        result = self.meter_reader.enrich_consumption_data(consumption.value)
        return result

    async def _get_allocation_units(self):
        result = await self.api.get_allocation_units()
        return result.value

    async def _get_configuration(self):
        result = await self.api.get_mapping_configuration(self.configuration.locale)
        return result.value

    async def _get_meters(self):
        result = await self.api.get_meters()
        return result.value

    async def get_meters(self) -> list[Meter]:
        if self.meter_reader.is_configured() is False:
            await self.connect()
        result = self.meter_reader.get_meters()
        return result