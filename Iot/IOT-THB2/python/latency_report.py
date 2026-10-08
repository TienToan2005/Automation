"""In thong ke do tre end-to-end va ti le mat ban tin tu InfluxDB (dung cho bao cao).
Chay: python latency_report.py --range 1h
"""
import argparse

import pandas as pd

import common


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--range", default="1h")
    a = ap.parse_args()
    flux = f'''
from(bucket: "{common.BUCKET_RAW}")
  |> range(start: -{a.range})
  |> filter(fn: (r) => r._measurement == "{common.MEASUREMENT_RAW}")
  |> filter(fn: (r) => r._field == "latency_ms" or r._field == "seq")
  |> pivot(rowKey: ["_time", "device_id"], columnKey: ["_field"], valueColumn: "_value")
'''
    df = common.get_client().query_api().query_data_frame(flux)
    if isinstance(df, list):
        df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
    if df.empty:
        print("Khong co du lieu")
        return
    for dev, g in df.groupby("device_id"):
        lat = g["latency_ms"].dropna()
        seq = g["seq"].dropna().astype(int)
        exp = int(seq.max() - seq.min() + 1)
        print(f"== {dev} ({a.range}) ==")
        print(f"  so ban ghi        : {len(g)}")
        print(f"  latency mean/P50  : {lat.mean():.1f} / {lat.quantile(.5):.1f} ms")
        print(f"  latency P95/P99   : {lat.quantile(.95):.1f} / {lat.quantile(.99):.1f} ms")
        print(f"  latency min/max   : {lat.min():.1f} / {lat.max():.1f} ms")
        print(f"  ban tin du kien   : {exp}, da luu: {seq.nunique()}, mat: {100 * (1 - seq.nunique() / exp):.1f}%")


if __name__ == "__main__":
    main()
