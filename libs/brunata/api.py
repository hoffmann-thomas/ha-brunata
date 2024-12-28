"""Brunata Online API Client"""

import base64
import hashlib
import logging
import os
import re
import urllib.parse
from datetime import datetime, timedelta

import asyncio
from socket import gaierror
from aiohttp import ClientResponse, ClientSession, ClientError
from async_timeout import timeout as async_timeout
from requests import Session

from .const import (
    API_URL,
    AUTHN_URL,
    CLIENT_ID,
    CONSUMPTION_URL,
    HEADERS,
    OAUTH2_PROFILE,
    OAUTH2_URL,
    REDIRECT,
    Consumption,
    Interval, METERS_URL,
)

logging.basicConfig(level=logging.DEBUG)
_LOGGER: logging.Logger = logging.getLogger(__package__)
TIMEOUT = 10


def start_of_interval(interval: Interval, offset: timedelta | None) -> str:
    """Returns start of year if interval is "M", otherwise start of month"""
    date = datetime.now()
    if offset is not None:
        date += offset
    if interval is Interval.MONTH:
        date = date.replace(month=1)
    date = date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return f"{date.isoformat()}.000Z"


def end_of_interval(interval: Interval, offset: timedelta | None) -> str:
    """Returns end of year if interval is "M", otherwise end of month"""
    date = datetime.now()
    if offset is not None:
        date += offset
    if interval is Interval.MONTH:
        date = date.replace(month=12)
    date = date.replace(day=28) + timedelta(days=4)
    date -= timedelta(days=date.day)
    date = date.replace(hour=23, minute=59, second=59, microsecond=0)
    return f"{date.isoformat()}.999Z"


