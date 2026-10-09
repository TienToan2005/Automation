"""Hiệu chỉnh thách thức người thật (chớp mắt, quay đầu) trên CHÍNH camera và gương mặt của bạn.

Chạy:  python tools/calibrate_liveness.py        (từ thư mục ai_service, AI Service đang chạy hay không đều được)

Công cụ mở webcam, yêu cầu bạn làm lần lượt: nhìn thẳng -> chớp mắt -> quay sang phải -> quay sang trái,
đo giá trị thật rồi in ra các biến môi trường nên đặt. Chỉ lưu SỐ ĐO, không lưu ảnh.
Cửa sổ hiển thị kiểu soi gương: "phải" của bạn nằm bên phải màn hình. Bấm Q để thoát.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

import main as ai
from liveness import BLINK_CLOSE_RATIO, BLINK_OPEN_RATIO, TURN_DELTA, face_ear, face_turn_offset

PREP_S = 2.0
PHASES = [
    ("still", "NHIN THANG, GIU YEN", 4.0),
    ("blink", "CHOP MAT TU NHIEN 5-6 LAN", 9.0),
    ("right", "QUAY DAU SANG PHAI cua ban, giu 1 giay", 5.0),
    ("left", "QUAY DAU SANG TRAI cua ban, giu 1 giay", 5.0),
]


def _stats(a):
    a = np.asarray(a, dtype=float)
    return a.min(), np.percentile(a, 5), np.median(a), np.percentile(a, 95), a.max()


def analyze(rec, fps):
    """rec: {phase: [(ear, offset), ...]} -> (danh sách dòng báo cáo, dict biến môi trường đề xuất)."""
    out, env = [], {}
    need = [p for p, _, _ in PHASES]
    for p in need:
        if len(rec.get(p, [])) < 5:
            out.append(f"[!] Pha '{p}' chỉ có {len(rec.get(p, []))} khung hình có mặt: thiếu dữ liệu, hãy chạy lại.")
            return out, env

    out.append(f"Tốc độ xử lý: {fps:.1f} khung hình/giây" + ("  <-- CHẬM (cần >= 3)" if fps < 3 else ""))

    # ---- Chớp mắt ----
    still_ear = [r[0] for r in rec["still"]]
    blink_ear = [r[0] for r in rec["blink"]]
    open_ear = float(np.median(still_ear))                              # mức "mắt mở": trung vị lúc đứng yên (không bị khung rác kéo lệch)
    bad = sum(abs(e - open_ear) > 0.3 * open_ear for e in still_ear)
    if bad:
        out.append(f"[!] Lúc đứng yên có {bad}/{len(still_ear)} khung hình EAR lệch quá 30% so với trung vị: landmark bị nhiễu/nhảy.")
    noise = float(np.median(np.abs(np.asarray(still_ear) - open_ear))) / open_ear   # độ lệch trung vị (bền với khung rác)
    ratios = np.asarray(blink_ear) / open_ear
    lo = float(np.percentile(ratios, 3))                                # mức thấp nhất đáng tin (bỏ nhiễu)
    out.append(f"Chớp mắt: EAR mắt mở = {open_ear:.3f}; khi chớp xuống còn {lo:.2f} lần mức mở "
               f"(nhiễu lúc đứng yên: ±{noise*100:.1f}%)")
    n_closed = {t: int((ratios < t).sum()) for t in (0.7, 0.8, 0.85)}
    out.append(f"          số khung hình có tỉ lệ < 0.70: {n_closed[0.7]}, < 0.80: {n_closed[0.8]}, < 0.85: {n_closed[0.85]} "
               f"(trên {len(ratios)} khung)")
    if lo >= 0.93:
        out.append("[!] Landmark 3D gần như không phản ứng với chớp mắt của bạn (tỉ lệ không xuống dưới 0.93).")
        out.append("    Hãy chớp MẠNH và chậm hơn, nhìn thẳng, đủ sáng; nếu vẫn vậy thì cần đổi phương pháp đo chớp mắt.")
    else:
        close = round(min(max(lo + 0.5 * (1 - lo), lo + 0.05), 0.9), 2)   # nửa đường giữa mắt mở và mắt nhắm sâu nhất
        open_r = round(min(close + 0.12, 0.97), 2)
        env["AI_BLINK_CLOSE_RATIO"], env["AI_BLINK_OPEN_RATIO"] = close, open_r
        out.append(f"          -> đề xuất AI_BLINK_CLOSE_RATIO={close}, AI_BLINK_OPEN_RATIO={open_r} "
                   f"(đang dùng {BLINK_CLOSE_RATIO}/{BLINK_OPEN_RATIO})")
        if noise * 3 > (1 - close):
            out.append("[!] Nhiễu lúc đứng yên khá lớn so với biên độ chớp mắt: có thể bị qua nhầm; hãy cải thiện ánh sáng.")

    # ---- Quay đầu ----
    base = float(np.median([r[1] for r in rec["still"]]))
    # Lấy phân vị 5/95 thay vì cực trị để khung rác đơn lẻ không quyết định kết quả
    right_d = np.asarray([r[1] - base for r in rec["right"]])
    left_d = np.asarray([r[1] - base for r in rec["left"]])
    pick = lambda d: float(np.percentile(d, 5) if abs(np.percentile(d, 5)) > abs(np.percentile(d, 95)) else np.percentile(d, 95))
    right, left = pick(right_d), pick(left_d)
    out.append(f"Quay đầu: sang PHẢI đạt {right:+.2f}, sang TRÁI đạt {left:+.2f} (đơn vị: khoảng cách hai mắt; "
               f"đúng chiều là phải ÂM, trái DƯƠNG)")
    if right > 0.03 and left < -0.03:
        env["AI_YAW_LEFT_SIGN"] = -1
        out.append("[!] Hai chiều bị ngược -> đề xuất AI_YAW_LEFT_SIGN=-1")
    else:
        small = min(abs(right), abs(left))
        if right >= -0.03 or left <= 0.03:
            out.append("[!] Một chiều gần như không đo được: hãy quay đầu rõ hơn (khoảng 30-40 độ) rồi chạy lại.")
        else:
            turn = round(min(max(small * 0.5, 0.06), 0.25), 2)
            env["AI_TURN_DELTA"] = turn
            out.append(f"          -> đề xuất AI_TURN_DELTA={turn} (đang dùng {TURN_DELTA})")
    return out, env


def main():
    ai.init_models()
    stream = ai.CameraStream(ai.CAMERA_SOURCE)
    if not stream.wait_first_frame(5):
        print("Không mở được camera (đang bị chương trình khác giữ? thử đặt AI_CAMERA=1).")
        return 1
    rec = {p: [] for p, _, _ in PHASES}
    n_frames, t0, last_seq = 0, time.time(), -1
    quit_ = False
    try:
        for phase, text, dur in PHASES:
            start = time.time()
            print(f"\n>>> {text}  (chuẩn bị {PREP_S:.0f}s rồi đo {dur:.0f}s)")
            while time.time() - start < PREP_S + dur and not quit_:
                frame, seq = stream.latest()
                if frame is None or seq == last_seq:
                    time.sleep(0.005)
                    continue
                last_seq = seq
                n_frames += 1
                faces = ai.face_app.get(frame)
                face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0])) if faces else None
                ear = face_ear(face) if face is not None else None
                off = face_turn_offset(face) if face is not None else None
                elapsed = time.time() - start
                recording = elapsed >= PREP_S
                if recording and ear is not None and off is not None:
                    width = float(face.bbox[2] - face.bbox[0])
                    rec[phase].append((ear, off, len(faces), float(face.det_score), width))
                view = cv2.flip(frame, 1)
                cv2.putText(view, text, (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                state = f"DO... {PREP_S + dur - elapsed:.0f}s" if recording else f"CHUAN BI {PREP_S - elapsed:.0f}s"
                cv2.putText(view, state, (15, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0) if recording else (0, 165, 255), 2)
                if ear is not None:
                    cv2.putText(view, f"EAR {ear:.3f}  turn {off:+.2f}", (15, view.shape[0] - 15),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
                else:
                    cv2.putText(view, "KHONG THAY MAT", (15, view.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                cv2.imshow("Calibrate liveness", view)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    quit_ = True
            if quit_:
                break
    finally:
        stream.release()
        cv2.destroyAllWindows()
    if quit_:
        print("Đã thoát giữa chừng.")
        return 1

    fps = n_frames / max(time.time() - t0, 1e-6)
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration_last.csv")
    with open(csv_path, "w", encoding="utf-8") as fh:
        fh.write("phase,ear,turn_offset,n_faces,det_score,face_width\n")
        for phase, rows in rec.items():
            for r in rows:
                fh.write(f"{phase},{r[0]:.4f},{r[1]:.4f},{r[2]},{r[3]:.3f},{r[4]:.0f}\n")
    print(f"Đã lưu số đo thô từng khung hình: {csv_path}")
    print("\n================ KẾT QUẢ HIỆU CHỈNH ================")
    lines, env = analyze(rec, fps)
    print("\n".join(lines))
    if env:
        print("\nĐặt trước khi chạy AI Service (PowerShell):")
        for k, v in env.items():
            print(f'  $env:{k}="{v}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
