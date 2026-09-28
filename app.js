const STORAGE_KEY = "quadro-semana-v1";
const SLOT_MIN = 30;
const START_HOUR = 7;
const LAST_START_MIN = 25 * 60; // 01:00 do dia seguinte
const NUM_SLOTS = (LAST_START_MIN - START_HOUR * 60) / SLOT_MIN + 1;
const SLOT_H = 28;
const DAY_NAMES = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];

const BUILTIN_TYPES = [
  {
    id: "ancora",
    name: "Âncora fixa",
    color: "#1e3a5f",
    border: "3px solid #0b1f38",
    textColor: "#fff",
    kind: "ancora",
  },
  {
    id: "estudo",
    name: "Estudo confiável",
    color: "#0f766e",
    border: "2px solid #115e59",
    textColor: "#fff",
    kind: "estudo",
  },
  {
    id: "estudo-bonus",
    name: "Estudo bônus",
    color: "#ccfbf1",
    border: "2px dashed #0f766e",
    textColor: "#134e4a",
    kind: "estudo-bonus",
  },
  {
    id: "yuki",
    name: "Passeio Yuki",
    color: "#f59e0b",
    border: "2px solid #b45309",
    textColor: "#1c1917",
    kind: "yuki",
  },
  {
    id: "sono",
    name: "Sono",
    color: "#7c3aed",
    border: "2px solid #5b21b6",
    textColor: "#fff",
    kind: "sono",
  },
  {
    id: "friccao",
    name: "Zona de fricção",
    color: "#dc2626",
    border: "2px solid #7f1d1d",
    textColor: "#fff",
    kind: "friccao",
    hatch: true,
  },
  {
    id: "abertura",
    name: "Abertura sem meta",
    color: "#2dd4bf",
    border: "2px solid #0f766e",
    textColor: "#134e4a",
    kind: "abertura",
    defaultMinutes: 15,
  },
];

let state = {
  weekStart: mondayKey(new Date()),
  types: structuredClone(BUILTIN_TYPES),
  weeks: {},
};

let dragTypeId = null;
let movingBlockId = null;
let resize = null;
let timerInterval = null;

const el = {
  calendar: document.getElementById("calendar"),
  palette: document.getElementById("palette-list"),
  weekLabel: document.getElementById("week-label"),
  studyCounter: document.getElementById("study-counter"),
  modal: document.getElementById("modal"),
  modalBody: document.getElementById("modal-body"),
  toast: document.getElementById("toast"),
  importFile: document.getElementById("import-file"),
};

function uid() {
  return crypto.randomUUID();
}

function mondayDate(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  const day = x.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  x.setDate(x.getDate() + diff);
  return x;
}

function mondayKey(d) {
  return isoDate(mondayDate(d));
}

function isoDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function parseIso(s) {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function addDays(iso, n) {
  const d = parseIso(iso);
  d.setDate(d.getDate() + n);
  return isoDate(d);
}

function slotLabel(slot) {
  const total = START_HOUR * 60 + slot * SLOT_MIN;
  const h = Math.floor(total / 60) % 24;
  const min = total % 60;
  return `${String(h).padStart(2, "0")}:${String(min).padStart(2, "0")}`;
}

function slotStartMin(slot) {
  return START_HOUR * 60 + slot * SLOT_MIN;
}

function currentBlocks() {
  if (!state.weeks[state.weekStart]) state.weeks[state.weekStart] = [];
  return state.weeks[state.weekStart];
}

function typeById(id) {
  return state.types.find((t) => t.id === id);
}

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    state.weekStart = data.weekStart || mondayKey(new Date());
    state.types = Array.isArray(data.types) && data.types.length ? data.types : structuredClone(BUILTIN_TYPES);
    state.weeks = data.weeks || {};
    for (const builtin of BUILTIN_TYPES) {
      if (!state.types.some((t) => t.id === builtin.id)) state.types.push({ ...builtin });
    }
  } catch (e) {
    console.warn("Falha ao ler localStorage", e);
  }
}

