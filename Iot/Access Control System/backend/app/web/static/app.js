// Tiện ích dùng chung cho các trang
async function request(path, options = {}) {
  const opts = { credentials: "same-origin", ...options };
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { /* không có body */ }
  if (res.status === 401 && !path.includes("/auth/login")) { location.href = "/login"; throw new Error("401"); }
  if (!res.ok) {
    let msg = (data && data.detail) || res.statusText;
    if (typeof msg === "object") msg = msg.message || JSON.stringify(msg);
    const err = new Error(msg);
    err.data = data;
    throw err;
  }
  return { data, res };
}

async function api(path, options = {}) { return (await request(path, options)).data; }

// Như api() cho endpoint phân trang bằng limit/offset + header X-Total-Count: trả {items, total}
async function apiPage(path, options = {}) {
  const { data, res } = await request(path, options);
  return { items: data, total: Number(res.headers.get("X-Total-Count") ?? data.length) };
}

function fmtTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString("vi-VN", { hour12: false, timeZone: "Asia/Ho_Chi_Minh" });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const RESULT_LABEL = { granted: "Cho vào", denied: "Từ chối", pending: "Đang xác thực", timeout: "Hết giờ", cancelled: "Đã hủy" };
const EVENT_LABEL = { card_scan: "Quẹt thẻ", face_verify: "Quét mặt", exit_button: "Nút trong", remote_open: "Mở từ xa", admin_action: "Quản trị" };

function badge(result) {
  const cls = result === "granted" ? "ok" : result === "pending" ? "warn" : "bad";
  return `<span class="badge ${cls}">${esc(RESULT_LABEL[result] || result)}</span>`;
}

function toast(msg, isError) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "show " + (isError ? "bad" : "ok");
  setTimeout(() => (t.className = ""), 3500);
}

async function logout() {
  await api("/api/v1/auth/logout", { method: "POST" });
  location.href = "/login";
}

// Bảng nhật ký dùng cho cả trang /logs, dashboard và /me
function renderLogRows(items, showStudent) {
  if (!items.length) return `<tr><td colspan="7" class="muted">Chưa có dữ liệu</td></tr>`;
  return items.map(l => `<tr>
    <td>${fmtTime(l.ts)}</td>
    ${showStudent ? `<td>${esc(l.student_id || "")}</td>` : ""}
    <td>${esc(EVENT_LABEL[l.event] || l.event)}</td>
    <td>${badge(l.result)}</td>
    <td>${esc(l.reason)}</td>
    <td>${l.confidence != null ? l.confidence.toFixed(2) : ""}</td>
    <td>${esc(l.note)} ${l.snapshot_url ? `<a href="${l.snapshot_url}" target="_blank">ảnh</a>` : ""}</td>
  </tr>`).join("");
}

// Đăng ký khuôn mặt: chụp từ webcam hoặc chọn file, rồi gửi lên PUT /users/{id}/face
function setupFaceEnroll(userId, onDone) {
  const video = document.getElementById("cam");
  const shots = [];
  const strip = document.getElementById("shots");
  let stream = null;

  const refresh = () => {
    strip.innerHTML = shots.map((s, i) => `<div class="shot"><img src="${s.url}"><button data-i="${i}" title="Xóa">×</button></div>`).join("");
    document.getElementById("shotCount").textContent = shots.length;
    strip.querySelectorAll("button").forEach(b => b.onclick = () => { shots.splice(+b.dataset.i, 1); refresh(); });
  };
  const add = blob => { shots.push({ blob, url: URL.createObjectURL(blob) }); refresh(); };

  // Tắt hẳn camera để nhả thiết bị cho chương trình khác (ví dụ AI Service cần đọc cùng webcam)
  const stopCam = () => {
    if (stream) stream.getTracks().forEach(t => t.stop());
    stream = null;
    video.srcObject = null;
    video.style.display = "none";
    document.getElementById("camStop").style.display = "none";
    document.getElementById("camStart").style.display = "";
  };
  document.getElementById("camStart").onclick = async () => {
    if (stream) return;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 } });
      video.srcObject = stream; video.style.display = "block";
      document.getElementById("camStop").style.display = "";
      document.getElementById("camStart").style.display = "none";
    } catch (e) { toast("Không mở được camera: " + e.message, true); }
  };
  document.getElementById("camStop").onclick = stopCam;
  window.addEventListener("pagehide", stopCam);
  document.addEventListener("visibilitychange", () => { if (document.hidden) stopCam(); });
  document.getElementById("camShot").onclick = () => {
    if (!stream) return toast("Hãy bật camera trước", true);
    const c = document.createElement("canvas");
    c.width = video.videoWidth; c.height = video.videoHeight;
    c.getContext("2d").drawImage(video, 0, 0);
    c.toBlob(b => add(b), "image/jpeg", 0.92);
  };
  document.getElementById("fileInput").onchange = e => { [...e.target.files].forEach(add); e.target.value = ""; };

  document.getElementById("enrollSubmit").onclick = async () => {
    if (!shots.length) return toast("Chưa có ảnh nào", true);
    const fd = new FormData();
    shots.forEach((s, i) => fd.append("images", s.blob, `face${i}.jpg`));
    const btn = document.getElementById("enrollSubmit");
    btn.disabled = true; btn.textContent = "Đang xử lý...";
    const result = document.getElementById("enrollResult");
    try {
      const data = await api(`/api/v1/users/${userId}/face`, { method: "PUT", body: fd });
      toast(`Đăng ký thành công (${data.used_images} ảnh hợp lệ)`);
      result.innerHTML = data.errors.map(e => `<div class="muted">Ảnh ${e.image}: ${esc(e.message)}</div>`).join("");
      shots.length = 0; refresh();
      stopCam();
      onDone && onDone();
    } catch (e) {
      toast(e.message, true);
      const errs = (e.data && e.data.detail && e.data.detail.errors) || [];
      result.innerHTML = errs.map(x => `<div class="bad-text">Ảnh ${x.image}: ${esc(x.message)}</div>`).join("");
    } finally { btn.disabled = false; btn.textContent = "Gửi đăng ký"; }
  };
}

