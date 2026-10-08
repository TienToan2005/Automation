"""Subscribe MQTT -> validate -> ghi InfluxDB (real-time).

Schema:  measurement=environment, tags=device_id,location,
         fields=temperature,humidity,distance_cm,seq,latency_ms
Timestamp cua point = thoi gian do thiet bi gui (ts, ms). Vi InfluxDB coi
(measurement + tags + timestamp) la khoa duy nhat, ban tin gui trung (duplicate)
se ghi de len nhau => idempotent, khong sinh ban ghi trung.
"""
import json
import logging
import time

import paho.mqtt.client as mqtt
from influxdb_client import Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

import common

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collector")

# Gioi han vat ly cua cam bien (loai ban tin hong). Outlier "mem" de cho buoc tien xu ly.
LIMITS = {"temperature": (-40.0, 85.0), "humidity": (0.0, 100.0), "distance_cm": (2.0, 400.0)}

stats = {"received": 0, "written": 0, "invalid": 0, "duplicate": 0, "lost": 0}
last_seq: dict = {}


def validate(msg):
    """Tra ve (fields hop le, ly do loi). Gia tri null/ngoai khoang bi bo qua rieng le."""
    if not isinstance(msg, dict):
        return {}, "payload khong phai object"
    dev, ts = msg.get("device_id"), msg.get("ts")
    if not isinstance(dev, str) or not dev:
        return {}, "thieu device_id"
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or ts <= 0:
        return {}, "ts khong hop le"
    if abs(time.time() * 1000 - ts) > 24 * 3600 * 1000:
        return {}, "ts lech qua 24h so voi gateway"
    good = {}
    for name, (lo, hi) in LIMITS.items():
        v = msg.get(name)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
            continue
        if lo <= v <= hi:
            good[name] = float(v)
    if not good:
        return {}, "khong co truong do nao hop le"
    return good, None


def track_sequence(dev, seq):
    if isinstance(seq, bool) or not isinstance(seq, int):
        return
    prev = last_seq.get(dev)
    if prev is not None:
        if seq == prev:
            stats["duplicate"] += 1
        elif seq > prev + 1:
            stats["lost"] += seq - prev - 1
    if prev is None or seq > prev:
        last_seq[dev] = seq


def main():
    client = common.get_client()
    common.ensure_buckets(client)
    write_api = client.write_api(write_options=SYNCHRONOUS)

    def on_connect(c, userdata, flags, rc, props=None):
        log.info("MQTT connected rc=%s, subscribe %s", rc, common.MQTT_TOPIC)
        c.subscribe(common.MQTT_TOPIC, qos=1)

    def on_message(c, userdata, m):
        recv_ms = time.time() * 1000
        stats["received"] += 1
        try:
            msg = json.loads(m.payload)
        except (ValueError, UnicodeDecodeError):
            stats["invalid"] += 1
            log.warning("JSON hong tren %s", m.topic)
            return
        fields, err = validate(msg)
        if err:
            stats["invalid"] += 1
            log.warning("bo ban tin (%s): %s", err, m.payload[:120])
            return
        dev = msg["device_id"]
        track_sequence(dev, msg.get("seq"))
        if isinstance(msg.get("seq"), int) and not isinstance(msg.get("seq"), bool):
            fields["seq"] = msg["seq"]
        fields["latency_ms"] = max(0.0, recv_ms - msg["ts"])
        p = Point(common.MEASUREMENT_RAW).tag("device_id", dev)
        p.tag("location", str(msg.get("location", "lab")))
        for k, v in fields.items():
            p.field(k, v)
        p.time(int(msg["ts"]), WritePrecision.MS)
        try:
            write_api.write(bucket=common.BUCKET_RAW, record=p)
            stats["written"] += 1
        except Exception as e:  # DB tam thoi loi: ghi log, khong lam sap collector
            log.error("ghi InfluxDB loi: %s", e)
        if stats["received"] % 20 == 0:
            log.info("stats %s", stats)

    if hasattr(mqtt, "CallbackAPIVersion"):
        mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    else:
        mq = mqtt.Client()
    mq.on_connect, mq.on_message = on_connect, on_message
    mq.reconnect_delay_set(1, 30)
    mq.connect(common.MQTT_HOST, common.MQTT_PORT, keepalive=30)
    try:
        mq.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        log.info("dung. stats cuoi: %s", stats)


if __name__ == "__main__":
    main()
