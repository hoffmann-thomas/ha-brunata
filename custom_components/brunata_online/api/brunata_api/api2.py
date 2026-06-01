import asyncio
import base64
import hashlib
import logging
import re
import secrets
import urllib.parse
from asyncio import timeout as async_timeout
from socket import gaierror

from aiohttp import ClientError, ClientResponse

from .models import (
    AllocationUnitResult,
    Configuration,
    ConsumptionResult,
    MappersConfiguration,
    MeterResult,
)
from .utils import from_response
from ..result import Result
from ..const import (
    API_URL,
    CLIENT_ID,
    CONSUMPTION_URL,
    HEADERS,
    KEYCLOAK_AUTH_URL,
    KEYCLOAK_TOKEN_URL,
    METERS_URL,
    REDIRECT,
    Consumption,
    Interval,
)

_LOGGER: logging.Logger = logging.getLogger(__package__)
TIMEOUT = 10


class BrunataApi:

    def __init__(self, username: str, password: str, session: ClientSession) -> None:
        self._username = username
        self._password = password
        self._session = session
        self._tokens: dict = {}

    # ── Token management ────────────────────────────────────────────────────────

    def _is_token_valid(self, kind: str) -> bool:
        if not self._tokens:
            return False
        if kind == "access_token":
            ts = self._tokens.get("expires_on")
            return ts is not None and datetime.fromtimestamp(ts) > datetime.now()
        if kind == "refresh_token":
            # Keycloak sets refresh_expires_in=0 for session-scoped tokens (no hard expiry).
            # Treat those as valid until an API call fails.
            expires_on = self._tokens.get("refresh_token_expires_on")
            if expires_on is None or expires_on == 0:
                return bool(self._tokens.get("refresh_token"))
            return datetime.fromtimestamp(expires_on) > datetime.now()
        return True

    async def _refresh_tokens(self) -> dict:
        if self._is_token_valid("access_token"):
            _LOGGER.debug(
                "Access token still valid, expires in %d s",
                self._tokens.get("expires_on", 0) - int(datetime.now().timestamp()),
            )
            return self._tokens

        async with self._session.post(
            KEYCLOAK_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": self._tokens.get("refresh_token"),
            },
        ) as resp:
            if not resp.ok:
                _LOGGER.error("Token refresh failed: %d", resp.status)
                return {}
            return await resp.json()

    # ── Keycloak PKCE auth flow ─────────────────────────────────────────────────

    @staticmethod
    def _generate_pkce() -> tuple[str, str]:
        raw = base64.urlsafe_b64encode(secrets.token_bytes(40)).decode()
        verifier = re.sub("[^a-zA-Z0-9]+", "", raw)
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        return verifier, challenge

    async def _keycloak_auth(self) -> dict:
        code_verifier, code_challenge = self._generate_pkce()

        # Step 1: GET the Keycloak login page
        async with self._session.get(
            KEYCLOAK_AUTH_URL,
            params={
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT,
                "scope": "openid offline_access",
                "response_type": "code",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            },
            allow_redirects=False,
        ) as resp:
            if resp.status not in (200, 302):
                _LOGGER.error("Keycloak auth page failed: %d %s", resp.status, resp.url)
                return {}

            # If Keycloak already has a valid session it may skip the login form
            # and redirect straight to our redirect_uri with a code.
            if resp.status == 302:
                loc = resp.headers.get("Location", "")
                if loc.startswith(REDIRECT) and "code=" in loc:
                    auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["code"][0]
                    return await self._exchange_code(auth_code, code_verifier)
                _LOGGER.error("Unexpected redirect from Keycloak auth: %s", loc[:200])
                return {}

            body = await resp.text()

        # Extract form action URL from the login page
        m = re.search(r'action="([^"]+)"', body)
        if not m:
            _LOGGER.error("Keycloak login form action not found in response")
            return {}
        form_action = m.group(1).replace("&amp;", "&")

        # Step 2: POST credentials to the login form
        async with self._session.post(
            form_action,
            data={
                "username": self._username,
                "password": self._password,
                "credentialId": "",
            },
            allow_redirects=False,
        ) as resp:
            redirect_loc = resp.headers.get("Location", "")
            if not redirect_loc.startswith(REDIRECT) or "code=" not in redirect_loc:
                body = await resp.text()
                # Extract Keycloak error message from the page
                err_match = re.search(
                    r'class="[^"]*(?:pf-c-alert__title|kc-feedback-text|alert-error)[^"]*"[^>]*>(.*?)</(?:span|p|div)',
                    body, re.S
                )
                kc_error = re.sub(r"<[^>]+>", "", err_match.group(1)).strip() if err_match else "no error text found"
                _LOGGER.error(
                    "Credential POST failed — status=%d location=%s keycloak_error=%s",
                    resp.status, redirect_loc[:200], kc_error,
                )
                return {}

        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_loc).query)["code"][0]
        return await self._exchange_code(auth_code, code_verifier)

    async def _exchange_code(self, auth_code: str, code_verifier: str) -> dict:
        """Exchange an authorization code for tokens."""
        async with self._session.post(
            KEYCLOAK_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT,
                "code": auth_code,
                "code_verifier": code_verifier,
            },
        ) as resp:
            if not resp.ok:
                body = await resp.text()
                _LOGGER.error("Token exchange failed: %d %s", resp.status, body[:200])
                return {}
            return await resp.json()

    # ── Token gate ──────────────────────────────────────────────────────────────

    async def _get_tokens(self) -> bool:
        """Ensure a valid access token is in the session. Returns True on success."""
        if self._is_token_valid("refresh_token"):
            tokens = await self._refresh_tokens()
        else:
            tokens = await self._keycloak_auth()

        if tokens.get("access_token"):
            now = int(datetime.now().timestamp())
            tokens.setdefault("expires_on", now + tokens.get("expires_in", 3600))
            refresh_exp = tokens.get("refresh_expires_in", 0)
            if refresh_exp and refresh_exp > 0:
                tokens.setdefault("refresh_token_expires_on", now + refresh_exp)
            self._tokens.update(tokens)
        else:
            self._tokens = {}
            _LOGGER.error("Failed to get tokens")

        return bool(self._tokens)

    # ── API wrapper ─────────────────────────────────────────────────────────────

    def _auth_headers(self) -> dict:
        token = self._tokens.get("access_token", "")
        token_type = self._tokens.get("token_type", "Bearer")
        return {**HEADERS, "Authorization": f"{token_type} {token}"}

    async def _api_wrapper(self, **kwargs) -> Result[ClientResponse]:
        kwargs["headers"] = self._auth_headers()
        async with async_timeout(TIMEOUT):
            try:
                async with self._session.request(**kwargs) as response:
                    await response.read()
                    response.raise_for_status()
                    return Result[ClientResponse](response)
            except asyncio.TimeoutError as exc:
                _LOGGER.error("Timeout fetching %s: %s", kwargs.get("url"), exc)
                return Result[ClientResponse](exc)
            except (KeyError, TypeError) as exc:
                _LOGGER.error("Parse error from %s: %s", kwargs.get("url"), exc)
                return Result[ClientResponse](exc)
            except (ClientError, gaierror) as exc:
                _LOGGER.error("Request error from %s: %s", kwargs.get("url"), exc)
                return Result[ClientResponse](exc)
            except Exception as exc:  # pylint: disable=broad-except
                _LOGGER.error("Unexpected error: %s", exc)
                return Result[ClientResponse](exc)

    # ── Public API methods ──────────────────────────────────────────────────────

    async def get_mapping_configuration(self, locale: str = "en") -> Result[MappersConfiguration]:
        if not await self._get_tokens():
            return Result[MappersConfiguration](Exception("Failed to get tokens"))
        response = await self._api_wrapper(
            method="GET",
            url=f"{API_URL}/locales/{locale}/common",
            headers={"Referer": CONSUMPTION_URL},
        )
        if response.is_error():
            return Result[MappersConfiguration](response.value)
        return Result[MappersConfiguration]((await from_response(response.value, Configuration, False)).mappers)

    async def get_allocation_units(self) -> Result[AllocationUnitResult]:
        if not await self._get_tokens():
            return Result[AllocationUnitResult](Exception("Failed to get tokens"))
        response = await self._api_wrapper(
            method="GET",
            url=f"{API_URL}/consumer/superallocationunits",
            headers={"Referer": CONSUMPTION_URL},
        )
        if response.is_error():
            return Result[AllocationUnitResult](response.value)
        return Result[AllocationUnitResult](await from_response(response.value, AllocationUnitResult))

    async def get_meters(self) -> Result[MeterResult]:
        if not await self._get_tokens():
            return Result[MeterResult](Exception("Failed to get tokens"))
        date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        response = await self._api_wrapper(
            method="GET",
            url=f"{API_URL}/consumer/meters",
            params={"startdate": f"{date.strftime('%Y-%m-%dT%H:%M:%S')}.000Z"},
            headers={"Referer": METERS_URL},
        )
        if response.is_error():
            return Result[MeterResult](response.value)
        return Result[MeterResult](await from_response(response.value, MeterResult, False))

    async def get_consumption(
        self,
        start_date: datetime,
        end_date: datetime,
        _type: Consumption,
        allocation_unit: str,
        interval: Interval,
    ) -> Result[ConsumptionResult]:
        if not await self._get_tokens():
            return Result[ConsumptionResult](Exception("Failed to get tokens"))
        fmt = "%Y-%m-%dT%H:%M:%S"
        response = await self._api_wrapper(
            method="GET",
            url=f"{API_URL}/consumer/consumption",
            params={
                "startdate": f"{start_date.strftime(fmt)}.000Z",
                "enddate": f"{end_date.strftime(fmt)}.999Z",
                "interval": interval.value,
                "allocationunit": allocation_unit,
            },
            headers={"Referer": f"{CONSUMPTION_URL}/{_type.name.lower()}"},
        )
        if response.is_error():
            return Result[ConsumptionResult](response.value)
        return Result[ConsumptionResult](await from_response(response.value, ConsumptionResult, False))