// Thêm người dùng + quét mặt trực tiếp bằng webcam (WebRTC).
// Mỗi lần bấm "Chụp & Quét": POST /api/v1/face/scan (Backend -> AI /api/ai/embed) để kiểm tra ngay;
// ảnh đạt được giữ lại, đủ số lượng thì "Thêm người dùng" gửi form + ảnh đến POST /api/v1/users/enroll.
function setupLiveEnroll(form, minImages, onDone) {
  const $ = id => document.getElementById(id);
  const video = $("cam"), box = $("camBox"), status = $("scanStatus");
  const btnStart = $("camStart"), btnStop = $("camStop"), btnShot = $("camShot"), btnAdd = $("addSubmit");
  const shots = [];          // chỉ chứa ảnh đã được AI chấp nhận: { blob, url }
  let stream = null, busy = false;

  const say = (text, kind = "") => { status.textContent = text; status.className = "scan-status " + kind; };
  const refresh = () => {
    $("shots").innerHTML = shots.map((s, i) => `<div class="shot ok"><img src="${s.url}" alt="Ảnh ${i + 1}"><button type="button" data-i="${i}" title="Xóa ảnh">×</button></div>`).join("");
    $("shots").querySelectorAll("button").forEach(b => b.onclick = () => { URL.revokeObjectURL(shots[+b.dataset.i].url); shots.splice(+b.dataset.i, 1); refresh(); });
    $("okCount").textContent = shots.length;
    btnAdd.disabled = busy || shots.length < minImages;
    btnShot.disabled = busy || !stream || shots.length >= 8;
  };
  const setBusy = v => { busy = v; refresh(); };

  // Tắt hẳn camera để nhả thiết bị (AI Service cũng cần đọc cùng webcam khi có phiên quét thẻ)
  const stopCam = () => {
    if (stream) stream.getTracks().forEach(t => t.stop());
    stream = null; video.srcObject = null;
    box.classList.remove("live");
    btnStop.style.display = "none"; btnStart.style.display = "";
    refresh();
  };
  btnStart.onclick = async () => {
    if (stream) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return say("Trình duyệt không cho dùng camera. Hãy mở trang qua http://localhost:... hoặc HTTPS.", "bad");
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" }, audio: false });
      video.srcObject = stream;
      box.classList.add("live"); btnStop.style.display = ""; btnStart.style.display = "none";
      say('Đặt mặt vào khung tròn rồi bấm "Chụp & Quét khuôn mặt".');
      refresh();
    } catch (e) {
      const msg = e.name === "NotAllowedError" ? "Bạn đã chặn quyền camera cho trang này"
        : e.name === "NotFoundError" ? "Không tìm thấy camera"
        : e.name === "NotReadableError" ? "Camera đang được chương trình khác sử dụng (có thể là AI Service)" : e.message;
      say("Không mở được camera: " + msg, "bad");
    }
  };
  btnStop.onclick = stopCam;
  window.addEventListener("pagehide", stopCam);
  document.addEventListener("visibilitychange", () => { if (document.hidden) stopCam(); });

  btnShot.onclick = async () => {
    if (!stream || busy || !video.videoWidth) return say("Camera chưa sẵn sàng, đợi một chút rồi thử lại.", "bad");
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);          // khung gốc (không lật) để AI phân tích
    const blob = await new Promise(r => canvas.toBlob(r, "image/jpeg", 0.92));
    setBusy(true); say("Đang xử lý ảnh, vui lòng giữ yên...", "busy");
    try {
      const fd = new FormData(); fd.append("image", blob, "frame.jpg");
      const r = await api("/api/v1/face/scan", { method: "POST", body: fd });
      if (r.ok) {
        shots.push({ blob, url: URL.createObjectURL(blob) });
        say(shots.length >= minImages
          ? `Thành công: ảnh hợp lệ (${shots.length}). Đã đủ ảnh, có thể bấm "Thêm người dùng".`
          : `Thành công: ảnh hợp lệ (${shots.length}/${minImages}). Hãy chụp thêm, hơi xoay mặt nhẹ.`, "ok");
      } else {
        say("Lỗi: " + r.message + (r.reason === "blurry" && r.sharpness != null ? ` (độ nét ${r.sharpness})` : ""), "bad");
      }
    } catch (e) { say("Lỗi: " + e.message, "bad"); }
    finally { setBusy(false); }
  };

  form.onsubmit = async e => {
    e.preventDefault();
    if (shots.length < minImages) return say(`Cần tối thiểu ${minImages} ảnh khuôn mặt hợp lệ.`, "bad");
    const fd = new FormData(form);
    if (!fd.get("card_uid")) fd.delete("card_uid");
    shots.forEach((s, i) => fd.append("images", s.blob, `face${i}.jpg`));
    setBusy(true); btnAdd.textContent = "Đang lưu..."; say("Đang xử lý và lưu người dùng...", "busy");
    try {
      const data = await api("/api/v1/users/enroll", { method: "POST", body: fd });
      toast(`Đã thêm ${data.user.full_name}, đăng ký khuôn mặt từ ${data.used_images} ảnh`);
      say("Thành công: đã thêm người dùng và lưu dữ liệu khuôn mặt.", "ok");
      shots.forEach(s => URL.revokeObjectURL(s.url)); shots.length = 0;
      form.reset(); stopCam(); onDone && onDone();
    } catch (err) {
      const errs = (err.data && err.data.detail && err.data.detail.errors) || [];
      say("Lỗi: " + err.message + (errs.length ? " — " + errs.map(x => `ảnh ${x.image}: ${x.message}`).join("; ") : ""), "bad");
    } finally { btnAdd.textContent = "Thêm người dùng"; setBusy(false); }
  };
  refresh();
}


