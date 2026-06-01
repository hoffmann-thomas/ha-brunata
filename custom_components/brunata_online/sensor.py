"""Sensor platform for Brunata Online."""
import logging
from datetime import datetime, UTC, timedelta

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

    Historical (completed) days are stored as daily statistics.
    Today's running total is stamped at the current UTC hour and updated on
    every poll, giving hourly intra-day resolution in the energy dashboard.
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
        """Clear any corrupted statistics on startup so they re-import cleanly."""
        await get_instance(self.hass).async_clear_statistics([self.entity_id])

    async def async_will_remove_from_hass(self) -> None:
        await get_instance(self.hass).async_clear_statistics([self.entity_id])

    async def async_update(self):
        now_utc = datetime.now(tz=UTC)
        yesterday_midnight_utc = now_utc.replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=1)

        # Use the last *completed-day* stat as the baseline so today's hourly
        # entries do not interfere with historical data insertion.
        base_stat = await self._get_base_stat(self.hass, before=yesterday_midnight_utc)
        self.hass.async_create_task(self._update_data(base_stat))
        self.async_write_ha_state()

    async def _update_data(self, base_stat):
        """Insert completed-day statistics and update today's hourly entry."""
        data: MeterDataSet = self.coordinator.data
        meter = data.get_meter(str(self._meter.meter_id))
        if meter is None:
            _LOGGER.debug("No coordinator data yet for meter %s", self._meter.meter_id)
            return

        now_utc = datetime.now(tz=UTC)
        today_midnight_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_midnight_utc = today_midnight_utc - timedelta(days=1)
        current_hour_utc = now_utc.replace(minute=0, second=0, microsecond=0)

        # ── Part 1: completed days ───────────────────────────────────────────
        # Brunata daily data is timestamped at CEST midnight (= 22:00 UTC previous day).
        # Filtering k < yesterday_midnight_utc naturally excludes today's Danish day
        # (stored at 22:00 UTC yesterday, which is AFTER yesterday midnight UTC)
        # while including all prior completed days.
        if base_stat is not None:
            data_cutoff = datetime.fromtimestamp(base_stat["start"], tz=UTC)
            historical = {k: v for k, v in meter.values.items()
                          if data_cutoff < k < yesterday_midnight_utc}
        else:
            historical = {k: v for k, v in meter.values.items()
                          if k < yesterday_midnight_utc}

        if historical:
            await self._insert_statistics(historical, base_stat)
            # Refresh base_stat so today's hourly entry builds on the latest sum
            base_stat = await self._get_base_stat(self.hass, before=yesterday_midnight_utc)

        # ── Part 2: today's hourly entry ─────────────────────────────────────
        # Today's Danish partial sits in [yesterday_midnight_utc, today_midnight_utc).
        today_data = {k: v for k, v in meter.values.items()
                      if yesterday_midnight_utc <= k < today_midnight_utc}
        if not today_data:
            return

        today_value = sum(today_data.values())
        base_sum = base_stat["sum"] if base_stat else 0

        metadata = self._make_metadata()
        async_import_statistics(
            self.hass,
            metadata,
            [StatisticData(start=current_hour_utc, sum=base_sum + today_value)],
        )

    # ── Helpers ─────────────────────────────────────────────────────────────

    async def _get_base_stat(self, hass: HomeAssistant, before: datetime):
        """Return the most recent statistic whose start timestamp is before *before*."""
        last_stats = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 20, self.entity_id, True, {"sum"}
        )
        for stat in last_stats.get(self.entity_id, []):
            if datetime.fromtimestamp(stat["start"], tz=UTC) < before:
                return stat
        return None

    def _make_metadata(self) -> StatisticMetaData:
        return StatisticMetaData(
            name=self._attr_name,
            source=RECORDER_DOMAIN,
            statistic_id=self.entity_id,
            unit_of_measurement=self._attr_native_unit_of_measurement,
            has_mean=False,
            has_sum=True,
            mean_type=StatisticMeanType.NONE,
        )

    async def _insert_statistics(self, new_data: dict[datetime, float], base_stat):
        statistics: list[StatisticData] = []
        total = base_stat["sum"] if base_stat is not None else 0

        for time, value in sorted(new_data.items()):
            total += value
            statistics.append(StatisticData(start=time, sum=total))

        if statistics:
            async_import_statistics(self.hass, self._make_metadata(), statistics)
