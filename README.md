# Brunata Online for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2026.5%2B-blue.svg)](https://www.home-assistant.io)
[![GitHub Release](https://img.shields.io/github/v/release/hoffmann-thomas/ha-brunata)](https://github.com/hoffmann-thomas/ha-brunata/releases)
[![License](https://img.shields.io/github/license/hoffmann-thomas/ha-brunata)](LICENSE)

Imports historical heating and water consumption data from [Brunata Online](https://online.brunata.com) into Home Assistant's **Energy Dashboard** as long-term statistics.

> **Supported in Denmark.** Brunata Online is a Danish utility metering service used in apartment buildings for district heating (varme) and water (vand) billing.

## What it does

- Creates sensor entities for each of your registered Brunata meters (radiator heating units, hot water, cold water)
- Imports up to **365 days** of historical daily consumption data on first setup
- Updates the current day's running total throughout the day (today's partial consumption)
- Stores all data as **Home Assistant long-term statistics** — visible in the Energy Dashboard and History graphs

## Requirements

- An active **Brunata Online** account at [online.brunata.com](https://online.brunata.com) with at least one registered meter
- Home Assistant **2026.5.0** or newer
- The **Recorder** integration (enabled by default in HA)

## Installation

### Via HACS (recommended)

1. Open HACS in your Home Assistant instance
2. Go to **Integrations**
3. Click the three-dot menu → **Custom repositories**
4. Add `https://github.com/hoffmann-thomas/ha-brunata` with category **Integration**
5. Find **Brunata Online** and click **Download**
6. Restart Home Assistant

### Manual installation

1. Download the [latest release](https://github.com/hoffmann-thomas/ha-brunata/releases/latest)
2. Copy the `custom_components/brunata_online` folder to your HA `config/custom_components/` directory
3. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Brunata Online**
3. Enter your Brunata Online **email address** and **password**
4. Click **Submit** — the integration authenticates and discovers your meters

> One integration entry per Brunata account. Each physical meter appears as a separate sensor entity.

## Sensors

For each registered meter, a sensor is created:

| Meter type | Example entity | Unit | Device class |
|---|---|---|---|
| Radiator (heating) | `sensor.brunata_heating_livingroom` | units* | Energy |
| Hot water | `sensor.brunata_hot_water_kitchen` | m³ | Water |
| Cold water | `sensor.brunata_cold_water_kitchen` | m³ | Water |

> *Brunata radiator sensors report in **varmeenheder** (heat units) — a proprietary Brunata allocation unit, not a standard energy unit. The values represent your proportional share of the building's total heating consumption.

## Energy Dashboard

After setup, add your Brunata sensors to the Energy Dashboard:

1. **Settings → Energy** (or **Energy** in the sidebar)
2. Click **Add consumption** under the relevant category
3. Select your Brunata sensor

Heating sensors appear under **Individual devices** (since varmeenheder is not a standard energy unit recognised by the dashboard). Water sensors appear under **Water consumption**.

## Technical notes

- **Authentication**: Uses Brunata's Keycloak OIDC (PKCE) flow — no credentials are stored in plain text beyond HA's encrypted config store
- **Data resolution**: Brunata provides daily data. The integration fetches in 30-day chunks to respect the API's response limit
- **Update interval**: Every 15 minutes. Historical data is only fetched on first setup; subsequent updates fetch only new days
- **Timezone**: Brunata timestamps are in Danish local time (CET/CEST). The integration stores UTC-normalised timestamps compatible with HA's recorder

## Troubleshooting

**"Failed to connect to Brunata" on setup**
- Verify you can log in at [online.brunata.com](https://online.brunata.com)
- Check that your account has at least one registered meter

**No data in Energy Dashboard after setup**
- Wait up to 15 minutes for the first data import to complete (365 days fetched in 30-day chunks)
- Ensure the sensors have been added to the Energy Dashboard configuration

**Negative today's usage**
- This can happen if the previous daily total was imported with a partial value that was later revised. Removing and re-adding the sensor to the Energy Dashboard configuration resets the baseline

## Contributing

Issues and pull requests are welcome at [github.com/hoffmann-thomas/ha-brunata](https://github.com/hoffmann-thomas/ha-brunata).

## License

MIT © [hoffmann-thomas](https://github.com/hoffmann-thomas)