// Biểu đồ cột SVG không cần thư viện ngoài. series: [{name, color, values[]}], stacked = xếp chồng.
// Màu truyền dạng "var(--ok)"; mỗi cột có <title> làm chú thích khi rê chuột.
function barChart(el, labels, series, { stacked = false, height = 220, ariaLabel = "Biểu đồ cột" } = {}) {
  const W = 720, H = height, L = 40, R = 8, T = 10, B = 26, n = labels.length;
  const totals = labels.map((_, i) => stacked ? series.reduce((a, x) => a + x.values[i], 0) : Math.max(...series.map(x => x.values[i])));
  const max = Math.max(...totals, 0);
  if (!n || !max) { el.innerHTML = '<p class="muted">Không có dữ liệu trong khoảng này.</p>'; return; }
  const raw = max / 4, mag = 10 ** Math.floor(Math.log10(raw)), step = [1, 2, 5, 10].map(m => m * mag).find(v => v >= raw);
  const top = Math.ceil(max / step) * step, plotW = W - L - R, plotH = H - T - B, gw = plotW / n;
  const y = v => T + plotH - (v / top) * plotH;
  const every = Math.ceil(n / 12);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(ariaLabel)}" class="chart">`;
  for (let v = 0; v <= top; v += step) {
    svg += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="grid"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" class="tick">${v}</text>`;
  }
  labels.forEach((label, i) => {
    const x0 = L + i * gw, bw = stacked ? gw * 0.7 : (gw * 0.8) / series.length;
    let acc = 0;
    series.forEach((s, k) => {
      const v = s.values[i];
      const x = stacked ? x0 + gw * 0.15 : x0 + gw * 0.1 + k * bw;
      const base = stacked ? acc : 0;
      if (v > 0) {
        svg += `<rect x="${x.toFixed(1)}" y="${y(base + v).toFixed(1)}" width="${bw.toFixed(1)}" height="${(y(base) - y(base + v)).toFixed(1)}" style="fill:${s.color}"><title>${esc(label)}: ${esc(s.name)} ${v}</title></rect>`;
      }
      acc += v;
    });
    if (i % every === 0) svg += `<text x="${(x0 + gw / 2).toFixed(1)}" y="${H - 8}" text-anchor="middle" class="tick">${esc(label)}</text>`;
  });
  svg += "</svg>";
  const legend = series.map(s => `<span class="legend-item"><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("");
  el.innerHTML = `<div class="legend">${legend}</div>${svg}`;
}
