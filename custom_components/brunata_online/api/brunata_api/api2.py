import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import urllib.parse
from datetime import datetime, timedelta
from socket import gaierror
from typing import Any, Coroutine, NamedTuple

from aiohttp import ClientError, ClientResponse, ClientSession
from async_timeout import timeout as async_timeout
from requests import Session

from .utils import from_response
from ..Result import Result
from .models import *

logging.basicConfig(level=logging.DEBUG)
_LOGGER: logging.Logger = logging.getLogger(__package__)
TIMEOUT = 10

from ..const import (
    API_URL,
    AUTHN_URL,
    CLIENT_ID,
    CONSUMPTION_URL,
    HEADERS,
    METERS_URL,
    OAUTH2_PROFILE,
    OAUTH2_URL,
    REDIRECT,
    Consumption,
    Interval,
)


class BrunataApi:

    def __init__(self, username: str, password: str, session: ClientSession) -> None:
        self._username = username
        self._password = password
        self._session = session
        self._session.headers.update(HEADERS)
        self._tokens = {}

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
            tokens = await self._api_wrapper(
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
        return await tokens.value.json()

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

    async def _api_wrapper(self, **args) -> Result[ClientResponse]:
        """Get information from the API."""
        async with async_timeout(TIMEOUT):
            try:
                async with self._session.request(**args) as response:
                    await response.read()
                    response.raise_for_status()
                    return Result[ClientResponse](response)
            except asyncio.TimeoutError as exception:
                _LOGGER.error(
                    "Timeout error fetching information from %s - %s",
                    args["url"],
                    exception,
                )
                return Result[ClientResponse](exception)
            except (KeyError, TypeError) as exception:
                _LOGGER.error(
                    "Error parsing information from %s - %s",
                    args["url"],
                    exception,
                )
                return Result[ClientResponse](exception)
            except (ClientError, gaierror) as exception:
                _LOGGER.error(
                    "Error fetching information from %s - %s",
                    args["url"],
                    exception,
                )
                return Result[ClientResponse](exception)
            except Exception as exception:  # pylint: disable=broad-except
                _LOGGER.error("Something really wrong happened! - %s", exception)
                return Result[ClientResponse](exception)

    async def get_mapping_configuration(self, locale: str = "en") -> Result[MappersConfiguration]:
        if not await self._get_tokens():
            return Result[MappersConfiguration](Exception("Failed to get tokens"))
        response = await self._api_wrapper(
            method="GET",
            url=f"{API_URL}/locales/{locale}/common",
            headers={
                "Referer": CONSUMPTION_URL,
            },
        )
        if response.is_error():
            return Result[MappersConfiguration](response.value)
        # Response contains a lot of localisation strings, we dont need.
        # We only care about the 'mappers' section, since it contains mappings for allocation units, units etc.
        return Result[MappersConfiguration]((await from_response(response.value, Configuration, False)).mappers)

    async def get_allocation_units(self) -> Result[AllocationUnitResult]:
        """Get all meters associated with the account."""
        if not await self._get_tokens():
            return Result[AllocationUnitResult](Exception("Failed to get tokens"))
        response = await self._api_wrapper(
            method="GET",
            url=f"{API_URL}/consumer/superallocationunits",
            headers={
                "Referer": CONSUMPTION_URL,
            },
        )
        if response.is_error():
            return Result[AllocationUnitResult](response.value)
        return Result[AllocationUnitResult](await from_response(response.value, AllocationUnitResult))

    async def get_meters(self) -> Result[MeterResult]:
        if not await self._get_tokens():
            return Result[MeterResult](Exception("Failed to get tokens"))
        date = datetime.now()
        date = date.replace(hour=0, minute=0, second=0, microsecond=0)

        response = await self._api_wrapper(
            method="GET",
            url=f"{API_URL}/consumer/meters",
            params={
                "startdate": f"{date.isoformat()}.000Z",
            },
            headers={
                "Referer": METERS_URL,
            },
        )
        if response.is_error():
            return Result[MeterResult](response.value)
        return Result[MeterResult](await (from_response(response.value, MeterResult, False)))

    async def get_consumption(self, start_date: datetime, end_date: datetime, _type: Consumption, allocation_unit: str,
                              interval: Interval) -> Result[ConsumptionResult]:
        """Get consumption data for a specific meter type."""
        if not await self._get_tokens():
            return Result[ConsumptionResult](Exception("Failed to get tokens"))
        start = f"{start_date.isoformat()}.000Z"
        end = f"{end_date.isoformat()}.999Z"
        response = await self._api_wrapper(
            method="GET",
            url=f"{API_URL}/consumer/consumption",
            params={
                "startdate": start,
                "enddate": end,
                "interval": interval.value,
                "allocationunit": allocation_unit,
            },
            headers={
                "Referer": f"{CONSUMPTION_URL}/{_type.name.lower()}",
            },
        )
        if response.is_error():
            return Result[ConsumptionResult](response.value)
        return Result[ConsumptionResult](await from_response(response.value, ConsumptionResult, False))
