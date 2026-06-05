from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from custom_components.brunata_online.api.brunata_api.meter_reader import (
    ConsumptionReading,
)


@dataclass
class MeterData:
    """Time-series data for one meter."""

    meter_id: str
    values: dict[datetime, float] = field(default_factory=dict)

    def update_from_reading(self, reading: ConsumptionReading) -> None:
        for v in reading.Values:
            if v.consumption is None:
                continue
            dt = v.fromDate
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            self.values[dt] = v.consumption

    def latest_value(self) -> float | None:
        if not self.values:
            return None
        return self.values[max(self.values)]


@dataclass
class MeterDataSet:
    """All meters known to the coordinator."""

    meters: dict[str, MeterData] = field(default_factory=dict)

    def update_from_api_result(self, result: list[ConsumptionReading]) -> None:
        for reading in result:
            meter_id = str(reading.Meter.meter_id)
            meter = self.meters.setdefault(meter_id, MeterData(meter_id))
            meter.update_from_reading(reading)

    def get_latest_value(self, meter_id: str) -> float | None:
        meter = self.meters.get(meter_id)
        return meter.latest_value() if meter else None

    def get_meter(self, meter_id: str) -> MeterData | None:
        return self.meters.get(meter_id)
