"""Cau hinh dung chung: doc .env, tao client InfluxDB, dam bao bucket ton tai."""
import os
from pathlib import Path

from dotenv import load_dotenv
from influxdb_client import InfluxDBClient
from influxdb_client.domain.bucket_retention_rules import BucketRetentionRules

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iot-lab")
BUCKET_RAW = os.getenv("INFLUX_BUCKET_RAW", "sensor_raw")
BUCKET_PROCESSED = os.getenv("INFLUX_BUCKET_PROCESSED", "sensor_processed")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "iot/lab2/+/telemetry")

MEASUREMENT_RAW = "environment"
MEASUREMENT_CLEAN = "environment_clean"

RETENTION_SECONDS = {BUCKET_RAW: 30 * 86400, BUCKET_PROCESSED: 90 * 86400}


def get_client() -> InfluxDBClient:
    return InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)


def ensure_buckets(client: InfluxDBClient) -> None:
    """Tao bucket neu chua co, kem retention policy (raw 30 ngay, processed 90 ngay)."""
    api = client.buckets_api()
    for name, secs in RETENTION_SECONDS.items():
        if api.find_bucket_by_name(name) is None:
            rules = BucketRetentionRules(type="expire", every_seconds=secs)
            api.create_bucket(bucket_name=name, retention_rules=rules, org=INFLUX_ORG)
            print(f"[setup] tao bucket {name} (retention {secs // 86400} ngay)")