function save() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function overlaps(a, b) {
  if (a.day !== b.day) return false;
  const a0 = slotStartMin(a.slot);
  const a1 = a0 + a.minutes;
  const b0 = slotStartMin(b.slot);
  const b1 = b0 + b.minutes;
  return a0 < b1 && b0 < a1;
}

function collides(candidate, ignoreId) {
  return currentBlocks().some((b) => b.id !== ignoreId && overlaps(candidate, b));
}

function defaultMinutes(type) {
  return type.defaultMinutes || SLOT_MIN;
}

function placeBlock(typeId, day, slot, extra = {}) {
  const type = typeById(typeId);
  if (!type) return null;
  const minutes = extra.minutes ?? defaultMinutes(type);
  const end = slotStartMin(slot) + minutes;
  const lastEnd = LAST_START_MIN + SLOT_MIN;
  if (end > lastEnd) return null;
  const block = {
    id: uid(),
    typeId,
    day,
    slot,
    minutes,
    text: extra.text ?? type.name,
  };
  if (collides(block)) {
    toast("Horário ocupado.");
    return null;
  }
  currentBlocks().push(block);
  save();
  render();
  return block;
}

function removeBlock(id) {
  const list = currentBlocks();
  const i = list.findIndex((b) => b.id === id);
  if (i >= 0) {
    list.splice(i, 1);
    save();
    render();
  }
}

function studyHours() {
  let min = 0;
  for (const b of currentBlocks()) {
    const t = typeById(b.typeId);
    if (t && (t.kind === "estudo" || t.kind === "estudo-bonus")) min += b.minutes;
  }
  const h = min / 60;
  const rounded = Number.isInteger(h) ? String(h) : h.toFixed(1).replace(".", ",");
  return rounded;
}

function renderPalette() {
  el.palette.innerHTML = "";
  for (const t of state.types) {
    const item = document.createElement("div");
    item.className = "palette-item";
    item.draggable = true;
    item.dataset.typeId = t.id;
    item.style.background = t.color;
    item.style.border = t.border;
    item.style.color = t.textColor || "#111";
    if (t.hatch) {
      item.style.backgroundImage =
        "repeating-linear-gradient(135deg, rgba(0,0,0,.22) 0 6px, transparent 6px 12px)";
    }
    item.innerHTML = `<span class="palette-name">${escapeHtml(t.name)}</span>`;
    if (!BUILTIN_TYPES.some((b) => b.id === t.id)) {
      const del = document.createElement("button");
      del.type = "button";
      del.className = "del-type";
      del.textContent = "×";
      del.title = "Remover tipo";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        state.types = state.types.filter((x) => x.id !== t.id);
        save();
        render();
      });
      item.appendChild(del);
    }
    item.addEventListener("dragstart", (e) => {
      dragTypeId = t.id;
      e.dataTransfer.setData("text/plain", "type:" + t.id);
      e.dataTransfer.effectAllowed = "copy";
    });
    item.addEventListener("dragend", () => {
      dragTypeId = null;
    });
    el.palette.appendChild(item);
  }
}