class BrunataOnlineApiClient:
    """Brunata Online API Client"""

    def __init__(self, username: str, password: str, session: ClientSession) -> None:
        self._username = username
        self._password = password
        self._session = session
        self._power = {}
        self._water = {}
        self._heating = {}
        self._tokens = {}
        self._meters = {}
        self._meter_types_map = {}
        self._meter_units_map = {}
        self._allocation_unit_map = {}
        self._session.headers.update(HEADERS)

    def _is_token_valid(self, token: str) -> bool:
        if not self._tokens:
            return False
        match token:
            case "access_token":
                ts = self._tokens.get("expires_on")
                if datetime.fromtimestamp(ts) < datetime.now():
                    return False
            case "refresh":
                ts = self._tokens.get("refresh_token_expires_on")
                if datetime.fromtimestamp(ts) < datetime.now():
                    return False
        return True

    async def _renew_tokens(self) -> dict:
        if self._is_token_valid("access_token"):
            _LOGGER.debug(
                "Token is not expired, expires in %d seconds",
                self._tokens.get("expires_on") - int(datetime.now().timestamp()),
            )
            return self._tokens
        # Get OAuth 2.0 token object
        try:
            tokens = await self.api_wrapper(
                method="POST",
                url=f"{OAUTH2_URL}/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._tokens.get("refresh_token"),
                    "CLIENT_ID": CLIENT_ID,
                },
            )
        except Exception as error:  # pylint: disable=broad-except
            _LOGGER.error("An error occurred while trying to renew tokens: %s", error)
            return {}
        return await tokens.json()

    def _b2c_auth(self) -> dict:
        # Initialize challenge values
        code_verifier = base64.urlsafe_b64encode(os.urandom(40)).decode("utf-8")
        code_verifier = re.sub("[^a-zA-Z0-9]+", "", code_verifier)
        code_challenge = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        code_challenge = (
            base64.urlsafe_b64encode(code_challenge).decode("utf-8").replace("=", "")
        )
        with Session() as session:
            # Initial authorization call
            req_code = session.request(
                method="GET",
                url=f"{API_URL.replace('webservice', 'auth-webservice')}/authorize",
                params={
                    "client_id": CLIENT_ID,
                    "redirect_uri": REDIRECT,
                    "scope": f"{CLIENT_ID} offline_access",
                    "response_type": "code",
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                },
            )
            # Get CSRF Token & Transaction ID
            try:
                csrf_token = str(req_code.cookies.get("x-ms-cpim-csrf"))
            except KeyError as exception:
                _LOGGER.error("Error while retrieving CSRF Token: %s", exception)
                return {}
            match = re.search(r"var SETTINGS = (\{[^;]*\});", req_code.text)
            if match:  # Use a little magic to avoid proper JSON parsing ✨
                transaction_id = [
                    i for i in match.group(1).split('","') if i.startswith("transId")
                ][0][10:]
                _LOGGER.debug("Transaction ID: %s", transaction_id)
            else:
                _LOGGER.error("Failed to get Transaction ID")
                return {}
            # Post credentials to B2C Endpoint
            req_auth = session.request(
                method="POST",
                url=f"{AUTHN_URL}/SelfAsserted",
                params={
                    "tx": transaction_id,
                    "p": OAUTH2_PROFILE,
                },
                data={
                    "request_type": "RESPONSE",
                    "logonIdentifier": self._username,
                    "password": self._password,
                },
                headers={
                    "Referer": str(req_code.url),
                    "X-Csrf-Token": csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                },
                allow_redirects=False,
            )
            # Get authentication code
            req_auth = session.request(
                method="GET",
                url=f"{AUTHN_URL}/api/CombinedSigninAndSignup/confirmed",
                params={
                    "rememberMe": str(False),
                    "csrf_token": csrf_token,
                    "tx": transaction_id,
                    "p": OAUTH2_PROFILE,
                },
                allow_redirects=False,
            )
            redirect = req_auth.headers["Location"]
            assert redirect.startswith(REDIRECT)
            _LOGGER.debug("%d - %s", req_auth.status_code, redirect)
            try:
                auth_code = urllib.parse.parse_qs(
                    urllib.parse.urlparse(redirect).query
                )["code"][0]
            except KeyError:
                _LOGGER.error(
                    "An error has occurred while attempting to authenticate. \
                        Please ensure your credentials are correct"
                )
                return {}
            # Get OAuth 2.0 token object
            tokens = session.request(
                method="POST",
                url=f"{OAUTH2_URL}/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "redirect_uri": REDIRECT,
                    "code": auth_code,
                    "code_verifier": code_verifier,
                },
            )
        return tokens.json()

    async def _get_tokens(self) -> bool:
        """
        Get access/refresh tokens using credentials or refresh token
        Returns True if tokens are valid
        """
        # Check values
        if self._is_token_valid("refresh_token"):
            tokens = await self._renew_tokens()
        else:
            tokens = self._b2c_auth()
        # Ensure validity of tokens
        if tokens.get("access_token"):
            # Add access token to session headers
            self._session.headers.update(
                {
                    "Authorization": f"{tokens.get('token_type')} {tokens.get('access_token')}",
                }
            )
            # Calculate refresh expiry
            if tokens.get("refresh_token") != self._tokens.get("refresh_token"):
                tokens.update(
                    {
                        "refresh_token_expires_on": int(datetime.now().timestamp())
                        + tokens.get("refresh_token_expires_in")
                    }
                )
            self._tokens.update(tokens)
        else:
            self._tokens = {}
            _LOGGER.error("Failed to get tokens")
        return bool(self._tokens)

    async def _init_mappers(self, locale: str = "en") -> bool | None:
        if not await self._get_tokens():
            return
        result = await (
            await self.api_wrapper(
                method="GET",
                url=f"{API_URL}/locales/{locale}/common",
                headers={
                    "Referer": CONSUMPTION_URL,
                },
            )
        ).json()
        self._meter_types_map = { str(k): v for (k, v) in enumerate(result["mappers"]["meterType"]) }
        self._meter_units_map = { str(k): v for (k, v) in enumerate(result["mappers"]["measurementUnit"]) }
        self._allocation_unit_map = result["mappers"]["allocationUnitMap"]

        return True

    async def _fetch_meters(self) -> bool | None:
        if not await self._get_tokens():
            return
        if not await self._init_mappers():
            return
        date = datetime.now()
        date = date.replace(hour=0, minute=0, second=0, microsecond=0)
        result = await (
            await self.api_wrapper(
                method="GET",
                url=f"{API_URL}/consumer/meters",
                params={
                    "startdate": f"{date.isoformat()}.000Z",
                },
                headers={
                    "Referer": METERS_URL,
                },
            )
        ).json()

        updated = False
        for item in result:
            json_meter = item["meter"]
            # Rien: my instance has a "shadow meter" without readings for each meter available with the same meterNo,
            # but the meterType is "Pulse Collector" and superAllocationUnit is null.
            # I am filtering out these here, but I do not know what their purpose is...
            if json_meter["superAllocationUnit"] is None:
                continue
            json_reading = item["reading"]
            meter_id = str(json_meter["meterId"])

            meter = self._meters.get(meter_id)
            if meter is None:
                meter = Meter(self, json_meter)
                self._meters[meter_id] = meter
                _LOGGER.debug("New meter found: %s", meter)
            updated |= meter.add_reading(json_reading)
        return updated

    async def available_meters(self):
        if not await self._get_tokens():
            return
        if not await self._fetch_meters():
            return
        return self._meters.values()

    async def update_meters(self):
        if not await self._get_tokens():
            return
        return await self._fetch_meters()

    async def fetch_meters(self) -> None:
        """Get all meters associated with the account."""
        if not await self._get_tokens():
            return
        meters = await (
            await self.api_wrapper(
                method="GET",
                url=f"{API_URL}/consumer/superallocationunits",
                headers={
                    "Referer": CONSUMPTION_URL,
                },
            )
        ).json()
        water_units = []
        heating_units = []
        power_units = []
        for meter in meters:
            match meter.get("superAllocationUnit"):
                case 1:  # Heating
                    heating_units += meter.get("allocationUnits")
                case 2:  # Water
                    water_units += meter.get("allocationUnits")
                case 3:  # Electricity
                    power_units += meter.get("allocationUnits")
        _LOGGER.info("Meter info: %s", str(meters))
        init = {"Meters": {"Day": {}, "Month": {}}}
        if heating_units:
            _LOGGER.debug("🔥 Heating meter(s) found")
            self._heating.update(init)
            self._heating.update({"Units": heating_units})
        if water_units:
            _LOGGER.debug("💧 Water meter(s) found")
            self._water.update(init)
            self._water.update({"Units": water_units})
        if power_units:
            _LOGGER.debug("⚡ Energy meter(s) found")
            self._power.update(init)
            self._power.update({"Units": power_units})

    async def fetch_consumption(self, _type: Consumption, interval: Interval) -> None:
        """Get consumption data for a specific meter type."""
        if not await self._get_tokens():
            return
        match _type:
            case Consumption.ELECTRICITY:
                usage = self._power
            case Consumption.WATER:
                usage = self._water
            case Consumption.HEATING:
                usage = self._heating
        if not usage:
            _LOGGER.debug("No %s meter was found", _type.name.lower())
            return
        consumption = [
            await (
                await self.api_wrapper(
                    method="GET",
                    url=f"{API_URL}/consumer/consumption",
                    params={
                        "startdate": start_of_interval(
                            interval, offset=timedelta(seconds=0)
                        ),
                        "enddate": end_of_interval(
                            interval, offset=timedelta(seconds=0)
                        ),
                        "interval": interval.value,
                        "allocationunit": unit,
                    },
                    headers={
                        "Referer": f"{CONSUMPTION_URL}/{_type.name.lower()}",
                    },
                )
            ).json()
            for unit in usage["Units"]
        ]
        # Add all metrics that are not None
        usage["Meters"][interval.name.capitalize()].update(
            {
                meter.get("meter").get("meterId")
                or index: {
                    "Name": meter.get("meter").get("placement") or index,
                    "Values": {
                        entry.get("fromDate")[
                            : 10 if interval is Interval.DAY else 7
                        ]: entry.get("consumption")
                        for entry in meter["consumptionValues"]
                        if entry.get("consumption") is not None
                    },
                }
                for lines in consumption
                for index, meter in enumerate(lines["consumptionLines"])
            }
        )

    def get_consumption(self) -> dict:
        """Return consumption data."""
        return {
            "Heating": self._heating,
            "Water": self._water,
            "Electricity": self._power,
        }

    async def api_wrapper(self, **args) -> ClientResponse:
        """Get information from the API."""
        async with async_timeout(TIMEOUT):
            try:
                async with self._session.request(**args) as response:
                    await response.read()
                    response.raise_for_status()
                    return response
            except asyncio.TimeoutError as exception:
                _LOGGER.error(
                    "Timeout error fetching information from %s - %s",
                    args["url"],
                    exception,
                )

            except (KeyError, TypeError) as exception:
                _LOGGER.error(
                    "Error parsing information from %s - %s",
                    args["url"],
                    exception,
                )
            except (ClientError, gaierror) as exception:
                _LOGGER.error(
                    "Error fetching information from %s - %s",
                    args["url"],
                    exception,
                )
            except Exception as exception:  # pylint: disable=broad-except
                _LOGGER.error("Something really wrong happened! - %s", exception)

