"""Sensor platform for Brunata Online."""
import logging
from datetime import datetime, UTC

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    DOMAIN as RECORDER_DOMAIN,
    StatisticMeanType,
    async_import_statistics,
    get_last_statistics,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass, SensorEntity
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant

from .coordinator import BrunataOnlineDataUpdateCoordinator
from .api.brunata_api.meter_reader import Meter
from .api.const import Consumption
from .const import DOMAIN
from .models import MeterDataSet

_LOGGER = logging.getLogger(__name__)

# Map from Brunata API unit strings to (HA device class, HA native unit).
# Brunata "units" (varmeenheder) are a proprietary radiator allocation unit
# with no direct energy equivalent — they are stored as-is.
_API_UNIT_MAP: dict[str, tuple[SensorDeviceClass, str]] = {
    "m³":  (SensorDeviceClass.WATER,  UnitOfVolume.CUBIC_METERS),
    "m3":  (SensorDeviceClass.WATER,  UnitOfVolume.CUBIC_METERS),
    "GJ":  (SensorDeviceClass.ENERGY, UnitOfEnergy.GIGA_JOULE),
    "MJ":  (SensorDeviceClass.ENERGY, UnitOfEnergy.MEGA_JOULE),
    "kWh": (SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
    "KWh": (SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
    "Wh":  (SensorDeviceClass.ENERGY, UnitOfEnergy.WATT_HOUR),
    "MWh": (SensorDeviceClass.ENERGY, UnitOfEnergy.MEGA_WATT_HOUR),
}


def _resolve_unit(meter: Meter) -> tuple[SensorDeviceClass | None, str]:
    """Return (device_class, native_unit) for a meter based on its API unit string."""
    api_unit = (meter.unit or "").strip()
    if api_unit in _API_UNIT_MAP:
        return _API_UNIT_MAP[api_unit]
    # Unknown unit (e.g. Brunata's proprietary radiator "units") — keep the
    # raw string so the value is displayed correctly even if the energy
    # dashboard cannot aggregate it as energy.
    _LOGGER.debug("Meter %s has unrecognised unit %r; storing as-is", meter.meter_id, api_unit)
    return None, api_unit


async def async_setup_entry(hass, entry, async_add_devices):
    """Setup sensor platform."""
    coordinator: BrunataOnlineDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    sensors = []
    for s in coordinator.sensors:
        try:
            Consumption(s.meter_type_code)
        except ValueError:
            _LOGGER.warning("Unknown meter type code %s for meter %s, skipping", s.meter_type_code, s.meter_id)
            continue
        sensors.append(BrunataStatisticsSensor(coordinator, entry, s))

    async_add_devices(sensors)


class BrunataStatisticsSensor(SensorEntity):
    """Statistics sensor for a single Brunata meter.

    Device class and unit are resolved at runtime from the meter's API-reported
    unit string, so the sensor works correctly for water (m³), electricity (kWh),
    district heating (GJ/MWh) and Brunata's proprietary radiator units alike.
    """

    _attr_state_class = SensorStateClass.TOTAL
    _attr_should_poll = True

    def __init__(self, coordinator: BrunataOnlineDataUpdateCoordinator, entry, meter: Meter):
        self.coordinator = coordinator
        self._meter = meter
        self._attr_unique_id = f"{meter.meter_id}-statistics"
        self._attr_name = f"Brunata {meter.value_category} - {meter.placement}"

        device_class, native_unit = _resolve_unit(meter)
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = native_unit

    @property
    def native_value(self):
        data: MeterDataSet = self.coordinator.data
        return data.get_latest_value(str(self._meter.meter_id))

    async def async_added_to_hass(self) -> None:
        """Clear any corrupted statistics on startup so they get re-imported cleanly."""
        await get_instance(self.hass).async_clear_statistics([self.entity_id])

    async def async_will_remove_from_hass(self) -> None:
        await get_instance(self.hass).async_clear_statistics([self.entity_id])

    async def async_update(self):
        last_stat = await self._get_last_stat(self.hass)
        self.hass.async_create_task(self._update_data(last_stat))
        self.async_write_ha_state()

    async def _update_data(self, last_stat):
        data: MeterDataSet = self.coordinator.data
        meter = data.get_meter(str(self._meter.meter_id))
        if meter is None:
            _LOGGER.debug("No coordinator data yet for meter %s", self._meter.meter_id)
            return

        # Only insert completed days. Today's partial value fluctuates and, when
        # inserted with a lower cumulative than the previous day's final entry,
        # causes the energy dashboard to show negative usage.
        today_midnight_utc = datetime.now(tz=UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        if last_stat is not None:
            data_cutoff = datetime.fromtimestamp(last_stat["start"], tz=UTC)
            new_data = {k: v for k, v in meter.values.items()
                        if k > data_cutoff and k < today_midnight_utc}
        else:
            new_data = {k: v for k, v in meter.values.items() if k < today_midnight_utc}

        if new_data:
            await self._insert_statistics(new_data, last_stat)

    async def _get_last_stat(self, hass: HomeAssistant):
        last_stats = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, self.entity_id, True, {"sum"}
        )
        if self.entity_id in last_stats and len(last_stats[self.entity_id]) > 0:
            return last_stats[self.entity_id][0]
        return None

    async def _insert_statistics(self, new_data: dict[datetime, float], last_stat):
        statistics: list[StatisticData] = []
        total = last_stat["sum"] if last_stat is not None else 0

        for time, value in sorted(new_data.items()):
            total += value
            statistics.append(StatisticData(start=time, sum=total))

        metadata = StatisticMetaData(
            name=self._attr_name,
            source=RECORDER_DOMAIN,
            statistic_id=self.entity_id,
            unit_of_measurement=self._attr_native_unit_of_measurement,
            has_mean=False,
            has_sum=True,
            mean_type=StatisticMeanType.NONE,
        )
        if statistics:
            async_import_statistics(self.hass, metadata, statistics)
