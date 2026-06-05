"""
Quick API connectivity test — run from the project root with:
  .venv/Scripts/python test_api.py
"""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(__file__))


# Stub out homeassistant so the package __init__.py can be imported
# without a full HA installation. Any attribute access on a stub returns
# another stub so the entire HA namespace is covered automatically.
class _Stub:
    """Returns itself for any attribute, call, or subclass operation."""

    def __getattr__(self, _):
        return _Stub()

    def __call__(self, *a, **kw):
        return _Stub()

    def __getitem__(self, _):
        return _Stub()

    def __mro_entries__(self, bases):
        return (object,)

    def __init_subclass__(cls, **kw):
        pass

    def __class_getitem__(cls, _):
        return cls


class _HAModule(types.ModuleType):
    def __getattr__(self, name):
        stub = _Stub()
        setattr(self, name, stub)
        return stub


def _stub_ha(*names):
    for name in names:
        if name not in sys.modules:
            sys.modules[name] = _HAModule(name)


_stub_ha(
    "homeassistant",
    "homeassistant.config_entries",
    "homeassistant.core",
    "homeassistant.core_config",
    "homeassistant.exceptions",
    "homeassistant.helpers",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.aiohttp_client",
    "homeassistant.components",
    "homeassistant.components.recorder",
    "homeassistant.components.recorder.models",
    "homeassistant.components.recorder.statistics",
    "homeassistant.components.sensor",
    "homeassistant.const",
    "homeassistant.util",
)

import aiohttp  # noqa: E402
from aiohttp import CookieJar  # noqa: E402

USERNAME = os.getenv("BRUNATA_USERNAME", "")
PASSWORD = os.getenv("BRUNATA_PASSWORD", "")

if not USERNAME or not PASSWORD:
    print("Set BRUNATA_USERNAME and BRUNATA_PASSWORD environment variables first.")
    sys.exit(1)


async def main():
    session = aiohttp.ClientSession(
        cookie_jar=CookieJar(unsafe=True, quote_cookie=False)
    )
    try:
        from custom_components.brunata_online.api.brunata_api.api2 import BrunataApi
        from custom_components.brunata_online.api.brunata_api.client import (
            BrunataClient,
        )
        from custom_components.brunata_online.api.const import Consumption, Interval
        import datetime

        api = BrunataApi(USERNAME, PASSWORD, session)

        print("-- 1. Authorize / get tokens --")
        ok = await api._get_tokens()
        print(f"   tokens ok: {ok}")
        if not ok:
            print("   Auth failed — check credentials and inspect logs above.")
            return

        print("\n-- 2. Mapping configuration --")
        config = await api.get_mapping_configuration("en")
        print(f"   error: {config.is_error()}")
        if not config.is_error():
            print(f"   meter types: {config.value.meterType[:5]}")

        print("\n-- 3. Allocation units --")
        alloc = await api.get_allocation_units()
        print(f"   error: {alloc.is_error()}")
        if not alloc.is_error():
            print(f"   units: {[u.allocationUnits for u in alloc.value.root]}")

        print("\n-- 4. Meters --")
        client = BrunataClient(USERNAME, PASSWORD, session, "en")
        meters = await client.get_meters()
        for m in meters:
            print(
                f"   [{m.meter_type_code}] {m.value_category} — id={m.meter_id} unit={m.unit} alloc={m.allocation_unit_code}"
            )

        if meters:
            print("\n-- 5. Consumption (last 30 days) --")
            end = datetime.datetime.today()
            start = end - datetime.timedelta(days=30)
            m = meters[0]
            readings = await client.get_consumption(
                start,
                end,
                Consumption(m.meter_type_code),
                m.allocation_unit_code,
                Interval.DAY,
            )
            for r in readings:
                for v in r.Values:
                    if v.consumption is not None:
                        print(f"   {v.fromDate.date()}  {v.consumption} {m.unit}")

    finally:
        await session.close()


asyncio.run(main())
