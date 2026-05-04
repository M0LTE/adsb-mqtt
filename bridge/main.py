import json
import logging
import re
import signal
import sys
import time
from datetime import datetime, timezone

import httpx
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from bridge.bincraft_decoder import decode_bytes
from bridge.config import Config

EMERGENCY_SQUAWKS = {"7500", "7600", "7700"}

# Fields excluded from change detection: they tick every poll without
# representing a real state change, and would force a publish every cycle.
_VOLATILE_FIELDS = {"seen", "seen_pos", "rssi", "messages", "rc", "now"}

_TOPIC_SAFE = re.compile(r"[^A-Z0-9_-]")
# Operator code: three letters at the start of a callsign followed by at least
# one more character (so plain regs like "G-ABCD" / "N12345" are excluded).
_CARRIER_RE = re.compile(r"^([A-Z]{3})[A-Z0-9].*$")


log = logging.getLogger("adsb-mqtt")


def _safe_topic_segment(s: str) -> str:
    return _TOPIC_SAFE.sub("_", s.upper())


def _carrier_code(flight: str | None) -> str | None:
    if not flight:
        return None
    m = _CARRIER_RE.match(flight.upper())
    return m.group(1) if m else None


def _clean(ac: dict) -> dict:
    """Strip blank string fields so they don't appear in the published payload."""
    out = {}
    for k, v in ac.items():
        if isinstance(v, str) and v == "":
            continue
        out[k] = v
    return out


def _diff_significant(prev: dict | None, curr: dict) -> bool:
    if prev is None:
        return True
    for k, v in curr.items():
        if k in _VOLATILE_FIELDS:
            continue
        if prev.get(k) != v:
            return True
    # Also detect removed keys (e.g. flight callsign cleared).
    for k in prev:
        if k in _VOLATILE_FIELDS:
            continue
        if k not in curr:
            return True
    return False


