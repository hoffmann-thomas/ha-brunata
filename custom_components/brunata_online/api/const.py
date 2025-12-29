"""Constants used by the Brunata API."""

from enum import Enum

# API Constants
BASE_URL = "https://online.brunata.com"
OAUTH2_PROFILE = "B2C_1_signin_username"
AUTHN_URL = f"https://brunatab2cprod.b2clogin.com/brunatab2cprod.onmicrosoft.com/{OAUTH2_PROFILE}"
AUTH_FULL_URL = "https://brunatab2cprod.b2clogin.com/brunatab2cprod.onmicrosoft.com/B2C_1_signin_username"
API_URL = f"{BASE_URL}/online-webservice/v1/rest"

OAUTH2_URL = f"{AUTHN_URL}/oauth2/v2.0"
CLIENT_ID = "e1d10965-78dc-4051-a1e5-251483e74d03"
REDIRECT = f"{BASE_URL}/auth-response"

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
