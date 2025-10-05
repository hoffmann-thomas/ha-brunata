from datetime import datetime
from typing import Optional

from aiohttp import ClientSession
from pydantic import BaseModel, RootModel
from typing_extensions import NamedTuple

from libs.brunata.const import Interval

class AllocationUnit(BaseModel):
    superAllocationUnit: int
    allocationUnits: list[str]

class AllocationUnitResult(RootModel[list[AllocationUnit]]):
    pass

class MappersConfiguration(BaseModel):
    meterType: list[Optional[str]]
    measurementUnit: list[Optional[str]]
    allocationUnitMap: dict[str,str]

class Configuration(BaseModel):
    mappers: MappersConfiguration

class MeterUnitResult(NamedTuple):
    water_units: list[str]
    heating_units: list[str]
    power_units: list[str]

class ApiConfiguration(NamedTuple):
    meter_types: list[str]
    meter_units: list[str]
    allocation_units: list[str]

class MeterDto(BaseModel):
    meterId: int
    placement: Optional[str]
    meterNo: str
    meterType: int
    scale: Optional[float]
    unitfactor: Optional[str]
    mountingDate: datetime
    dismountedDate: Optional[datetime]
    transmitting: bool
    allocationUnit: str
    superAllocationUnit: Optional[int]
    unit: int
    meterSequenceNo: int
    numerator: Optional[str]
    denominator: Optional[str]

class Reading(BaseModel):
    readingId: Optional[int]
    readingDate: Optional[datetime]
    value: Optional[float]

class MeterConfiguration(BaseModel):
    meter: MeterDto
    reading: Reading

class MeterResult(RootModel[list[MeterConfiguration]]):
    pass

class ConsumptionValueDto(BaseModel):
    fromDate: datetime
    toDate: datetime
    consumption: Optional[float]

class ConsumptionLine(BaseModel):
    meter: MeterDto
    consumptionValues: list[ConsumptionValueDto]

class ConsumptionResult(BaseModel):
    startDate: datetime
    endDate: datetime
    interval: str
    consumptionLines: list[ConsumptionLine]

class BrunataClientConfiguration(NamedTuple):
    username: str
    password: str
    session: ClientSession
    locale: str

