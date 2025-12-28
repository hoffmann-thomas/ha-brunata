"""Sensor platform for Brunata Online."""
import logging
from datetime import datetime, timedelta, UTC
from zoneinfo import ZoneInfo

import pytz
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    DOMAIN as RECORDER_DOMAIN, StatisticsRow,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass, SensorEntity
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.util import Throttle

from . import BrunataOnlineDataUpdateCoordinator
from .api.brunata_api.meter_reader import Meter
from .api.const import Consumption
from .api.models import TimeSeries
from .const import DEFAULT_NAME, DOMAIN, ICON, SCAN_INTERVAL, SENSOR
from .entity import BrunataOnlineEntity
from .models import MeterDataSet

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_devices):
    """Setup sensor platform."""
    coordinator: BrunataOnlineDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    sensors = []

    for s in coordinator.sensors:
        match s.meter_type_code:
            case Consumption.WATER:
                e = BrunataWaterStatisticsSensor(coordinator, entry, s)
            case Consumption.HEATING:
                e = BrunataHeatingStatisticsSensor(coordinator, entry, s)
            case Consumption.ELECTRICITY:
                e = BrunataEnergyStatisticsSensor(coordinator, entry, s)
        sensors.append(e)

    async_add_devices(sensors)

class ConsumptionSensor(SensorEntity):
    """Representation of a consumption sensor."""
    def __init__(self, coordinator, entry, meter: Meter):
        pass

