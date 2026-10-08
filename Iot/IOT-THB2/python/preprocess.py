"""Tien xu ly du lieu: doc InfluxDB -> lam sach -> outlier -> resample -> dac trung -> chuan hoa -> ghi lai.

Vi du:
  python preprocess.py --range 1h --window 10s --method iqr
  python preprocess.py --range 6h --window 1m --method zscore --loop 60   # chay lap moi 60s
"""
import argparse
import time

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler

import common

SENSORS = ["temperature", "humidity", "distance_cm"]


def read_raw(client, rng):
    fields = " or ".join(f'r._field == "{f}"' for f in SENSORS)
    flux = f'''
from(bucket: "{common.BUCKET_RAW}")
  |> range(start: -{rng})
  |> filter(fn: (r) => r._measurement == "{common.MEASUREMENT_RAW}")
  |> filter(fn: (r) => {fields})
  |> pivot(rowKey: ["_time", "device_id"], columnKey: ["_field"], valueColumn: "_value")
'''
    df = client.query_api().query_data_frame(flux)
    if isinstance(df, list):
        df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
    if df.empty:
        return df
    keep = ["_time", "device_id"] + [c for c in SENSORS if c in df.columns]
    df = df[keep].rename(columns={"_time": "time"})
    for c in SENSORS:
        if c not in df.columns:
            df[c] = np.nan
    return df.sort_values("time")


def detect_outliers(s, method, k):
    """Tra ve mask True tai vi tri outlier."""
    x = s.dropna()
    if len(x) < 8:
        return pd.Series(False, index=s.index)
    if method == "zscore":
        sd = x.std()
        z = (s - x.mean()) / (sd if sd > 0 else 1.0)
        return z.abs() > k
    q1, q3 = x.quantile(0.25), x.quantile(0.75)
    iqr = q3 - q1
    return (s < q1 - k * iqr) | (s > q3 + k * iqr)


def process_device(g, a):
    report = {"raw_rows": len(g)}
    g = g.drop_duplicates(subset="time").set_index("time").sort_index()
    g = g[SENSORS]
    report["after_dedup"] = len(g)

    outlier_any = pd.Series(False, index=g.index)
    for c in SENSORS:
        mask = detect_outliers(g[c], a.method, a.k)
        report[f"outliers_{c}"] = int(mask.sum())
        outlier_any |= mask
        g.loc[mask, c] = np.nan
    report["missing_before"] = int(g.isna().sum().sum())

    out = g.resample(a.window).mean()
    out["n_samples"] = g[SENSORS[0]].resample(a.window).size()
    out["n_outliers"] = outlier_any.resample(a.window).sum()
    report["empty_windows"] = int((out["n_samples"] == 0).sum())

    # Missing values: noi suy theo thoi gian (toi da a.max_gap cua so lien tiep), con lai de NaN
    out[SENSORS] = out[SENSORS].interpolate(method="time", limit=a.max_gap, limit_area="inside")
    report["missing_after"] = int(out[SENSORS].isna().sum().sum())
    out = out.dropna(subset=SENSORS)

    for c in SENSORS:
        out[f"{c}_roll"] = out[c].rolling(a.roll, min_periods=1).mean()
        out[f"{c}_delta"] = out[c].diff().fillna(0.0)
    if len(out) >= 2:
        out[[f"{c}_norm" for c in SENSORS]] = MinMaxScaler().fit_transform(out[SENSORS])
    else:
        for c in SENSORS:
            out[f"{c}_norm"] = 0.0
    out["n_samples"] = out["n_samples"].astype(float)
    out["n_outliers"] = out["n_outliers"].astype(float)
    report["out_rows"] = len(out)
    return out, report


def run_once(client, a):
    raw = read_raw(client, a.range)
    if raw.empty:
        print("[preprocess] chua co du lieu trong khoang thoi gian nay")
        return
    write_api = client.write_api()
    for dev, g in raw.groupby("device_id"):
        out, rep = process_device(g.copy(), a)
        print(f"[preprocess] {dev}: {rep}")
        if out.empty:
            continue
        out = out.assign(device_id=dev)
        write_api.write(bucket=common.BUCKET_PROCESSED, record=out,
                        data_frame_measurement_name=common.MEASUREMENT_CLEAN,
                        data_frame_tag_columns=["device_id"])
    write_api.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--range", default="1h", help="khoang thoi gian doc, vd 30m, 6h, 2d")
    ap.add_argument("--window", default="10s", help="cua so resample, vd 10s, 1min")
    ap.add_argument("--method", choices=["iqr", "zscore"], default="iqr")
    ap.add_argument("--k", type=float, default=None, help="IQR: 1.5 mac dinh; Z-score: 3.0 mac dinh")
    ap.add_argument("--max-gap", type=int, default=3, help="so cua so thieu lien tiep toi da duoc noi suy")
    ap.add_argument("--roll", type=int, default=6, help="cua so rolling mean (so diem)")
    ap.add_argument("--loop", type=int, default=0, help="lap lai moi N giay (0 = chay 1 lan)")
    a = ap.parse_args()
    if a.k is None:
        a.k = 3.0 if a.method == "zscore" else 1.5

    client = common.get_client()
    common.ensure_buckets(client)
    while True:
        run_once(client, a)
        if not a.loop:
            break
        time.sleep(a.loop)


if __name__ == "__main__":
    main()
