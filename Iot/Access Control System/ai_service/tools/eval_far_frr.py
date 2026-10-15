"""Đo FAR/FRR (nhận diện) và APCER/BPCER (chống giả mạo) để chọn ngưỡng cho ai_service.

Bước 1 - Thu dữ liệu từ webcam (mỗi lần chạy = 1 clip):
  python tools/eval_far_frr.py collect --label genuine  --claim B23DCAT296   # chính chủ đứng trước camera
  python tools/eval_far_frr.py collect --label impostor --claim B23DCAT296   # NGƯỜI KHÁC đứng trước camera, giả danh B23DCAT296
  python tools/eval_far_frr.py collect --label spoof    --claim B23DCAT296   # giơ ảnh/video của B23DCAT296 (điện thoại, ảnh in...)
  Phím: SPACE bắt đầu/tạm dừng ghi, Q kết thúc clip.

Bước 2 - Đánh giá:
  python tools/eval_far_frr.py evaluate
  -> in bảng kết quả, đề xuất ngưỡng, lưu results/scores.csv, results/summary.json, results/*.png

Thuật ngữ:
  FAR   (False Accept Rate)  : tỉ lệ người khác được nhận là chủ thẻ       (similarity >= ngưỡng)
  FRR   (False Reject Rate)  : tỉ lệ chủ thẻ bị từ chối nhầm               (similarity <  ngưỡng)
  APCER (Attack Presentation Classification Error Rate): ảnh/video giả bị coi là người thật (PAD >= ngưỡng)
  BPCER (Bona fide Presentation Classification Error Rate): người thật bị coi là giả (PAD < ngưỡng)
  EER   : điểm ngưỡng mà hai loại lỗi bằng nhau
"""
import argparse
import csv
import glob
import json
import os
import sys
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

LABELS = ("genuine", "impostor", "spoof")
PAD_WINDOW = 6          # giống ai_service: lấy trung bình 6 frame liên tiếp
THRESHOLDS = np.round(np.arange(0.0, 1.0001, 0.01), 2)