function renderCalendar() {
  const weekStart = parseIso(state.weekStart);
  const todayKey = isoDate(new Date());
  el.weekLabel.textContent = `${fmtDate(weekStart)} — ${fmtDate(parseIso(addDays(state.weekStart, 6)))}`;
  el.studyCounter.textContent = `Estudo: ${studyHours()}h`;

  el.calendar.innerHTML = "";

  const timeCol = document.createElement("div");
  timeCol.className = "time-col";
  const corner = document.createElement("div");
  corner.className = "corner";
  timeCol.appendChild(corner);
  for (let s = 0; s < NUM_SLOTS; s++) {
    const lab = document.createElement("div");
    lab.className = "time-label";
    lab.textContent = slotLabel(s);
    timeCol.appendChild(lab);
  }
  el.calendar.appendChild(timeCol);

  for (let day = 0; day < 7; day++) {
    const dateIso = addDays(state.weekStart, day);
    const col = document.createElement("div");
    col.className = "day-col";
    const head = document.createElement("div");
    head.className = "day-header" + (dateIso === todayKey ? " today" : "");
    const d = parseIso(dateIso);
    head.textContent = `${DAY_NAMES[day]} ${d.getDate()}/${d.getMonth() + 1}`;
    col.appendChild(head);

    const slotsWrap = document.createElement("div");
    slotsWrap.className = "day-slots" + (dateIso === todayKey ? " today" : "");
    slotsWrap.dataset.day = String(day);

    for (let s = 0; s < NUM_SLOTS; s++) {
      const slot = document.createElement("div");
      slot.className = "slot";
      slot.dataset.day = String(day);
      slot.dataset.slot = String(s);
      slotsWrap.appendChild(slot);
    }
    bindDropTarget(slotsWrap, day);

    for (const block of currentBlocks().filter((b) => b.day === day)) {
      slotsWrap.appendChild(renderBlock(block));
    }

    col.appendChild(slotsWrap);
    el.calendar.appendChild(col);
  }
}

function slotFromPoint(wrap, clientY) {
  const rect = wrap.getBoundingClientRect();
  let slot = Math.floor((clientY - rect.top) / SLOT_H);
  return Math.max(0, Math.min(NUM_SLOTS - 1, slot));
}

function clearDropHints(wrap) {
  wrap.querySelectorAll(".slot").forEach((s) => s.classList.remove("drop-ok", "drop-no"));
}

