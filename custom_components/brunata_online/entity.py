"""Shared device_info for all Brunata Online entities."""

from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, NAME, VERSION


def brunata_device_info(entry_id: str) -> DeviceInfo:
    """Return a DeviceInfo dict shared by all entities for a config entry."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name=NAME,
        manufacturer="Brunata",
        sw_version=VERSION,
    )