# ================================== THU DỮ LIỆU ==================================
def collect(args):
    clip = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(args.dataset, args.label, args.claim, clip)
    os.makedirs(out_dir, exist_ok=True)
    cam = int(args.camera) if str(args.camera).isdigit() else args.camera
    cap = cv2.VideoCapture(cam)
    recording, saved, last = False, 0, 0.0
    print(f"Clip: {out_dir}\nSPACE = bắt đầu/tạm dừng ghi, Q = kết thúc")
    while saved < args.frames:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.2)
            continue
        now = time.time()
        if recording and now - last >= args.interval:
            cv2.imwrite(os.path.join(out_dir, f"frame_{saved:04d}.jpg"), frame)
            saved += 1
            last = now
        view = frame.copy()
        color = (0, 0, 255) if recording else (0, 255, 255)
        cv2.putText(view, f"{args.label.upper()} / {args.claim}", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        cv2.putText(view, f"{'DANG GHI' if recording else 'TAM DUNG (SPACE)'}  {saved}/{args.frames}",
                    (15, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        cv2.imshow("Thu du lieu FAR/FRR", view)
        key = cv2.waitKey(1) & 0xFF
        if key == ord(" "):
            recording = not recording
        elif key == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()
    if saved == 0:
        os.rmdir(out_dir)
        print("Không lưu frame nào.")
    else:
        print(f"Đã lưu {saved} frame vào {out_dir}")


# ================================== CHẤM ĐIỂM ==================================
def load_embeddings(folder):
    embs = {}
    for path in glob.glob(os.path.join(folder, "*.npy")):
        e = np.load(path).astype(np.float32)
        embs[os.path.splitext(os.path.basename(path))[0]] = e / np.linalg.norm(e)
    return embs


def score_dataset(args):
    from insightface.app import FaceAnalysis
    from liveness import PassivePAD

    embs = load_embeddings(args.embeddings)
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    pad = PassivePAD(args.models)
    if not pad.enabled:
        print("[CẢNH BÁO] Không có model PAD -> bỏ qua phần chống giả mạo")

    rows = []
    for label in LABELS:
        for claim_dir in sorted(glob.glob(os.path.join(args.dataset, label, "*"))):
            claim = os.path.basename(claim_dir)
            if claim not in embs:
                print(f"[BỎ QUA] {label}/{claim}: không có embeddings/{claim}.npy")
                continue
            for clip_dir in sorted(glob.glob(os.path.join(claim_dir, "*"))):
                frames = sorted(glob.glob(os.path.join(clip_dir, "*.jpg")) + glob.glob(os.path.join(clip_dir, "*.png")))
                for i, path in enumerate(frames):
                    img = cv2.imread(path)
                    row = {"label": label, "claim": claim, "clip": os.path.basename(clip_dir), "frame": i,
                           "path": os.path.relpath(path, args.dataset), "status": "ok",
                           "similarity": "", "pad": "", "face_width": ""}
                    faces = app.get(img) if img is not None else []
                    if not faces:
                        row["status"] = "no_face"
                    else:
                        f = max(faces, key=lambda x: x.bbox[2] - x.bbox[0])   # mặt lớn nhất, như ai_service
                        width = float(f.bbox[2] - f.bbox[0])
                        row["face_width"] = round(width, 1)
                        row["similarity"] = round(float(np.dot(f.normed_embedding, embs[claim])), 4)
                        if pad.enabled:
                            row["pad"] = round(pad.score(img, f.bbox), 4)
                        if width < args.min_face_width:
                            row["status"] = "too_small"   # ai_service sẽ báo move_closer và không xử lý
                    rows.append(row)
                print(f"  {label}/{claim}/{os.path.basename(clip_dir)}: {len(frames)} frame")
    return rows


# ================================== CHỈ SỐ ==================================
def error_curve(pos, neg):
    """pos: điểm của lớp phải CHẤP NHẬN; neg: điểm của lớp phải TỪ CHỐI. Chấp nhận khi điểm >= ngưỡng.
    Trả về (tỉ lệ chấp nhận nhầm neg, tỉ lệ từ chối nhầm pos) theo từng ngưỡng."""
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    fa = np.array([(neg >= t).mean() if len(neg) else np.nan for t in THRESHOLDS])
    fr = np.array([(pos < t).mean() if len(pos) else np.nan for t in THRESHOLDS])
    return fa, fr


def eer(fa, fr):
    if np.isnan(fa).all() or np.isnan(fr).all():
        return None, None
    i = int(np.nanargmin(np.abs(fa - fr)))
    return float(THRESHOLDS[i]), float((fa[i] + fr[i]) / 2)


def pick_threshold(fa, fr, target):
    """Trong các ngưỡng có tỉ lệ chấp nhận nhầm <= target, lấy những ngưỡng có tỉ lệ từ chối nhầm thấp nhất,
    rồi chọn ngưỡng ở GIỮA dải đó để chừa biên an toàn cho cả hai phía."""
    if np.isnan(fa).all() or np.isnan(fr).all():
        return None
    ok = np.where(fa <= target)[0]
    if not len(ok):
        return None
    best = ok[fr[ok] <= fr[ok].min() + 1e-9]
    i = int(best[len(best) // 2])
    return {"threshold": float(THRESHOLDS[i]), "false_accept": float(fa[i]), "false_reject": float(fr[i]),
            "safe_range": [float(THRESHOLDS[best[0]]), float(THRESHOLDS[best[-1]])]}


def windowed_pad(rows):
    """Điểm PAD theo cửa sổ trượt 6 frame liên tiếp trong cùng clip - giống cách ai_service quyết định."""
    out = {}
    clips = {}
    for r in rows:
        if r["status"] == "ok" and r["pad"] != "":
            clips.setdefault((r["label"], r["claim"], r["clip"]), []).append(float(r["pad"]))
    for (label, _, _), s in clips.items():
        if len(s) >= PAD_WINDOW:
            means = np.convolve(s, np.ones(PAD_WINDOW) / PAD_WINDOW, mode="valid")
        else:
            means = [float(np.mean(s))]
        out.setdefault(label, []).extend(float(m) for m in means)
    return out


def at(fa, fr, t):
    i = int(np.argmin(np.abs(THRESHOLDS - t)))
    return float(fa[i]), float(fr[i])


def pct(x):
    return "  -   " if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:5.1f}%"


def evaluate(args):
    os.makedirs(args.out, exist_ok=True)
    rows = score_dataset(args)
    if not rows:
        print(f"Không có dữ liệu trong {args.dataset}. Chạy lệnh collect trước.")
        return

    csv_path = os.path.join(args.out, "scores.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if r["status"] == "ok"]
    by = lambda lab, key: [float(r[key]) for r in ok if r["label"] == lab and r[key] != ""]
    counts = {lab: {"frames": sum(r["label"] == lab for r in rows),
                    "scored": sum(r["label"] == lab for r in ok),
                    "no_face": sum(r["label"] == lab and r["status"] == "no_face" for r in rows),
                    "too_small": sum(r["label"] == lab and r["status"] == "too_small" for r in rows)}
              for lab in LABELS}

    # ---- Nhận diện: genuine (phải chấp nhận) vs impostor (phải từ chối) ----
    far, frr = error_curve(by("genuine", "similarity"), by("impostor", "similarity"))
    sim_eer_t, sim_eer = eer(far, frr)
    sim_pick = pick_threshold(far, frr, args.target)

    # ---- Chống giả mạo: người thật (genuine+impostor) vs spoof, theo cửa sổ 6 frame ----
    win = windowed_pad(rows)
    bona = win.get("genuine", []) + win.get("impostor", [])
    apcer, bpcer = error_curve(bona, win.get("spoof", []))
    pad_eer_t, pad_eer = eer(apcer, bpcer)
    pad_pick = pick_threshold(apcer, bpcer, args.target)

    # ---- Toàn hệ thống ở ngưỡng hiện tại (theo frame): chấp nhận khi sim >= ts VÀ pad >= tp ----
    def system_accept(lab, ts, tp):
        rs = [r for r in rows if r["label"] == lab]
        if not rs:
            return None
        acc = [r["status"] == "ok" and float(r["similarity"]) >= ts and (r["pad"] == "" or float(r["pad"]) >= tp)
               for r in rs]
        return float(np.mean(acc))

    cur_far, cur_frr = at(far, frr, args.sim_threshold)
    cur_apcer, cur_bpcer = at(apcer, bpcer, args.pad_threshold)

    print("\n================ SỐ LƯỢNG MẪU ================")
    for lab in LABELS:
        c = counts[lab]
        clips = len({(r["claim"], r["clip"]) for r in rows if r["label"] == lab})
        print(f"{lab:9s}: {clips:3d} clip, {c['frames']:5d} frame | chấm được {c['scored']:5d} | "
              f"không thấy mặt {c['no_face']:4d} | mặt quá nhỏ {c['too_small']:4d}")

    people = {lab: len({r["claim"] for r in rows if r["label"] == lab}) for lab in LABELS}
    if min(counts[lab]["scored"] for lab in LABELS) < 200 or min(people.values()) < 3:
        print("[CẢNH BÁO] Dữ liệu còn ít (nên có >= 200 frame mỗi loại và >= 3 người/chủ thẻ khác nhau).\n"
              "           Ngưỡng đề xuất dưới đây chưa đáng tin, chỉ để tham khảo.")

    print("\n================ NHẬN DIỆN (similarity, theo frame) ================")
    print(f"Ngưỡng hiện tại {args.sim_threshold:.2f}:  FAR {pct(cur_far)}   FRR {pct(cur_frr)}")
    if sim_eer_t is not None:
        print(f"EER {pct(sim_eer)} tại ngưỡng {sim_eer_t:.2f}")
    if sim_pick:
        print(f"ĐỀ XUẤT AI_SIM_THRESHOLD={sim_pick['threshold']:.2f}  (FAR {pct(sim_pick['false_accept'])} <= {args.target * 100:.1f}%, FRR {pct(sim_pick['false_reject'])}; "
              f"dải an toàn {sim_pick['safe_range'][0]:.2f}-{sim_pick['safe_range'][1]:.2f})")
    else:
        print("Chưa đề xuất được: cần cả dữ liệu genuine và impostor.")

    print("\n================ CHỐNG GIẢ MẠO (PAD, trung bình 6 frame) ================")
    print(f"Ngưỡng hiện tại {args.pad_threshold:.2f}:  APCER {pct(cur_apcer)}   BPCER {pct(cur_bpcer)}")
    if pad_eer_t is not None:
        print(f"EER {pct(pad_eer)} tại ngưỡng {pad_eer_t:.2f}")
    if pad_pick:
        print(f"ĐỀ XUẤT AI_PAD_THRESHOLD={pad_pick['threshold']:.2f}  (APCER {pct(pad_pick['false_accept'])} <= {args.target * 100:.1f}%, BPCER {pct(pad_pick['false_reject'])}; "
              f"dải an toàn {pad_pick['safe_range'][0]:.2f}-{pad_pick['safe_range'][1]:.2f})")
    else:
        print("Chưa đề xuất được: cần cả dữ liệu người thật và spoof.")

    print("\n================ TOÀN HỆ THỐNG (theo frame, chưa tính bước thách thức) ================")
    ts = args.sim_threshold
    tp = args.pad_threshold
    sys_rows = {lab: system_accept(lab, ts, tp) for lab in LABELS}
    print(f"Ở ngưỡng hiện tại (sim {ts:.2f}, pad {tp:.2f}):")
    print(f"  Chủ thẻ được chấp nhận        : {pct(sys_rows['genuine'])}   (càng cao càng tốt)")
    print(f"  Người khác lọt qua            : {pct(sys_rows['impostor'])}   (càng thấp càng tốt)")
    print(f"  Ảnh/video giả lọt qua         : {pct(sys_rows['spoof'])}   (càng thấp càng tốt)")
    print("Lưu ý: ai_service còn yêu cầu 5 frame khớp liên tiếp và thách thức quay đầu/chớp mắt,\n"
          "nên tỉ lệ lọt qua thực tế theo PHIÊN sẽ thấp hơn con số theo frame ở trên.")

    summary = {
        "counts": counts,
        "target_false_accept": args.target,
        "recognition": {"current_threshold": args.sim_threshold, "FAR": cur_far, "FRR": cur_frr,
                        "EER": sim_eer, "EER_threshold": sim_eer_t, "recommended": sim_pick},
        "anti_spoofing": {"current_threshold": args.pad_threshold, "APCER": cur_apcer, "BPCER": cur_bpcer,
                          "EER": pad_eer, "EER_threshold": pad_eer_t, "recommended": pad_pick,
                          "window_frames": PAD_WINDOW},
        "system_frame_level_at_current": sys_rows,
    }
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=lambda x: None if x != x else x)

    plot(args, far, frr, apcer, bpcer, by, win)
    print(f"\nĐã lưu: {csv_path}, {os.path.join(args.out, 'summary.json')} và biểu đồ .png trong {args.out}")


def plot(args, far, frr, apcer, bpcer, by, win):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("(Không có matplotlib -> bỏ qua biểu đồ)")
        return
    fig, ax = plt.subplots(2, 2, figsize=(12, 8))
    bins = np.linspace(-0.2, 1, 49)
    ax[0, 0].hist(by("genuine", "similarity"), bins=bins, alpha=.6, label="Chủ thẻ (genuine)")
    ax[0, 0].hist(by("impostor", "similarity"), bins=bins, alpha=.6, label="Người khác (impostor)")
    ax[0, 0].axvline(args.sim_threshold, color="k", ls="--", label=f"ngưỡng {args.sim_threshold:.2f}")
    ax[0, 0].set(title="Phân bố similarity", xlabel="cosine similarity", ylabel="số frame")
    ax[0, 0].legend()
    ax[0, 1].plot(THRESHOLDS, far * 100, label="FAR")
    ax[0, 1].plot(THRESHOLDS, frr * 100, label="FRR")
    ax[0, 1].axvline(args.sim_threshold, color="k", ls="--")
    ax[0, 1].set(title="Nhận diện: FAR/FRR theo ngưỡng", xlabel="ngưỡng similarity", ylabel="%")
    ax[0, 1].legend()
    bins = np.linspace(0, 1, 41)
    ax[1, 0].hist(win.get("genuine", []) + win.get("impostor", []), bins=bins, alpha=.6, label="Người thật")
    ax[1, 0].hist(win.get("spoof", []), bins=bins, alpha=.6, label="Giả mạo (spoof)")
    ax[1, 0].axvline(args.pad_threshold, color="k", ls="--", label=f"ngưỡng {args.pad_threshold:.2f}")
    ax[1, 0].set(title="Phân bố điểm PAD (trung bình 6 frame)", xlabel="xác suất người thật", ylabel="số cửa sổ")
    ax[1, 0].legend()
    ax[1, 1].plot(THRESHOLDS, apcer * 100, label="APCER (giả lọt qua)")
    ax[1, 1].plot(THRESHOLDS, bpcer * 100, label="BPCER (thật bị chặn)")
    ax[1, 1].axvline(args.pad_threshold, color="k", ls="--")
    ax[1, 1].set(title="Chống giả mạo: APCER/BPCER theo ngưỡng", xlabel="ngưỡng PAD", ylabel="%")
    ax[1, 1].legend()
    fig.tight_layout()
    fig.savefig(os.path.join(args.out, "far_frr.png"), dpi=120)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="ghi một clip từ webcam")
    c.add_argument("--label", required=True, choices=LABELS)
    c.add_argument("--claim", required=True, help="mã sinh viên được giả danh / chủ thẻ (phải có embeddings/<mã>.npy)")
    c.add_argument("--frames", type=int, default=120, help="số frame tối đa của clip (mặc định 120)")
    c.add_argument("--interval", type=float, default=0.15, help="giây giữa hai frame lưu (mặc định 0.15)")
    c.add_argument("--camera", default=os.environ.get("AI_CAMERA", "0"))
    c.add_argument("--dataset", default=os.path.join(ROOT, "eval_data"))

    e = sub.add_parser("evaluate", help="chấm điểm toàn bộ dữ liệu và đề xuất ngưỡng")
    e.add_argument("--dataset", default=os.path.join(ROOT, "eval_data"))
    e.add_argument("--embeddings", default=os.path.join(ROOT, "embeddings"))
    e.add_argument("--models", default=os.path.join(ROOT, "models"))
    e.add_argument("--out", default=os.path.join(ROOT, "results"))
    e.add_argument("--target", type=float, default=0.01, help="tỉ lệ chấp nhận nhầm tối đa chấp nhận được (mặc định 1%%)")
    e.add_argument("--sim-threshold", type=float, default=float(os.environ.get("AI_SIM_THRESHOLD", 0.50)))
    e.add_argument("--pad-threshold", type=float, default=float(os.environ.get("AI_PAD_THRESHOLD", 0.70)))
    e.add_argument("--min-face-width", type=float, default=float(os.environ.get("AI_MIN_FACE_WIDTH", 120)))

    args = ap.parse_args()
    collect(args) if args.cmd == "collect" else evaluate(args)


if __name__ == "__main__":
    main()
