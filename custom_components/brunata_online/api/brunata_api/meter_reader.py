from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from custom_components.brunata_online.api.brunata_api.models import ConsumptionValueDto, MappersConfiguration, \
    AllocationUnitResult, MeterResult, ConsumptionResult


class Meter(BaseModel):
    meter_id: int
    meter_number: str
    unit: str
    scale: Optional[float]
    placement: Optional[str]
    meter_type: str
    mounting_date: Optional[datetime]
    dismount_date: Optional[datetime]
    value_category: str
    allocation_unit_code: str
    meter_type_code: int

class ConsumptionReading(BaseModel):
    Meter: Meter
    Values: list[ConsumptionValueDto]

class MeterReader:
    def __init__(self) -> None:
        self.meters: dict[int, Meter] = {}

    def is_configured(self) -> bool:
        return len(self.meters) > 0

    def configure_metadata(self, mappers_config: MappersConfiguration,
                           allocation_units: AllocationUnitResult,
                           meters: MeterResult) -> None:

        for meter in meters.root:
            if meter.meter.superAllocationUnit is None:
                continue
            unit = mappers_config.measurementUnit[meter.meter.unit]
            meter_type = mappers_config.meterType[meter.meter.meterType]
            value_category = mappers_config.allocationUnitMap[meter.meter.allocationUnit]
            self.meters[meter.meter.meterId] = Meter(
                meter_id=meter.meter.meterId,
                meter_number=meter.meter.meterNo,
                unit=unit,
                scale=meter.meter.scale,
                placement=meter.meter.placement,
                meter_type=meter_type,
                mounting_date=meter.meter.mountingDate,
                dismount_date=meter.meter.dismountedDate,
                value_category=value_category,
                allocation_unit_code=meter.meter.allocationUnit,
                meter_type_code=meter.meter.meterType
            )


    def enrich_consumption_data(self, consumption_data: ConsumptionResult) -> list[ConsumptionReading]:
        result = [
            ConsumptionReading(
                Meter=self.meters[line.meter.meterId],
                Values=line.consumptionValues
            )
            for line in consumption_data.consumptionLines
        ]

        return result

    def get_meters(self) -> list[Meter]:
        return list(self.meters.values())