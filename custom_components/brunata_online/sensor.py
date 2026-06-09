"""Sensor platform for Brunata Online."""

from __future__ import annotations

import asyncio
import logging
import unicodedata
from datetime import UTC, datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    DOMAIN as RECORDER_DOMAIN,
    StatisticMeanType,
    async_import_statistics,
    get_last_statistics,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
    SensorEntity,
)
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
    "m³": (SensorDeviceClass.WATER, UnitOfVolume.CUBIC_METERS),
    "m3": (SensorDeviceClass.WATER, UnitOfVolume.CUBIC_METERS),
    "GJ": (SensorDeviceClass.ENERGY, UnitOfEnergy.GIGA_JOULE),
    "MJ": (SensorDeviceClass.ENERGY, UnitOfEnergy.MEGA_JOULE),
    "kWh": (SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
    "KWh": (SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
    "Wh": (SensorDeviceClass.ENERGY, UnitOfEnergy.WATT_HOUR),
    "MWh": (SensorDeviceClass.ENERGY, UnitOfEnergy.MEGA_WATT_HOUR),
}


def _resolve_unit(meter: Meter) -> tuple[SensorDeviceClass | None, str]:
    api_unit = unicodedata.normalize("NFC", (meter.unit or "").strip())
    if api_unit in _API_UNIT_MAP:
        return _API_UNIT_MAP[api_unit]
    api_lower = api_unit.casefold()
    for key, val in _API_UNIT_MAP.items():
        if unicodedata.normalize("NFC", key).casefold() == api_lower:
            return val
    # Brunata's proprietary "units" (varmeenheder) have no HA equivalent.
    # Fall back to kWh so the sensor is compatible with the HA energy dashboard.
    _LOGGER.debug(
        "Meter %s has unrecognised unit %r (bytes: %s); falling back to kWh",
        meter.meter_id,
        api_unit,
        api_unit.encode(),
    )
    return SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Brunata sensors from a config entry."""
    coordinator: BrunataOnlineDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    entities = []
    for meter in coordinator.sensors:
        try:
            Consumption(meter.meter_type_code)
        except ValueError:
            _LOGGER.warning(
                "Unknown meter type code %s for meter %s — skipping",
                meter.meter_type_code,
                meter.meter_id,
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
        self._import_lock = asyncio.Lock()

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

    async def async_will_remove_from_hass(self) -> None:
        pass  # Statistics are intentionally preserved; _statistics_need_reset handles recovery

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
            _LOGGER.exception(
                "Error importing statistics for meter %s", self._meter.meter_id
            )

    # ── Statistics import ────────────────────────────────────────────────────

    async def _import_statistics(self) -> None:
        async with self._import_lock:
            await self._do_import_statistics()

    async def _do_import_statistics(self) -> None:
        meter_data = self.coordinator.data.get_meter(str(self._meter.meter_id))
        if meter_data is None:
            _LOGGER.debug("No data yet for meter %s", self._meter.meter_id)
            return

        last_stat = await self._get_last_stat()

        # Fix A+B: if there is no existing baseline in the DB, the history
        # import has not yet run.  A live-poll import starting from sum=0
        # would corrupt the series when history arrives later with older
        # (but correctly accumulated) sums.  Skip until history is done;
        # async_import_full_history sets _history_import_complete=True and
        # then calls async_set_updated_data, which re-triggers this path.
        if last_stat is None and not self.coordinator._history_import_complete:
            _LOGGER.debug(
                "Meter %s: deferring statistics import until history is complete",
                self._meter.meter_id,
            )
            return

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
                k: v
                for k, v in meter_data.values.items()
                if cutoff < k and _is_complete(k)
            }
        else:
            new_data = {k: v for k, v in meter_data.values.items() if _is_complete(k)}

        if not new_data:
            return

        statistics: list[StatisticData] = []
        total: float = last_stat["sum"] if last_stat else 0.0
        for ts, value in sorted(new_data.items()):
            if value < 0:
                _LOGGER.warning(
                    "Meter %s: skipping negative consumption %.4f at %s (API correction artifact)",
                    self._meter.meter_id,
                    value,
                    ts.date(),
                )
                continue
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
        _unit_class_map = {
            SensorDeviceClass.ENERGY: "energy",
            SensorDeviceClass.WATER: "volume",
        }
        unit_class = _unit_class_map.get(self._attr_device_class)
        if (
            unit_class is None
            and self._meter.meter_type_code == Consumption.HEATING.value
        ):
            _LOGGER.debug(
                "Heating meter %s uses unrecognised unit %r — exposing with unit_class='energy'",
                self._meter.meter_id,
                self._attr_native_unit_of_measurement,
            )
            unit_class = "energy"
        return StatisticMetaData(
            name=self._attr_name,
            source=RECORDER_DOMAIN,
            statistic_id=self.entity_id,
            unit_of_measurement=self._attr_native_unit_of_measurement,
            unit_class=unit_class,
            has_mean=False,
            has_sum=True,
            mean_type=StatisticMeanType.NONE,
        )