function bindDropTarget(wrap, day) {
  wrap.addEventListener("dragover", (e) => {
    if (!dragTypeId && !movingBlockId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = movingBlockId ? "move" : "copy";
    const slot = slotFromPoint(wrap, e.clientY);
    clearDropHints(wrap);
    const cell = wrap.querySelector(`.slot[data-slot="${slot}"]`);
    const ok = canPlaceAt(day, slot);
    if (cell) {
      cell.classList.toggle("drop-ok", ok);
      cell.classList.toggle("drop-no", !ok);
    }
  });
  wrap.addEventListener("dragleave", (e) => {
    if (!wrap.contains(e.relatedTarget)) clearDropHints(wrap);
  });
  wrap.addEventListener("drop", (e) => {
    e.preventDefault();
    const slot = slotFromPoint(wrap, e.clientY);
    clearDropHints(wrap);
    const data = e.dataTransfer.getData("text/plain") || "";
    if (data.startsWith("move:")) {
      moveBlockTo(data.slice(5), day, slot);
    } else if (data.startsWith("type:")) {
      placeBlock(data.slice(5), day, slot);
    } else if (movingBlockId) {
      moveBlockTo(movingBlockId, day, slot);
    } else if (dragTypeId) {
      placeBlock(dragTypeId, day, slot);
    }
    movingBlockId = null;
    dragTypeId = null;
  });
}

function canPlaceAt(day, slot) {
  if (movingBlockId) {
    const b = currentBlocks().find((x) => x.id === movingBlockId);
    if (!b) return false;
    const end = slotStartMin(slot) + b.minutes;
    if (end > LAST_START_MIN + SLOT_MIN) return false;
    return !collides({ ...b, day, slot }, b.id);
  }
  const type = typeById(dragTypeId);
  if (!type) return false;
  const minutes = defaultMinutes(type);
  const end = slotStartMin(slot) + minutes;
  if (end > LAST_START_MIN + SLOT_MIN) return false;
  return !collides({ day, slot, minutes }, null);
}

function moveBlockTo(id, day, slot) {
  const b = currentBlocks().find((x) => x.id === id);
  if (!b) return;
  const end = slotStartMin(slot) + b.minutes;
  if (end > LAST_START_MIN + SLOT_MIN) return;
  if (collides({ ...b, day, slot }, id)) return;
  b.day = day;
  b.slot = slot;
  save();
  render();
}

function renderBlock(block) {
  const type = typeById(block.typeId) || {
    color: "#94a3b8",
    border: "1px solid #64748b",
    textColor: "#111",
    name: "?",
  };
  const node = document.createElement("div");
  node.className = "block" + (type.hatch ? " friction" : "");
  node.draggable = true;
  node.dataset.id = block.id;
  node.style.top = `${block.slot * SLOT_H}px`;
  node.style.height = `${(block.minutes / SLOT_MIN) * SLOT_H}px`;
  node.style.background = type.color;
  node.style.border = type.border;
  node.style.color = type.textColor || "#111";
  if (type.hatch) {
    node.style.backgroundImage =
      "repeating-linear-gradient(135deg, rgba(0,0,0,.25) 0 7px, transparent 7px 14px)";
  }

  const text = document.createElement("span");
  text.className = "block-text";
  text.contentEditable = "true";
  text.textContent = block.text;
  text.addEventListener("pointerdown", (e) => e.stopPropagation());
  text.addEventListener("click", (e) => e.stopPropagation());
  text.addEventListener("blur", () => {
    block.text = text.textContent.trim() || type.name;
    save();
  });
  text.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      text.blur();
    }
  });

  const x = document.createElement("button");
  x.type = "button";
  x.className = "block-x";
  x.textContent = "×";
  x.title = "Remover";
  x.addEventListener("click", (e) => {
    e.stopPropagation();
    removeBlock(block.id);
  });

  const handle = document.createElement("div");
  handle.className = "resize-handle";
  handle.addEventListener("pointerdown", (e) => startResize(e, block));

  node.append(text, x, handle);

  node.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    removeBlock(block.id);
  });

  node.addEventListener("dragstart", (e) => {
    if (document.activeElement === text) {
      e.preventDefault();
      return;
    }
    movingBlockId = block.id;
    node.classList.add("dragging");
    e.dataTransfer.setData("text/plain", "move:" + block.id);
    e.dataTransfer.effectAllowed = "move";
  });
  node.addEventListener("dragend", () => {
    movingBlockId = null;
    node.classList.remove("dragging");
  });

  node.addEventListener("click", (e) => {
    if (e.target === x || e.target === handle || e.target === text) return;
    if (type.kind === "friccao") openFrictionTimer(block);
    if (type.kind === "abertura") openAbertura();
  });

  return node;
}

function startResize(e, block) {
  e.preventDefault();
  e.stopPropagation();
  const startY = e.clientY;
  const startMin = block.minutes;
  resize = { id: block.id, startY, startMin };
  document.body.style.userSelect = "none";

  function onMove(ev) {
    const dy = ev.clientY - startY;
    let minutes = Math.round((startMin + (dy / SLOT_H) * SLOT_MIN) / SLOT_MIN) * SLOT_MIN;
    minutes = Math.max(SLOT_MIN, minutes);
    const maxMin = LAST_START_MIN + SLOT_MIN - slotStartMin(block.slot);
    minutes = Math.min(minutes, maxMin);
    const probe = { ...block, minutes };
    if (collides(probe, block.id)) return;
    block.minutes = minutes;
    const node = document.querySelector(`.block[data-id="${block.id}"]`);
    if (node) node.style.height = `${(minutes / SLOT_MIN) * SLOT_H}px`;
  }

  function onUp() {
    document.removeEventListener("pointermove", onMove);
    document.removeEventListener("pointerup", onUp);
    document.body.style.userSelect = "";
    resize = null;
    save();
    render();
  }

  document.addEventListener("pointermove", onMove);
  document.addEventListener("pointerup", onUp);
}

function fmtDate(d) {
  return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
}

