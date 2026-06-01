"""Sensor platform for Brunata Online."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    DOMAIN as RECORDER_DOMAIN,
    StatisticMeanType,
    async_import_statistics,
    get_last_statistics,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import BrunataOnlineDataUpdateCoordinator
from .api.brunata_api.meter_reader import Meter
from .api.const import Consumption
from .const import DOMAIN
from .entity import brunata_device_info
from .models import MeterDataSet

_LOGGER = logging.getLogger(__name__)

# Maps Brunata API unit strings to (HA device class, HA native unit).
# Brunata "units" (varmeenheder) have no standard energy equivalent and are stored as-is.
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
    api_unit = (meter.unit or "").strip()
    if api_unit in _API_UNIT_MAP:
        return _API_UNIT_MAP[api_unit]
    # Brunata's proprietary "units" (varmeenheder) have no HA equivalent.
    # Use no device class so HA does not validate the unit against a unit_class.
    _LOGGER.debug("Meter %s has unrecognised unit %r; storing as-is", meter.meter_id, api_unit)
    return None, api_unit


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Brunata sensors from a config entry."""
    coordinator: BrunataOnlineDataUpdateCoordinator = (
        hass.data[DOMAIN][entry.entry_id]["coordinator"]
    )

    entities = []
    for meter in coordinator.sensors:
        try:
            Consumption(meter.meter_type_code)
        except ValueError:
            _LOGGER.warning(
                "Unknown meter type code %s for meter %s — skipping",
                meter.meter_type_code, meter.meter_id,
            )
            continue
        entities.append(BrunataStatisticsSensor(coordinator, entry, meter))

    async_add_entities(entities)


class BrunataStatisticsSensor(
    CoordinatorEntity[BrunataOnlineDataUpdateCoordinator],
    SensorEntity,
):
    """Long-term statistics sensor for a single Brunata meter.

    Device class and unit are resolved at runtime from the meter's API-reported
    unit string, supporting m³, kWh, GJ/MWh and Brunata's proprietary radiator
    units ("units" / varmeenheder).

    Statistics are imported via async_import_statistics each time the coordinator
    refreshes.  Today's partial-day value is filtered out to prevent the energy
    dashboard from showing negative usage.
    """

    _attr_state_class = SensorStateClass.TOTAL
    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: BrunataOnlineDataUpdateCoordinator,
        entry: ConfigEntry,
        meter: Meter,
    ) -> None:
        super().__init__(coordinator)
        self._meter = meter
        self._entry = entry
        self._attr_unique_id = f"{meter.meter_id}-statistics"
        self._attr_name = f"{meter.value_category} - {meter.placement}"
        self._attr_device_info = brunata_device_info(entry.entry_id)

        device_class, native_unit = _resolve_unit(meter)
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = native_unit

    @property
    def native_value(self) -> float | None:
        data: MeterDataSet = self.coordinator.data
        return data.get_latest_value(str(self._meter.meter_id))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # In HA 2026+, async_add_listener no longer calls the callback
        # immediately on registration — only on subsequent refreshes.
        # Trigger state write and first statistics import explicitly.
        self.async_write_ha_state()
        self.hass.async_create_task(
            self._safe_import_statistics(),
            name=f"brunata_import_initial_{self._meter.meter_id}",
        )

    def async_will_remove_from_hass(self) -> None:
        instance = get_instance(self.hass)
        if instance:
            instance.async_clear_statistics([self.entity_id])

    @callback
    def _handle_coordinator_update(self) -> None:
        """Respond to coordinator data refresh: update state and import statistics."""
        self.async_write_ha_state()
        self.hass.async_create_task(
            self._safe_import_statistics(),
            name=f"brunata_import_{self._meter.meter_id}",
        )

    async def _safe_import_statistics(self) -> None:
        try:
            await self._import_statistics()
        except Exception:
            _LOGGER.exception("Error importing statistics for meter %s", self._meter.meter_id)

    # ── Statistics import ────────────────────────────────────────────────────

    async def _import_statistics(self) -> None:
        meter_data = self.coordinator.data.get_meter(str(self._meter.meter_id))
        if meter_data is None:
            _LOGGER.debug("No data yet for meter %s", self._meter.meter_id)
            return

        last_stat = await self._get_last_stat()

        # Only insert completed Danish days.
        # Each Brunata daily entry covers midnight-to-midnight CEST.
        # A period whose fromDate is k is complete once k + 24 h has passed.
        # This is timezone-agnostic — no hardcoded UTC offsets — and handles
        # both CET (UTC+1) and CEST (UTC+2) correctly.
        now_utc = datetime.now(tz=UTC)

        def _is_complete(ts: datetime) -> bool:
            return ts + timedelta(hours=24) < now_utc

        if last_stat is not None:
            cutoff = datetime.fromtimestamp(last_stat["start"], tz=UTC)
            new_data = {
                k: v for k, v in meter_data.values.items()
                if cutoff < k and _is_complete(k)
            }
        else:
            new_data = {k: v for k, v in meter_data.values.items() if _is_complete(k)}

        if not new_data:
            return

        statistics: list[StatisticData] = []
        total: float = last_stat["sum"] if last_stat else 0.0
        for ts, value in sorted(new_data.items()):
            total += value
            statistics.append(StatisticData(start=ts, sum=total))

        async_import_statistics(self.hass, self._statistics_metadata(), statistics)

    async def _get_last_stat(self) -> dict | None:
        last = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, self.entity_id, True, {"sum"}
        )
        rows = last.get(self.entity_id, [])
        return rows[0] if rows else None

    def _statistics_metadata(self) -> StatisticMetaData:
        # unit_class groups units into comparable categories for the energy dashboard.
        _unit_class_map = {
            SensorDeviceClass.ENERGY: "energy",
            SensorDeviceClass.WATER:  "volume",
        }
        return StatisticMetaData(
            name=self._attr_name,
            source=RECORDER_DOMAIN,
            statistic_id=self.entity_id,
            unit_of_measurement=self._attr_native_unit_of_measurement,
            unit_class=_unit_class_map.get(self._attr_device_class),
            has_mean=False,
            has_sum=True,
            mean_type=StatisticMeanType.NONE,
        )