class Reading:
    def __init__(self, meter, id: str | None, reading_value: float, reading_date: datetime):
        self._meter = meter
        self._id = id
        self.value = reading_value
        self.date = reading_date

    def __str__(self):
        return f"{self.value}{self._meter.meter_unit} @{self.date}"

class Meter:
    """
    Representing a single meter
    """
    def __init__(self, api: BrunataOnlineApiClient, json_meter: dict[str, str]):
        self._api = api

        self._meter_no = str(json_meter["meterNo"])
        self._meter_type_id = str(json_meter["meterType"])
        self._meter_unit_id = str(json_meter["unit"])
        self._allocation_unit_id = str(json_meter["allocationUnit"])

        self.meter_type = api._meter_types_map[self._meter_type_id]
        self.meter_unit = api._meter_units_map[self._meter_unit_id]
        self.allocation_unit = api._allocation_unit_map[self._allocation_unit_id]
        self._readings: dict[datetime, Reading] = {}
        self.latest_reading: Reading | None = None

    def add_reading(self, reading_json: dict[str, str]) -> bool:
        value = float(reading_json["value"]) if reading_json["value"] else None
        date = datetime.fromisoformat(reading_json["readingDate"]) if reading_json["readingDate"] else None
        rid = reading_json["readingId"]
        if date is not None and date not in self._readings:
            reading = Reading(self, rid, value, date)
            self._readings[date] = reading
            if self.latest_reading is None or self.latest_reading.date < date:
                self.latest_reading = reading
            _LOGGER.debug("Updated %s with new reading", self)
            return True
        else:
            return False

    def all_readings(self) -> list[Reading]:
        return list(sorted(self._readings.values(), key=lambda r: r.date))

    def __str__(self):
        return f"Meter({self.allocation_unit} ({self.meter_type}) - last_reading: {self.latest_reading})"