class Bridge:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.status_topic = f"{cfg.mqtt_topic_prefix}/status"
        self.aircraft_prefix = f"{cfg.mqtt_topic_prefix}/aircraft"
        self.flight_prefix = f"{cfg.mqtt_topic_prefix}/flight"
        self.type_prefix = f"{cfg.mqtt_topic_prefix}/type"
        self.carrier_prefix = f"{cfg.mqtt_topic_prefix}/carrier"
        self.emergency_prefix = f"{cfg.mqtt_topic_prefix}/events/emergency"

        self.client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=cfg.mqtt_client_id,
            clean_session=True,
        )
        if cfg.mqtt_username:
            self.client.username_pw_set(cfg.mqtt_username, cfg.mqtt_password or "")
        self.client.will_set(self.status_topic, "offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

        self.http = httpx.Client(
            timeout=cfg.http_timeout_sec,
            headers={
                "User-Agent": "adsb-mqtt/1.0 (+https://github.com/m0lte/adsb-mqtt)",
                # Don't let httpx negotiate gzip/br: the upstream payload is
                # zstd inside the body, which must be delivered untouched.
                "Accept-Encoding": "identity",
            },
        )

        # Last published cleaned-state per hex (used for change detection).
        self.last: dict[str, dict] = {}
        # Wall-clock when we last saw each hex in any poll.
        self.last_seen_at: dict[str, float] = {}

        self._stop = False

    # ----- MQTT callbacks -----
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            log.info("MQTT connected to %s:%d", self.cfg.mqtt_host, self.cfg.mqtt_port)
            client.publish(self.status_topic, "online", qos=1, retain=True)
        else:
            log.error("MQTT connect failed: %s", reason_code)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        log.warning("MQTT disconnected: %s", reason_code)

    # ----- core loop -----
    def run(self) -> None:
        self.client.connect_async(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=60)
        self.client.loop_start()

        next_tick = time.monotonic()
        while not self._stop:
            start = time.monotonic()
            try:
                self._poll_once()
            except Exception as e:
                log.exception("poll iteration failed: %s", e)
            elapsed = time.monotonic() - start
            next_tick += self.cfg.poll_interval_sec
            sleep = next_tick - time.monotonic()
            if sleep < 0:
                # Fell behind; reset cadence.
                next_tick = time.monotonic()
                log.debug("poll took %.2fs (over budget)", elapsed)
            else:
                time.sleep(sleep)

        self._shutdown()

    def _poll_once(self) -> None:
        resp = self.http.get(self.cfg.adsb_url)
        resp.raise_for_status()
        decoded = decode_bytes(resp.content, zstd_compressed=True)
        now_wall = time.time()
        published = 0
        for raw_ac in decoded.get("aircraft", []):
            ac = _clean(raw_ac)
            hex_id = ac.get("hex")
            if not hex_id:
                continue
            self.last_seen_at[hex_id] = now_wall
            if not _diff_significant(self.last.get(hex_id), ac):
                continue
            ac_with_ts = dict(ac)
            ac_with_ts["last_seen"] = datetime.fromtimestamp(
                now_wall - ac.get("seen", 0.0), tz=timezone.utc
            ).isoformat()
            payload = json.dumps(ac_with_ts, separators=(",", ":"), default=str)
            self._publish_aircraft(hex_id, ac, payload)
            self.last[hex_id] = ac
            published += 1

        expired = self._expire_stale(now_wall)
        log.info(
            "poll: %d aircraft, %d published, %d expired",
            len(decoded.get("aircraft", [])),
            published,
            expired,
        )

    def _publish_aircraft(self, hex_id: str, ac: dict, payload: str) -> None:
        qos = self.cfg.mqtt_qos
        # Primary: per-hex retained.
        self.client.publish(
            f"{self.aircraft_prefix}/{hex_id}", payload, qos=qos, retain=True
        )
        # Fan-out: by signal type.
        ac_type = ac.get("type")
        if ac_type:
            self.client.publish(
                f"{self.type_prefix}/{_safe_topic_segment(ac_type)}/{hex_id}",
                payload, qos=qos, retain=False,
            )
        # Fan-out: by flight callsign (when known).
        flight = ac.get("flight")
        if flight:
            self.client.publish(
                f"{self.flight_prefix}/{_safe_topic_segment(flight)}/{hex_id}",
                payload, qos=qos, retain=False,
            )
        # Fan-out: by carrier (ICAO 3-letter operator code parsed from callsign).
        carrier = _carrier_code(flight)
        if carrier:
            self.client.publish(
                f"{self.carrier_prefix}/{carrier}/{hex_id}",
                payload, qos=qos, retain=False,
            )
        # Emergency events.
        if ac.get("squawk") in EMERGENCY_SQUAWKS:
            self.client.publish(
                f"{self.emergency_prefix}/{hex_id}", payload, qos=qos, retain=False
            )

    def _expire_stale(self, now_wall: float) -> int:
        cutoff = now_wall - self.cfg.stale_after_sec
        stale = [h for h, t in self.last_seen_at.items() if t < cutoff]
        for h in stale:
            # Empty retained payload clears the last-known state for new subs.
            self.client.publish(
                f"{self.aircraft_prefix}/{h}", payload=b"", qos=self.cfg.mqtt_qos, retain=True
            )
            self.last_seen_at.pop(h, None)
            self.last.pop(h, None)
        return len(stale)

    def stop(self) -> None:
        self._stop = True

    def _shutdown(self) -> None:
        try:
            self.client.publish(self.status_topic, "offline", qos=1, retain=True).wait_for_publish(timeout=2)
        except Exception:
            pass
        self.client.loop_stop()
        self.client.disconnect()
        self.http.close()


def main() -> int:
    cfg = Config.from_env()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info(
        "starting: url=%s broker=%s:%d prefix=%s interval=%.2fs",
        cfg.adsb_url, cfg.mqtt_host, cfg.mqtt_port, cfg.mqtt_topic_prefix, cfg.poll_interval_sec,
    )
    bridge = Bridge(cfg)

    def _handle_signal(signum, frame):
        log.info("signal %d received, shutting down", signum)
        bridge.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    bridge.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