function render() {
  renderPalette();
  renderCalendar();
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(msg) {
  el.toast.hidden = false;
  el.toast.textContent = msg;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    el.toast.hidden = true;
  }, 2800);
}

function closeModal() {
  el.modal.hidden = true;
  el.modalBody.innerHTML = "";
  if (timerInterval) {
    clearInterval(timerInterval);
    timerInterval = null;
  }
}

function openAbertura() {
  el.modal.hidden = false;
  el.modalBody.innerHTML = `<p style="font-size:1.1rem;margin:8px 12px 0 0">Só abrir o caderno. Nenhuma meta além disso.</p>`;
}

function openFrictionTimer() {
  el.modal.hidden = false;
  el.modalBody.innerHTML = `
    <h3 style="margin:0 20px 8px 0">Zona de fricção</h3>
    <div class="timer-row">
      <label>Minutos <input type="number" id="timer-min" min="1" max="180" value="20" style="width:72px" /></label>
      <button type="button" id="timer-start">Começar</button>
      <button type="button" id="timer-stop">Parar</button>
    </div>
    <div class="timer-display" id="timer-display">20:00</div>
    <div id="timer-msg"></div>
  `;
  const input = document.getElementById("timer-min");
  const display = document.getElementById("timer-display");
  const msg = document.getElementById("timer-msg");
  let remaining = 20 * 60;

  function paint() {
    const m = Math.floor(remaining / 60);
    const s = remaining % 60;
    display.textContent = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  document.getElementById("timer-start").onclick = () => {
    if (timerInterval) clearInterval(timerInterval);
    remaining = Math.max(1, Number(input.value) || 20) * 60;
    msg.innerHTML = "";
    paint();
    timerInterval = setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        remaining = 0;
        paint();
        clearInterval(timerInterval);
        timerInterval = null;
        playBeep();
        msg.innerHTML = `<p class="timer-done">Hora de fechar, sem negociar</p>`;
        return;
      }
      paint();
    }, 1000);
  };
  document.getElementById("timer-stop").onclick = () => {
    if (timerInterval) {
      clearInterval(timerInterval);
      timerInterval = null;
    }
  };
}

function playBeep() {
  try {
    const ctx = new AudioContext();
    const now = ctx.currentTime;
    [0, 0.35, 0.7].forEach((t) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "square";
      o.frequency.value = 880;
      o.connect(g);
      g.connect(ctx.destination);
      g.gain.setValueAtTime(0.0001, now + t);
      g.gain.exponentialRampToValueAtTime(0.18, now + t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, now + t + 0.28);
      o.start(now + t);
      o.stop(now + t + 0.3);
    });
  } catch (e) {
    console.warn(e);
  }
}

function firstFreeAfterNow() {
  const now = new Date();
  const todayIso = isoDate(now);
  const weekStart = parseIso(state.weekStart);
  const todayInWeek = [...Array(7)].map((_, i) => addDays(state.weekStart, i));
  const day = todayInWeek.indexOf(todayIso);
  if (day < 0) return { error: "Mude para a semana atual (botão Hoje) para colocar o mínimo viável." };

  const nowMin = now.getHours() * 60 + now.getMinutes();
  if (nowMin >= LAST_START_MIN + SLOT_MIN) {
    return { error: "Já passou da 01h — não há slot hoje." };
  }
  let slot = 0;
  if (nowMin > START_HOUR * 60) {
    slot = Math.ceil((nowMin - START_HOUR * 60) / SLOT_MIN);
  }

  const minutes = 20;
  for (let s = slot; s < NUM_SLOTS; s++) {
    const end = slotStartMin(s) + minutes;
    if (end > LAST_START_MIN + SLOT_MIN) break;
    if (!collides({ day, slot: s, minutes }, null)) return { day, slot: s, minutes };
  }
  return { error: "Não há horário livre hoje depois de agora." };
}

