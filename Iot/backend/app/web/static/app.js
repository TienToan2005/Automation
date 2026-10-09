// Tiện ích dùng chung cho các trang
async function api(path, options = {}) {
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
  return data;
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
