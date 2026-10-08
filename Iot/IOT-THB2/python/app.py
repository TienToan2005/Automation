"""Ứng dụng Streamlit: giám sát real-time, dữ liệu đã tiền xử lý, độ trễ end-to-end, chất lượng dữ liệu.
Chạy: streamlit run app.py
"""
import pandas as pd
import plotly.express as px
import streamlit as st

import common

st.set_page_config(page_title="IoT Lab 2", layout="wide")

NHAN = {
    "time": "Thời gian",
    "temperature": "Nhiệt độ (°C)",
    "humidity": "Độ ẩm (%)",
    "distance_cm": "Khoảng cách (cm)",
    "latency_ms": "Độ trễ (ms)",
    "device_id": "Thiết bị",
}


@st.cache_resource
def client():
    return common.get_client()


def query(bucket, measurement, fields, rng, device=None):
    f = " or ".join(f'r._field == "{x}"' for x in fields)
    dev = f'|> filter(fn: (r) => r.device_id == "{device}")' if device else ""
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -{rng})
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> filter(fn: (r) => {f})
  {dev}
  |> pivot(rowKey: ["_time", "device_id"], columnKey: ["_field"], valueColumn: "_value")
'''
    df = client().query_api().query_data_frame(flux)
    if isinstance(df, list):
        df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
    if df.empty:
        return df
    return df.drop(columns=[c for c in df.columns if c.startswith("_") and c != "_time"] +
                   ["result", "table"], errors="ignore").rename(columns={"_time": "time"}).sort_values("time")


st.title("IoT Lab 2 – Giám sát dữ liệu cảm biến")
with st.sidebar:
    st.header("Tùy chọn")
    rng = st.selectbox("Khoảng thời gian", ["5m", "15m", "1h", "6h", "24h"], index=2,
                       help="m = phút, h = giờ")
    live = st.toggle("Tự động làm mới (5 giây)", value=True)

@st.fragment(run_every=5 if live else None)
def dashboard(rng):
    """Chỉ phần này được làm mới định kỳ, không tải lại cả trang nên không bị nhấp nháy."""
    try:
        raw = query(common.BUCKET_RAW, common.MEASUREMENT_RAW,
                    ["temperature", "humidity", "distance_cm", "latency_ms", "seq"], rng)
    except Exception as e:
        st.error(f"Không kết nối được InfluxDB: {e}")
        return

    if raw.empty:
        st.warning("Chưa có dữ liệu. Hãy chạy collector.py và thiết bị (Wokwi hoặc simulator.py).")
        return

    tab1, tab2, tab3, tab4 = st.tabs(["Thời gian thực", "Dữ liệu đã xử lý", "Độ trễ", "Chất lượng dữ liệu"])

    with tab1:
        last = raw.iloc[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Nhiệt độ (°C)", f"{last.get('temperature', float('nan')):.1f}")
        c2.metric("Độ ẩm (%)", f"{last.get('humidity', float('nan')):.1f}")
        c3.metric("Khoảng cách (cm)", f"{last.get('distance_cm', float('nan')):.0f}")
        c4.metric("Số bản ghi", len(raw))
        for col, title in [("temperature", "Nhiệt độ"), ("humidity", "Độ ẩm"), ("distance_cm", "Khoảng cách")]:
            st.plotly_chart(px.line(raw, x="time", y=col, color="device_id", title=title, labels=NHAN),
                            use_container_width=True)

    with tab2:
        proc = query(common.BUCKET_PROCESSED, common.MEASUREMENT_CLEAN,
                     ["temperature", "temperature_roll", "temperature_delta", "temperature_norm",
                      "humidity", "humidity_roll", "distance_cm", "distance_cm_roll", "n_outliers"], rng)
        if proc.empty:
            st.info("Chưa có dữ liệu đã xử lý. Chạy: python preprocess.py --range 1h --window 10s")
        else:
            metric = st.selectbox("Đại lượng", ["temperature", "humidity", "distance_cm"],
                                  format_func=lambda k: NHAN[k])
            both = raw[["time", metric]]
            clean = proc[["time", metric, f"{metric}_roll"]]
            fig = px.line(both, x="time", y=metric, labels=NHAN,
                          title=f"{NHAN[metric]}: dữ liệu thô so với đã resample và trung bình trượt")
            fig.update_traces(name="Dữ liệu thô", showlegend=True, opacity=0.4)
            fig.add_scatter(x=clean["time"], y=clean[metric], name="Sau resample", mode="lines")
            fig.add_scatter(x=clean["time"], y=clean[f"{metric}_roll"], name="Trung bình trượt", mode="lines")
            st.plotly_chart(fig, use_container_width=True)
            st.caption(f"Số outlier đã loại: {int(proc['n_outliers'].sum())}")
            st.dataframe(proc.tail(30), use_container_width=True)

    with tab3:
        lat = raw["latency_ms"].dropna()
        if lat.empty:
            st.info("Chưa có dữ liệu độ trễ.")
        else:
            a, b, c, d = st.columns(4)
            a.metric("Trung bình (ms)", f"{lat.mean():.1f}")
            b.metric("P50 (ms)", f"{lat.quantile(.5):.1f}")
            c.metric("P95 (ms)", f"{lat.quantile(.95):.1f}")
            d.metric("Lớn nhất (ms)", f"{lat.max():.1f}")
            st.plotly_chart(px.line(raw, x="time", y="latency_ms", labels=NHAN,
                                    title="Độ trễ từ thiết bị đến collector (ms)"), use_container_width=True)
            st.plotly_chart(px.histogram(lat, nbins=40, labels={"value": "Độ trễ (ms)", "count": "Số bản tin"},
                                         title="Phân bố độ trễ"), use_container_width=True)
            st.caption("Độ trễ = thời điểm collector nhận − timestamp do thiết bị gán. "
                       "Chỉ chính xác khi đồng hồ hai bên được đồng bộ (NTP). "
                       "Lưu ý: Wokwi chạy chậm hơn thời gian thực nên độ trễ đo từ Wokwi bị lệch tăng dần.")

    with tab4:
        for dev, g in raw.groupby("device_id"):
            seq = g["seq"].dropna().astype(int)
            expected = int(seq.max() - seq.min() + 1) if len(seq) else 0
            got = seq.nunique()
            loss = 100 * (1 - got / expected) if expected else 0
            st.subheader(dev)
            a, b, c = st.columns(3)
            a.metric("Bản tin dự kiến", expected)
            b.metric("Bản tin đã lưu", got)
            c.metric("Tỉ lệ mất", f"{loss:.1f}%")
            miss = g[["temperature", "humidity", "distance_cm"]].isna().mean() * 100
            st.bar_chart(miss.rename(index=NHAN).rename("% thiếu"))



dashboard(rng)
