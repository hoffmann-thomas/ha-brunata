from __future__ import annotations

import logging
from datetime import datetime

from aiohttp import ClientSession

from custom_components.brunata_online.api.brunata_api.api2 import BrunataApi
from custom_components.brunata_online.api.brunata_api.meter_reader import (
    ConsumptionReading,
    Meter,
    MeterReader,
)
from custom_components.brunata_online.api.const import Consumption, Interval

_LOGGER: logging.Logger = logging.getLogger(__package__)


class BrunataClient:

    def __init__(self, username: str, password: str, session: ClientSession, locale: str) -> None:
        self.api = BrunataApi(username, password, session)
        self.meter_reader = MeterReader()
        self.locale = locale

    async def connect(self) -> None:
        await self._update_metadata(self.locale)

    async def _update_metadata(self, locale: str = "en") -> None:
        mapping_config = await self.api.get_mapping_configuration(locale)
        if mapping_config.is_error():
            raise RuntimeError(f"Failed to retrieve mapping configuration: {mapping_config.value}")

        allocation_units = await self.api.get_allocation_units()
        if allocation_units.is_error():
            raise RuntimeError(f"Failed to retrieve allocation units: {allocation_units.value}")

        meters = await self.api.get_meters()
        if meters.is_error():
            raise RuntimeError(f"Failed to retrieve meters: {meters.value}")

        self.meter_reader.configure_metadata(
            mapping_config.value, allocation_units.value, meters.value
        )

    async def get_meters(self) -> list[Meter]:
        if not self.meter_reader.is_configured():
            await self.connect()
        return self.meter_reader.get_meters()

    async def get_consumption(
        self,
        start_date: datetime,
        end_date: datetime,
        _type: Consumption,
        unit: str,
        interval: Interval,
    ) -> list[ConsumptionReading]:
        if not self.meter_reader.is_configured():
            await self.connect()
        consumption = await self.api.get_consumption(start_date, end_date, _type, unit, interval)
        if consumption.is_error():
            raise RuntimeError(f"Failed to retrieve consumption: {consumption.value}")
        return self.meter_reader.enrich_consumption_data(consumption.value)
