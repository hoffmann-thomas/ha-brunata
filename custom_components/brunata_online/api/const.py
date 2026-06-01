"""Constants used by the Brunata API."""

from enum import Enum

# API base
BASE_URL = "https://online.brunata.com"
API_URL = f"{BASE_URL}/online-webservice/v2/rest"

# Keycloak OIDC endpoints (migrated from Azure AD B2C)
CLIENT_ID = "82770188-c92e-4d16-927d-a15c472eda55"
REDIRECT = f"{BASE_URL}/auth-redirect"
KEYCLOAK_BASE = f"{BASE_URL}/iam/realms/online-prod/protocol/openid-connect"
KEYCLOAK_AUTH_URL = f"{KEYCLOAK_BASE}/auth"

# Token exchange goes through the Brunata proxy, not directly to Keycloak.
# The proxy issues tokens accepted by the consumer API.
OAUTH_PROXY_BASE = f"{BASE_URL}/online-auth-webservice/v1/rest"
KEYCLOAK_TOKEN_URL = f"{OAUTH_PROXY_BASE}/oauth/token"

# Brunata app page URLs (used as Referer headers)
CONSUMPTION_URL = f"{BASE_URL}/consumption-overview"
METERS_URL = f"{BASE_URL}/react-online/meters-values"

# Default headers
HEADERS = {
    "User-Agent": "ha-brunata/0.0.1",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


class Consumption(Enum):
    """Enum for the different types of consumption."""

    HEATING = 1
    WATER = 2
    ELECTRICITY = 3


class Interval(Enum):
    """Enum for the different types of intervals."""

    DAY = "D"
    MONTH = "M"