function addMinimoViavel() {
  const found = firstFreeAfterNow();
  if (found.error) {
    toast(found.error);
    return;
  }
  const customId = "minimo-viavel";
  if (!typeById(customId)) {
    state.types.push({
      id: customId,
      name: "Mínimo viável",
      color: "#86efac",
      border: "2px solid #16a34a",
      textColor: "#14532d",
      kind: "minimo",
      defaultMinutes: 20,
    });
  }
  const ok = placeBlock(customId, found.day, found.slot, {
    minutes: 20,
    text: "Mínimo viável ✓",
  });
  if (!ok) toast("Não deu pra encaixar o mínimo viável.");
}

function duplicateWeek() {
  const next = addDays(state.weekStart, 7);
  const src = currentBlocks();
  if (!src.length) {
    toast("Esta semana está vazia.");
    return;
  }
  const dest = state.weeks[next] || [];
  if (dest.length && !confirm("A próxima semana já tem blocos. Substituir?")) return;
  state.weeks[next] = src.map((b) => ({ ...b, id: uid() }));
  save();
  toast("Semana duplicada. Use → para ver a próxima.");
}

function exportWeek() {
  const payload = {
    version: 1,
    exportedAt: new Date().toISOString(),
    weekStart: state.weekStart,
    types: state.types,
    weeks: state.weeks,
    currentWeekBlocks: currentBlocks(),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `semana-${state.weekStart}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function importWeek(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result);
      if (Array.isArray(data.types)) state.types = data.types;
      if (data.weeks && typeof data.weeks === "object") {
        state.weeks = data.weeks;
      } else if (Array.isArray(data.currentWeekBlocks) && data.weekStart) {
        state.weeks[data.weekStart] = data.currentWeekBlocks;
      } else if (Array.isArray(data.blocks) && data.weekStart) {
        state.weeks[data.weekStart] = data.blocks;
      } else {
        throw new Error("JSON sem blocos");
      }
      if (data.weekStart) state.weekStart = data.weekStart;
      save();
      render();
      toast("Semana importada.");
    } catch (e) {
      toast("JSON inválido.");
    }
  };
  reader.readAsText(file);
}

document.getElementById("btn-prev-week").onclick = () => {
  state.weekStart = addDays(state.weekStart, -7);
  save();
  render();
};
document.getElementById("btn-next-week").onclick = () => {
  state.weekStart = addDays(state.weekStart, 7);
  save();
  render();
};
document.getElementById("btn-this-week").onclick = () => {
  state.weekStart = mondayKey(new Date());
  save();
  render();
};
document.getElementById("btn-minimo").onclick = addMinimoViavel;
document.getElementById("btn-duplicate").onclick = duplicateWeek;
document.getElementById("btn-export").onclick = exportWeek;
document.getElementById("btn-import").onclick = () => el.importFile.click();
el.importFile.addEventListener("change", () => {
  const file = el.importFile.files[0];
  if (file) importWeek(file);
  el.importFile.value = "";
});
document.getElementById("modal-close").onclick = closeModal;
el.modal.addEventListener("click", (e) => {
  if (e.target === el.modal) closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

document.getElementById("new-type-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const name = document.getElementById("new-type-name").value.trim();
  const color = document.getElementById("new-type-color").value;
  if (!name) return;
  state.types.push({
    id: uid(),
    name,
    color,
    border: "2px solid #1c1917",
    textColor: contrastText(color),
    kind: "custom",
  });
  document.getElementById("new-type-name").value = "";
  save();
  render();
});

function contrastText(hex) {
  const n = hex.replace("#", "");
  const r = parseInt(n.slice(0, 2), 16);
  const g = parseInt(n.slice(2, 4), 16);
  const b = parseInt(n.slice(4, 6), 16);
  const y = (r * 299 + g * 587 + b * 114) / 1000;
  return y > 150 ? "#1c1917" : "#fff";
}

load();
render();
