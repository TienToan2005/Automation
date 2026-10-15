// Chuông thông báo + toast thời gian thực cho admin (chỉ được nạp cho admin, xem base.html).
// Hỏi GET /api/v1/notifications?after=<id> mỗi ~2 giây (10 giây khi tab ẩn). Có thông báo mới thì:
// hiện toast, kêu bíp (mức cao), cập nhật chuông, và phát sự kiện "lab:notify" trên document để từng trang tự làm mới.
(function () {
  const bell = document.getElementById("bell");
  if (!bell) return;
  const POLL_MS = 2000, HIDDEN_POLL_MS = 10000, KEEP = 40;
  const TOAST_MS = { info: 6000, medium: 12000, high: 30000 }, MAX_TOASTS = 4;
  const panel = document.getElementById("bellPanel"), list = document.getElementById("bellList");
  const countEl = document.getElementById("bellCount"), toasts = document.getElementById("toasts");
  const soundBox = document.getElementById("soundOn");
  const baseTitle = document.title;
  const KIND_ICON = { alert: "🚨", remote_open: "🔓", device_online: "🟢", device_offline: "🔴", device_lockout: "⛔", ai_unavailable: "🤖", test: "🔔" };

  let boot = null, lastId = 0, items = [], failures = 0, timer = null, audio = null;

  const store = {
    get(key) { try { return JSON.parse(localStorage.getItem(key)); } catch (e) { return null; } },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* chế độ riêng tư: bỏ qua */ } },
  };
  const seenId = () => { const s = store.get("lab.notify.seen"); return s && s.boot === boot ? s.id : 0; };
  const href = n => (n.link && /^\/(?!\/)/.test(n.link) ? n.link : "");        // chỉ cho phép link nội bộ (không cho "//host")

  function beep() {
    if (!soundBox.checked) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      [880, 660].forEach((freq, i) => {
        const osc = audio.createOscillator(), gain = audio.createGain(), t = audio.currentTime + i * 0.18;
        osc.frequency.value = freq; osc.type = "sine"; osc.connect(gain); gain.connect(audio.destination);
        gain.gain.setValueAtTime(0.0001, t); gain.gain.exponentialRampToValueAtTime(0.25, t + 0.02); gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
        osc.start(t); osc.stop(t + 0.17);
      });
    } catch (e) { /* trình duyệt chặn âm thanh khi chưa tương tác: bỏ qua */ }
  }

  function renderBell() {
    const unread = items.filter(n => n.id > seenId()).length;
    countEl.hidden = !unread;
    countEl.textContent = unread > 9 ? "9+" : unread;
    bell.setAttribute("aria-label", unread ? `Thông báo, ${unread} chưa đọc` : "Thông báo");
    document.title = unread ? `(${unread}) ${baseTitle}` : baseTitle;
    list.innerHTML = items.length ? items.slice().reverse().map(n => `
      <div class="bell-item lvl-${esc(n.level)} ${n.id > seenId() ? "unread" : ""}">
        <span class="bell-ico" aria-hidden="true">${KIND_ICON[n.kind] || "•"}</span>
        <div>
          <b>${esc(n.title)}</b>
          <div>${esc(n.message)}</div>
          <div class="muted">${fmtTime(n.ts)}${href(n) ? ` &middot; <a href="${esc(href(n))}">Xem</a>` : ""}${n.snapshot_url ? ` &middot; <a href="${esc(n.snapshot_url)}" target="_blank">ảnh</a>` : ""}</div>
        </div>
      </div>`).join("") : '<p class="muted" style="padding:12px 14px">Chưa có thông báo nào kể từ lần khởi động backend gần nhất.</p>';
  }

  function markRead() { store.set("lab.notify.seen", { boot, id: lastId }); renderBell(); }

  function toast(n) {
    const el = document.createElement("div");
    el.className = `ntoast ntoast-${n.level}`;
    el.innerHTML = `<span class="bell-ico" aria-hidden="true">${KIND_ICON[n.kind] || "•"}</span>
      <div class="ntoast-body"><b>${esc(n.title)}</b><div>${esc(n.message)}</div>
      ${href(n) ? `<a href="${esc(href(n))}">Xem chi tiết</a>` : ""}</div>
      <button type="button" class="ntoast-close secondary small" aria-label="Đóng thông báo">×</button>`;
    const close = () => el.remove();
    el.querySelector("button").onclick = close;
    toasts.appendChild(el);
    while (toasts.children.length > MAX_TOASTS) toasts.firstChild.remove();
    setTimeout(close, TOAST_MS[n.level] || 8000);
  }

  function accept(newItems) {
    for (const n of newItems) {
      items.push(n);
      toast(n);
      if (n.level === "high") beep();
      document.dispatchEvent(new CustomEvent("lab:notify", { detail: n }));
    }
    items = items.slice(-KEEP);
    lastId = newItems[newItems.length - 1].id;
  }

  async function poll() {
    clearTimeout(timer);
    let delay = document.hidden ? HIDDEN_POLL_MS : POLL_MS;
    try {
      const first = boot === null;
      const data = await api("/api/v1/notifications" + (first ? "?limit=20" : `?after=${lastId}`));
      failures = 0;
      if (first) {                                   // lần mở trang: chỉ nạp lịch sử vào chuông, không bật toast
        boot = data.boot; items = data.items; lastId = data.last_id;
      } else if (data.boot !== boot) {               // backend vừa khởi động lại: id bắt đầu lại từ 1, nạp lại từ đầu
        boot = data.boot; items = []; lastId = 0; delay = 0;
      } else if (data.items.length) {
        accept(data.items);
        if (!panel.hidden) markRead();
      }
      renderBell();
    } catch (e) {
      failures += 1;                                 // mất mạng / backend tạm dừng: giãn dần, tự nối lại
      delay = Math.min(30000, POLL_MS * 2 ** failures);
    }
    timer = setTimeout(poll, delay);
  }

  bell.onclick = () => {
    panel.hidden = !panel.hidden;
    bell.setAttribute("aria-expanded", String(!panel.hidden));
    if (!panel.hidden) markRead();
  };
  document.addEventListener("click", e => {         // bấm ra ngoài thì đóng
    if (!panel.hidden && !panel.contains(e.target) && !bell.contains(e.target)) { panel.hidden = true; bell.setAttribute("aria-expanded", "false"); }
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !panel.hidden) { panel.hidden = true; bell.focus(); } });
  document.getElementById("markRead").onclick = markRead;
  soundBox.checked = store.get("lab.notify.sound") !== false;
  soundBox.onchange = () => store.set("lab.notify.sound", soundBox.checked);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  poll();
})();
