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

const RESULT_LABEL = { granted: "Thành công", denied: "Từ chối", pending: "Đang xác thực", timeout: "Hết giờ", cancelled: "Đã hủy" };
const EVENT_LABEL = { card_scan: "Quẹt thẻ", face_verify: "Quét mặt", exit_button: "Nút trong", remote_open: "Mở từ xa", admin_action: "Quản trị" };

function badge(result) {
  const cls = result === "granted" ? "good" : result === "pending" ? "warn" : "bad";
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

// Thêm người dùng + quét khuôn mặt có hướng dẫn (kiểu eKYC ngân hàng): nhìn thẳng -> quay trái -> quay phải -> chớp mắt.
// Camera của trình duyệt (WebRTC) gửi liên tục từng khung hình tới POST /api/v1/face/guide/frame (Backend -> AI /api/ai/enroll/frame).
// AI kiểm tra chất lượng + tư thế và báo khung nào vừa hoàn thành bước nào (passed_step); trang giữ đúng khung đó làm ảnh đăng ký.
// Đủ các bước thì "Thêm người dùng" gửi form + ảnh tới POST /api/v1/users/enroll (AI kiểm tra lại từng ảnh rồi lấy trung bình vector).
function setupGuidedEnroll(form, minImages, onDone) {
  const $ = id => document.getElementById(id);
  const video = $("cam"), stage = $("camBox"), status = $("scanStatus"), instruction = $("scanInstruction"), hintEl = $("scanHint");
  const btnStart = $("scanStart"), btnRetry = $("scanRetry"), btnSkip = $("scanSkip"), btnStop = $("camStop"), btnAdd = $("addSubmit");
  const FRAME_INTERVAL_MS = 120, FRAME_WIDTH = 960;
  const canvas = document.createElement("canvas");
  const shots = [];            // { step, label, blob, url }: đúng khung đã hoàn thành từng bước
  let stream = null, sessionId = null, running = false, busy = false, finished = false, runToken = 0, flashUntil = 0, lastState = null;

  const say = (text, kind = "") => { status.textContent = text; status.className = "scan-status " + kind; };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const errCode = e => e && e.data && e.data.detail && e.data.detail.code;

  // ---------- hiển thị ----------
  function renderShots() {
    $("shots").innerHTML = shots.map(s => `<figure class="shot ok"><img src="${s.url}" alt="${esc(s.label)}"><figcaption>${esc(s.label)}</figcaption></figure>`).join("");
  }
  function renderControls() {
    const timedOut = !!(lastState && lastState.step_timeout);
    btnStart.hidden = running || busy;                       // đang quét: chỉ còn nút Dừng
    btnStart.textContent = shots.length || finished ? "Quét lại từ đầu" : "Bắt đầu quét khuôn mặt";
    btnStop.hidden = !running;
    btnRetry.hidden = !timedOut;
    btnSkip.hidden = !(lastState && lastState.step === "blink" && (timedOut || (running && lastState.time_left <= 8)));
    btnAdd.disabled = busy || !finished || shots.length < minImages;
  }
  function render(r) {
    lastState = r;
    stage.dataset.step = r.done ? "" : (r.step || "");
    stage.classList.toggle("complete", !!r.done);
    stage.querySelectorAll(".seg").forEach((el, i) => el.setAttribute("class", "seg " + ((r.steps[i] || {}).state || "pending")));
    $("scanSteps").innerHTML = r.steps.map((s, i) => `<li class="${esc(s.state)}">${i + 1}. ${esc(s.label)}${s.state === "done" ? " ✓" : s.state === "skipped" ? " (bỏ qua)" : ""}</li>`).join("");
    $("okCount").textContent = `${r.steps.filter(s => s.state === "done" || s.state === "skipped").length}/${r.total}`;
    instruction.textContent = r.instruction;
    if (performance.now() >= flashUntil) {            // dòng "✓ đã chụp" giữ ~1 giây trước khi nhường chỗ cho gợi ý
      hintEl.className = "scan-hint";
      hintEl.textContent = r.message || (running && !r.done && r.time_left > 0 && r.time_left <= 5 ? `Còn ${r.time_left} giây` : "");
    }
    renderControls();
  }
  function idle() {
    lastState = null; flashUntil = 0;
    stage.dataset.step = ""; stage.classList.remove("complete");
    stage.querySelectorAll(".seg").forEach(el => el.setAttribute("class", "seg pending"));
    $("scanSteps").innerHTML = ""; $("okCount").textContent = "0/4";
    instruction.textContent = 'Bấm "Bắt đầu quét khuôn mặt"';
    hintEl.className = "scan-hint"; hintEl.textContent = "";
    renderControls();
  }
  function resetShots() {
    shots.forEach(s => URL.revokeObjectURL(s.url));
    shots.length = 0; renderShots();
  }

  // ---------- camera ----------
  const stopCam = () => {
    if (stream) stream.getTracks().forEach(t => t.stop());       // nhả hẳn camera: AI Service cũng cần đọc webcam khi có phiên quét thẻ
    stream = null; video.srcObject = null;
    stage.classList.remove("live");
  };
  async function openCamera() {
    if (stream) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw Object.assign(new Error("Trình duyệt không cho dùng camera. Hãy mở trang qua http://localhost:... hoặc HTTPS."), { friendly: true });
    }
    stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" }, audio: false });
    video.srcObject = stream;
    stage.classList.add("live");
    if (!video.videoWidth) await Promise.race([new Promise(res => { video.onloadedmetadata = res; }), sleep(5000)]);
  }
  const cameraError = e => e.friendly ? e.message
    : e.name === "NotAllowedError" ? "Bạn đã chặn quyền camera cho trang này"
    : e.name === "NotFoundError" ? "Không tìm thấy camera"
    : e.name === "NotReadableError" ? "Camera đang được chương trình khác sử dụng (có thể là AI Service)" : e.message;
  function grab() {
    if (!video.videoWidth) return Promise.resolve(null);
    const w = Math.min(video.videoWidth, FRAME_WIDTH), h = Math.round(video.videoHeight * w / video.videoWidth);
    canvas.width = w; canvas.height = h;
    canvas.getContext("2d").drawImage(video, 0, 0, w, h);          // khung gốc (không lật gương) để AI phân tích
    return new Promise(r => canvas.toBlob(r, "image/jpeg", 0.85));
  }

  // ---------- phiên quét ----------
  function finish() {
    running = false; finished = true; stopCam();
    say('Hoàn tất quét khuôn mặt. Bấm "Thêm người dùng" để lưu.', "ok");
    renderControls();
  }
  function fail(e) {
    runToken++; running = false; stopCam();
    const code = errCode(e);
    say(code === "ai_unavailable"
        ? 'AI Service chưa chạy. Mở PowerShell, chạy ai_service\\start_ai.ps1, đợi dòng "HỆ THỐNG AI SẴN SÀNG" rồi bấm Bắt đầu lại.'
        : code === "session_expired" ? 'Phiên quét đã hết hạn. Bấm "Bắt đầu quét khuôn mặt" để làm lại.'
        : "Lỗi: " + e.message, "bad");
    renderControls();
  }
  function onFrameResult(r, blob) {
    if (r.passed_step) {
      const label = (r.steps.find(s => s.id === r.passed_step) || {}).label || r.passed_step;
      const i = shots.findIndex(s => s.step === r.passed_step);
      if (i >= 0) { URL.revokeObjectURL(shots[i].url); shots.splice(i, 1); }
      shots.push({ step: r.passed_step, label, blob, url: URL.createObjectURL(blob) });
      renderShots();
      hintEl.className = "scan-hint ok"; hintEl.textContent = `✓ ${label}: đã chụp`;
      flashUntil = performance.now() + 900;
    }
    render(r);
    if (r.done) return finish();
    if (r.step_timeout) {
      running = false;
      say(`Chưa nhận được "${r.instruction}". Bấm "Thử lại bước này".`, "bad");
      renderControls();
    }
  }
  async function loop(token) {
    while (running && token === runToken) {
      const t0 = performance.now();
      const blob = await grab();
      if (!blob) { await sleep(100); continue; }
      let r;
      try {
        const fd = new FormData(); fd.append("session_id", sessionId); fd.append("image", blob, "frame.jpg");
        r = await api("/api/v1/face/guide/frame", { method: "POST", body: fd });
      } catch (e) { if (token === runToken) fail(e); return; }
      if (token !== runToken) return;                              // đã dừng / bắt đầu lại trong lúc chờ AI
      onFrameResult(r, blob);
      await sleep(Math.max(0, FRAME_INTERVAL_MS - (performance.now() - t0)));
    }
  }
  function run() { running = true; renderControls(); loop(++runToken); }

  async function begin() {
    runToken++; running = false; resetShots(); finished = false; idle();
    busy = true; renderControls(); say("Đang mở camera...", "busy");
    try { await openCamera(); }
    catch (e) { busy = false; stopCam(); renderControls(); return say("Không mở được camera: " + cameraError(e), "bad"); }
    say("Đang bắt đầu phiên quét...", "busy");
    try {
      const r = await api("/api/v1/face/guide/start", { method: "POST" });
      sessionId = r.session_id;
      render(r);
    } catch (e) { busy = false; return fail(e); }
    busy = false;
    say("Làm theo hướng dẫn trên camera. Hệ thống tự chụp khi bạn làm đúng.");
    run();
  }
  // "retry": làm lại bước hiện tại sau khi hết giờ; "skip": bỏ qua bước chớp mắt (camera quá chậm)
  async function command(name) {
    if (busy || !sessionId) return;
    busy = true; renderControls();
    try {
      const fd = new FormData(); fd.append("session_id", sessionId); fd.append("command", name);
      const r = await api("/api/v1/face/guide/frame", { method: "POST", body: fd });
      busy = false;
      render(r);
      if (r.done) return finish();
      say("Làm theo hướng dẫn trên camera. Hệ thống tự chụp khi bạn làm đúng.");
      run();
    } catch (e) { busy = false; fail(e); }
  }
  const stopAll = () => { runToken++; running = false; stopCam(); };

  btnStart.onclick = begin;
  btnRetry.onclick = () => command("retry");
  btnSkip.onclick = () => command("skip");
  btnStop.onclick = () => { stopAll(); say('Đã dừng. Bấm "Quét lại từ đầu" khi sẵn sàng.'); renderControls(); };
  window.addEventListener("pagehide", stopAll);
  document.addEventListener("visibilitychange", () => { if (document.hidden && (running || stream)) { stopAll(); say('Đã dừng vì bạn rời khỏi trang. Bấm "Quét lại từ đầu" khi sẵn sàng.'); renderControls(); } });

  form.onsubmit = async e => {
    e.preventDefault();
    if (busy) return;
    if (!finished || shots.length < minImages) return say("Hãy hoàn thành các bước quét khuôn mặt trước.", "bad");
    const fd = new FormData(form);
    if (!fd.get("card_uid")) fd.delete("card_uid");
    shots.forEach((s, i) => fd.append("images", s.blob, `face${i}_${s.step}.jpg`));
    busy = true; btnAdd.textContent = "Đang lưu..."; renderControls(); say("Đang xử lý và lưu người dùng...", "busy");
    try {
      const data = await api("/api/v1/users/enroll", { method: "POST", body: fd });
      toast(`Đã thêm ${data.user.full_name}, đăng ký khuôn mặt từ ${data.used_images} ảnh`);
      say("Thành công: đã thêm người dùng và lưu dữ liệu khuôn mặt.", "ok");
      resetShots(); finished = false; form.reset(); idle();
      onDone && onDone();
    } catch (err) {
      const errs = (err.data && err.data.detail && err.data.detail.errors) || [];
      say("Lỗi: " + err.message + (errs.length ? " — " + errs.map(x => `ảnh ${x.image}: ${x.message}`).join("; ") : ""), "bad");
    } finally { btnAdd.textContent = "Thêm người dùng"; busy = false; renderControls(); }
  };
  idle();
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