class StatisticsSensor(SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: BrunataOnlineDataUpdateCoordinator, entry, meter: Meter):
        self.coordinator = coordinator

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup callback to remove statistics when deleting entity"""
        await get_instance(self.hass).async_clear_statistics([self.entity_id])

    async def async_update(self):
        last_stat = await self._get_last_stat(self.hass)
        self.hass.async_create_task(self._update_data(last_stat))

    async def _update_data(self, last_stat: StatisticData | None):
        data: MeterDataSet = self.coordinator.data
        data_cutoff = pytz.utc.localize(datetime.fromtimestamp(last_stat["start"], UTC))
        meter = data.get_meter(self.entity_id)
        if meter is None: return
        #new_data = {(time, value) for time, value in meter.values if time > data_cutoff}
        new_data = {k: v for k, v in meter.values.items() if k > data_cutoff}
        if new_data is not None:
            await self._insert_statistics(new_data, last_stat)


    async def _get_last_stat(self, hass: HomeAssistant) -> StatisticsRow | None:
        last_stats = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, self.entity_id, True, {"sum"}
        )

        if self.entity_id in last_stats and len(last_stats[self.entity_id]) > 0:
            return last_stats[self.entity_id][0]
        else:
            return None

    async def _insert_statistics(self, new_data: dict[datetime, float], last_stat: StatisticData | None):
        statistics: list[StatisticData] = []
        if last_stat is not None:
            total = last_stat["sum"]
        else:
            total = 0

        sorted_data = sorted(new_data)
        for (time, value) in sorted_data:
            total += value
            statistics.append(
                StatisticData(
                    start=time,
                    sum=total
                )
            )

        metadata = StatisticMetaData(
            name=self._attr_name,
            source=RECORDER_DOMAIN,
            statistic_id=self.entity_id,
            unit_of_measurement=self.unit_of_measurement,
            has_mean=False,
            has_sum=True
        )
        if len(statistics) > 0:
            async_import_statistics(self.hass, metadata, statistics)


class WaterSensor(SensorEntity):
    _attr_device_class = SensorDeviceClass.WATER
    _attr_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    pass

class EnergySensor(SensorEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    pass

class HeatingSensor(SensorEntity):
    _attr_device_class = SensorDeviceClass.GAS
    pass

class BrunataWaterStatisticsSensor(WaterSensor, StatisticsSensor):
    def __init__(self, coordinator, entry, meter: Meter):
        super().__init__(coordinator, entry)
        self.attr_name = f"Brunata {meter.value_category} - {meter.placement}"
        self._attr_unique_id = f"{meter.meter_id}-statistics"
        self._meter = meter

class BrunataEnergyStatisticsSensor(EnergySensor, StatisticsSensor):
    def __init__(self, coordinator, entry, meter: Meter):
        super().__init__(coordinator, entry)
        self.attr_name = f"Brunata {meter.value_category} - {meter.placement}"
        self._attr_unique_id = f"{meter.meter_id}-statistics"
        self._meter = meter

class BrunataHeatingStatisticsSensor(HeatingSensor, StatisticsSensor):
    def __init__(self, coordinator, entry, meter: Meter):
        super().__init__(coordinator, entry)
        self.attr_name = f"Brunata {meter.value_category} - {meter.placement}"
        self._attr_unique_id = f"{meter.meter_id}-statistics"
        self._meter = meter

class BrunataOnlineEnergySensor(BrunataOnlineEntity):
    """Energy Sensor"""

    _attr_name = "Brunata Energy Consumed"
    _attr_native_unit_of_measurement = UnitOfEnergy
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{DEFAULT_NAME}_{SENSOR}"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self.coordinator.data.get("body")

    @property
    def icon(self):
        """Return the icon of the sensor."""
        return ICON

    @property
    def device_class(self):
        """Return the device class of the sensor."""
        return "brunata_online__custom_device_class"


class BrunataOnlineWaterSensor(BrunataOnlineEntity):
    """brunata_online Sensor class."""

    _attr_name = "Brunata Water Consumed"
    _attr_native_unit_of_measurement = UnitOfVolume
    _attr_device_class = SensorDeviceClass.VOLUME
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{DEFAULT_NAME}_{SENSOR}"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self.coordinator.data.get("body")

    @property
    def icon(self):
        """Return the icon of the sensor."""
        return ICON

    @property
    def device_class(self):
        """Return the device class of the sensor."""
        return "brunata_online__custom_device_class"


class BrunataOnlineHeatingSensor(BrunataOnlineEntity):
    """brunata_online Sensor class."""

    _attr_name = "Brunata Energy Consumed"
    _attr_native_unit_of_measurement = UnitOfEnergy
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{DEFAULT_NAME}_{SENSOR}"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self.coordinator.data.get("body")

    @property
    def icon(self):
        """Return the icon of the sensor."""
        return ICON

    @property
    def device_class(self):
        """Return the device class of the sensor."""
        return "brunata_online__custom_device_class"


class BrunataWaterStatistics(BrunataOnlineEntity):
    """This class handles the total energy of the meter,
    and imports it as long term statistics from Brunata Online."""

    _attr_name = "Brunata Energy Consumed"
    _attr_native_unit_of_measurement = UnitOfEnergy
    _attr_device_class = SensorDeviceClass.VOLUME
    _attr_state_class = SensorStateClass.TOTAL

    sensor: BrunataOnlineWaterSensor

    def __init__(self, water_sensor: BrunataOnlineWaterSensor, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{water_sensor.name}-statistic"
        self.sensor = water_sensor

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup callback to remove statistics when deleting entity"""
        await get_instance(self.hass).async_clear_statistics([self.entity_id])

    @Throttle(SCAN_INTERVAL)
    async def async_update(self):
        """Continually update history"""
        last_stat = await self._get_last_stat(self.hass)  # Last time we have data for

        if last_stat is not None and pytz.utc.localize(datetime.now()) - pytz.utc.localize(datetime.fromtimestamp(last_stat["start"], datetime.timezone.utc)) < timedelta(days=1):
            # If less than 1 day since last record, don't pull new data.
            # Data is available at the earliest a day after.
            return

        # Create a job to fetch new data

        self.hass.async_create_task(self._update_data(last_stat))

    async def _update_data(self, last_stat: StatisticData):
        if last_stat is None:
            # if none import from last january
            from_date = datetime(datetime.today().year - 1, 1, 1)
        else:
            # Next day at noon (eloverblik.py will strip time)
            from_date = ZoneInfo.fromutc(datetime.fromtimestamp(last_stat["start"]))

        data = await self.hass.async_add_executor_job(
            self.sensor.async_update
        )

        if data is not None:
            await self._insert_statistics(data, last_stat)
        else:
            _LOGGER.debug("None data was returned from Eloverblik")

    async def _insert_statistics(
            self,
            data: dict[datetime, TimeSeries],
            last_stat: StatisticData):

        statistics : list[StatisticData] = []

        if last_stat is not None:
            total = last_stat["sum"]
        else:
            total = 0

        # Sort time series to ensure correct insertion
        sorted_time_series = sorted(data.values(), key=lambda timeseries : timeseries.data_date)

        for time_series in sorted_time_series:
            if time_series._metering_data is not None:
                number_of_hours = len(time_series._metering_data)

                # data_date returned is end of the time series
                date = time_series.data_date - timedelta(hours=number_of_hours)

                for hour in range(0, number_of_hours):
                    start = date + timedelta(hours=hour)

                    total += time_series.get_metering_data(hour + 1)

                    statistics.append(
                        StatisticData(
                            start=start,
                            sum=total
                        ))

        metadata = StatisticMetaData(
            name=self._attr_name,
            source=RECORDER_DOMAIN,
            statistic_id=self.entity_id,
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            has_mean=False,
            has_sum=True,
        )

        if len(statistics) > 0:
            async_import_statistics(self.hass, metadata, statistics)

    async def _get_last_stat(self, hass: HomeAssistant) -> StatisticData:
        last_stats = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, self.entity_id, True, {"sum"}
        )

        if self.entity_id in last_stats and len(last_stats[self.entity_id]) > 0:
            return last_stats[self.entity_id][0]
        else:
            return None
