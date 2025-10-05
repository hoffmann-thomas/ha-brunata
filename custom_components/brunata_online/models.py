from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

from custom_components.brunata_online.api.brunata_api.meter_reader import ConsumptionReading


@dataclass
class MeterData:
    """Represents the time series for one meter."""
    meter_id: str
    values: Dict[datetime, float] = field(default_factory=dict)

    def update_from_reading(self, reading) -> None:
        """Merge a reading.Values list into this meter's data."""
        for v in reading.Values:
            key = v.starttime
            self.values[key] = v.value

    def latest_value(self) -> Optional[float]:
        """Return the most recent value if available."""
        if not self.values:
            return None
        latest_date = max(self.values.keys())
        return self.values[latest_date]


@dataclass
class MeterDataSet:
    """Represents all meters known to the coordinator."""
    meters: Dict[str, MeterData] = field(default_factory=dict)

    def update_from_api_result(self, result: list[ConsumptionReading]) -> None:
        """Merge readings from an API response."""
        for reading in result:
            meter_id = str(reading.Meter.meter_id)
            meter = self.meters.get(meter_id)
            if not meter:
                meter = MeterData(meter_id)
                self.meters[meter_id] = meter
            meter.update_from_reading(reading)

    def get_latest_value(self, meter_id: str) -> Optional[float]:
        meter = self.meters.get(meter_id)
        return meter.latest_value() if meter else None

    def get_meter(self, entity_id: str) -> Optional[MeterData]:
        meter = self.meters.get(entity_id)
        return meter