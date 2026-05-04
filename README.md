# adsb-mqtt

Polls a [readsb](https://github.com/wiedehopf/readsb) `re-API` endpoint for
zstd-compressed binCraft aircraft snapshots, decodes them, and republishes
per-aircraft updates to MQTT.

By default it polls
`https://adsb.oarc.uk/re-api/?binCraft&zstd&box=-90,90,-180,180` (whole
world) every 1 s and publishes one MQTT message per aircraft *per change*.

## Topic hierarchy

| Topic | Payload | Retain | When |
|---|---|---|---|
| `{prefix}/aircraft/{hex}` | full JSON state | yes | any field change |
| `{prefix}/aircraft/{hex}` | empty | yes | aircraft not seen for `STALE_AFTER_SEC` |
| `{prefix}/flight/{flight}/{hex}` | full JSON state | no | flight callsign known + state changed |
| `{prefix}/type/{type}/{hex}` | full JSON state | no | on every change (`adsb_icao`, `mlat`, `mode_s`, ...) |
| `{prefix}/events/emergency/{hex}` | full JSON state | no | squawk in `7500`/`7600`/`7700` |
| `{prefix}/status` | `online` / `offline` | yes | LWT + connect |

`{prefix}` defaults to the hostname from `ADSB_URL` (e.g. `adsb.oarc.uk`)
so a single broker fanning in feeds from multiple sources keeps them
separated. Override with `MQTT_TOPIC_PREFIX` if you want something
different. Callsigns are uppercased and any character outside `[A-Z0-9_-]`
is replaced with `_` in topic segments.

The JSON payload is the per-aircraft dict produced by the binCraft decoder
(hex, lat, lon, alt_baro, gs, track, flight, t, r, type, ...) plus a
`last_seen` ISO-8601 UTC timestamp.

## Configuration

All via environment variables.

| Var | Default |
|---|---|
| `ADSB_URL` | `https://adsb.oarc.uk/re-api/?binCraft&zstd&box=-90,90,-180,180` |
| `POLL_INTERVAL_SEC` | `1.0` |
| `STALE_AFTER_SEC` | `60` |
| `HTTP_TIMEOUT_SEC` | `10` |
| `MQTT_HOST` | `localhost` |
| `MQTT_PORT` | `1883` |
| `MQTT_USERNAME` | (unset) |
| `MQTT_PASSWORD` | (unset) |
| `MQTT_CLIENT_ID` | `adsb-mqtt` |
| `MQTT_TOPIC_PREFIX` | hostname from `ADSB_URL` (e.g. `adsb.oarc.uk`) |
| `MQTT_QOS` | `0` |
| `LOG_LEVEL` | `INFO` |

## Pull the prebuilt image

Multi-arch images (linux/amd64, linux/arm64) are published to GHCR on
every push to `main` and on tags:

```sh
docker pull ghcr.io/m0lte/adsb-mqtt:latest
docker run --rm -e MQTT_HOST=your-broker ghcr.io/m0lte/adsb-mqtt:latest
```

## Run with docker compose

The provided `docker-compose.yml` pulls the prebuilt image from GHCR and
includes a local Mosquitto broker for convenience.

```sh
docker compose up
```

To build the image locally instead of pulling, swap the `image:` line in
`docker-compose.yml` for `build: .` (a comment in the file shows where) and
run `docker compose up --build`.

In another shell:

```sh
mosquitto_sub -h localhost -t 'adsb.oarc.uk/status'
mosquitto_sub -h localhost -t 'adsb.oarc.uk/aircraft/+' -v
mosquitto_sub -h localhost -t 'adsb.oarc.uk/events/emergency/+' -v
mosquitto_sub -h localhost -t 'adsb.oarc.uk/flight/BAW+/+' -v
mosquitto_sub -h localhost -t 'adsb.oarc.uk/aircraft/+' -C 1 | jq .
```

To point at your own broker, drop the `mosquitto` service from the compose
file and set `MQTT_HOST`/`MQTT_PORT`/credentials.

## Run without Docker

```sh
pip install -r requirements.txt
MQTT_HOST=localhost python -m bridge.main
```

## Notes on box scope

The upstream API expects `box=south,north,west,east`. The default
(`-90,90,-180,180`) is global. Narrow it for lower bandwidth and faster
polls, e.g. UK-ish: `box=49,61,-11,2`.

## Credits

Decoding logic vendored and refactored from
[acarsGuy/binCraft-decoder](https://github.com/acarsGuy/binCraft-decoder).
