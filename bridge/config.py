import os
from dataclasses import dataclass


def _env_str(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v is not None and v != "" else default


def _env_opt(name: str) -> str | None:
    v = os.environ.get(name)
    return v if v else None


def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name)
    return float(v) if v else default


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v else default


@dataclass(frozen=True)
class Config:
    adsb_url: str
    poll_interval_sec: float
    stale_after_sec: float
    http_timeout_sec: float
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    mqtt_client_id: str
    mqtt_topic_prefix: str
    mqtt_qos: int
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            adsb_url=_env_str(
                "ADSB_URL",
                "https://adsb.oarc.uk/re-api/?binCraft&zstd&box=-90,90,-180,180",
            ),
            poll_interval_sec=_env_float("POLL_INTERVAL_SEC", 1.0),
            stale_after_sec=_env_float("STALE_AFTER_SEC", 60.0),
            http_timeout_sec=_env_float("HTTP_TIMEOUT_SEC", 10.0),
            mqtt_host=_env_str("MQTT_HOST", "localhost"),
            mqtt_port=_env_int("MQTT_PORT", 1883),
            mqtt_username=_env_opt("MQTT_USERNAME"),
            mqtt_password=_env_opt("MQTT_PASSWORD"),
            mqtt_client_id=_env_str("MQTT_CLIENT_ID", "adsb-mqtt"),
            mqtt_topic_prefix=_env_str("MQTT_TOPIC_PREFIX", "adsb").rstrip("/"),
            mqtt_qos=_env_int("MQTT_QOS", 0),
            log_level=_env_str("LOG_LEVEL", "INFO").upper(),
        )
