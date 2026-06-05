"""Constants for Brunata Online."""

from datetime import timedelta

NAME = "Brunata Online"
DOMAIN = "brunata_online"
VERSION = "1.0.0"

ISSUE_URL = "https://github.com/hoffmann-thomas/ha-brunata/issues"

SENSOR = "sensor"
PLATFORMS = [SENSOR]

CONF_USERNAME = "username"
CONF_PASSWORD = "password"

DEFAULT_NAME = DOMAIN
SCAN_INTERVAL = timedelta(minutes=15)

STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}  v{VERSION}
Custom integration — report issues at {ISSUE_URL}
-------------------------------------------------------------------
"""
