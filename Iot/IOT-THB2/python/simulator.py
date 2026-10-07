"""Gia lap ESP32 + DHT22 + LDR: publish JSON len MQTT, co chu y chen loi de test pipeline.

Chen: outlier (spike), gia tri null (mat do), ban tin gui trung, ban tin bi mat.
Dung: python simulator.py --interval 1 --device esp32-sim-01
"""
import argparse
import json
import math
import random
import time

import paho.mqtt.client as mqtt

import common


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="esp32-sim-01")
    ap.add_argument("--interval", type=float, default=1.0, help="giay giua 2 ban tin")
    ap.add_argument("--count", type=int, default=0, help="0 = chay mai")
    ap.add_argument("--clean", action="store_true", help="khong chen loi")
    a = ap.parse_args()

    if hasattr(mqtt, "CallbackAPIVersion"):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    else:
        c = mqtt.Client()
    c.connect(common.MQTT_HOST, common.MQTT_PORT, 30)
    c.loop_start()
    topic = f"iot/lab2/{a.device}/telemetry"
    seq, t0 = 0, time.time()
    try:
        while a.count == 0 or seq < a.count:
            seq += 1
            t = time.time() - t0
            temp = 28 + 3 * math.sin(t / 120) + random.gauss(0, 0.2)
            hum = 65 - 8 * math.sin(t / 120) + random.gauss(0, 0.8)
            distance_cm = max(2.0, 150 + 100 * math.sin(t / 200) + random.gauss(0, 3))
            msg = {"device_id": a.device, "location": "lab", "seq": seq,
                   "ts": int(time.time() * 1000),
                   "temperature": round(temp, 2), "humidity": round(hum, 2),
                   "distance_cm": round(distance_cm, 1)}
            send_twice = False
            if not a.clean:
                r = random.random()
                if r < 0.03:
                    msg["temperature"] = round(temp + random.choice([-1, 1]) * random.uniform(15, 25), 2)
                elif r < 0.06:
                    msg["humidity"] = None
                elif r < 0.08:
                    msg["distance_cm"] = None
                elif r < 0.10:
                    send_twice = True
                if random.random() < 0.03:
                    time.sleep(a.interval)
                    continue  # mat ban tin: seq nhay coc
            payload = json.dumps(msg)
            c.publish(topic, payload, qos=1)
            if send_twice:
                c.publish(topic, payload, qos=1)
            print(payload)
            time.sleep(a.interval)
    except KeyboardInterrupt:
        pass
    c.loop_stop()


if __name__ == "__main__":
    main()
