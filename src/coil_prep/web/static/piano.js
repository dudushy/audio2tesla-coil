// Soundpad: uma pad por nota (C1-B8, as mesmas testadas na calibragem), onda quadrada
// (OscillatorNode "square" = duty 50%) no navegador, várias pads ao mesmo tempo (multitoque). Com
// "Ajustar à faixa" só aparecem as notas calibradas; sem ele, as outras ficam esmaecidas.
// Nível = peak_db do servidor, dividido pelo nº de vozes (como a render do servidor).
// Gravação: lista de notas {start,end,pitch}; exporta pelo servidor (/api/live/export).
// Duas vistas: pads (uma por nota) ou piano (fileiras de oitavas).
(() => {
  const LOW = 24, HIGH = 119;  // C1..B8 (MIDI), como COIL_LOW/COIL_HIGH do servidor
  const BLACK = new Set([1, 3, 6, 8, 10]);
  const HUES = [270, 215, 185, 140, 90, 45, 10, 325];  // uma cor por oitava (C1..C8)
  // teclas do computador (linha A-K = C a D+1; Z/X trocam a oitava)
  const KEYS = { a: 0, w: 1, s: 2, e: 3, d: 4, f: 5, t: 6, g: 7, y: 8, h: 9, u: 10, j: 11, k: 12, o: 13, l: 14 };
  const hz = (p) => 440 * 2 ** ((p - 69) / 12);
  const octOf = (p) => Math.floor(p / 12) - 1;
  const hue = (p) => HUES[Math.min(Math.floor((p - LOW) / 12), HUES.length - 1)];

  const root = document.getElementById("kb-box");
  if (!root) return;
  const kb = root.querySelector(".kb"), info = root.querySelector("#kb-info");
  const $r = (id) => root.querySelector("#" + id);
  let base = 60;  // C4: oitava das teclas do computador
  let amp = 10 ** (-8 / 20), range = null, heard = null;  // heard: notas ouvidas na calibragem
  let ctx = null;
  const inputs = new Map();  // dedo/mouse ("p<id>") ou tecla ("k<letra>") -> nota (null = fora das pads)
  const voices = new Map();  // nota soando -> [oscilador, ganho]

  // --- grade -----------------------------------------------------------------------------------
  const outOfRange = (p) => (heard ? !heard.has(p)
    : range !== null && (hz(p) < range[0] || hz(p) > range[1]));
  function visible() {
    const all = Array.from({ length: HIGH - LOW + 1 }, (_, i) => LOW + i);
    return $r("fit").checked ? all.filter((p) => !outOfRange(p)) : all;
  }
  // todas as notas: 12 colunas (uma oitava por linha) ou 6 com a tela estreita; só a faixa:
  // o nº de colunas que deixa as pads maiores no espaço disponível
  function colsFor(n) {
    const w = kb.clientWidth, h = kb.clientHeight;
    if (n === HIGH - LOW + 1) return w < 600 && h > w * 0.8 ? 6 : 12;
    let best = 1, score = 0;
    for (let c = 1; c <= Math.min(n, 12); c++) {
      const rows = Math.ceil(n / c);
      // lado menor da pad, preferindo a última linha completa
      const s = Math.min(w / c, h / rows) * (0.7 + 0.3 * n / (c * rows));
      if (s > score) { score = s; best = c; }
    }
    return best;
  }
  const view = () => $r("view").value;  // "pads" ou "piano"
  function makeKey(p, cls) {
    const b = document.createElement("button");
    b.type = "button"; b.tabIndex = -1; b.dataset.p = p;
    b.className = cls;
    b.style.setProperty("--h", hue(p));
    if (outOfRange(p)) b.classList.add("out");
    const f = hz(p);
    b.setAttribute("aria-label", `${noteName(p)} ${Math.round(f)} Hz`);
    b.innerHTML = `<b>${noteName(p)}</b><small>${Math.round(f)} Hz</small>`;
    return b;
  }

  // o que define o desenho; se mudar ao redimensionar, redesenha
  let shape = "";
  const shapeNow = () => (view() === "piano" ? "piano" + pianoPlan().perRow : "pads" + colsFor(visible().length));
  function build() {
    shape = shapeNow();
    kb.textContent = "";
    kb.classList.toggle("piano", view() === "piano");
    if (view() === "piano") buildPiano(); else buildPads();
    paint();
  }
  function buildPads() {
    const notesShown = visible(), cols = colsFor(notesShown.length);
    const rows = Math.ceil(notesShown.length / cols);
    kb.style.setProperty("--cols", cols);
    kb.style.setProperty("--rows", rows);
    notesShown.forEach((p, i) => {
      const b = makeKey(p, "pad" + (BLACK.has(p % 12) ? " sharp" : ""));
      b.style.gridColumn = (i % cols) + 1;  // todas do mesmo tamanho; sobra fica vazia em cima
      b.style.gridRow = rows - Math.floor(i / cols);  // grave embaixo
      kb.appendChild(b);
    });
    kb.classList.toggle("dense", kb.clientHeight / rows < 42);  // pad baixa: só o nome da nota
  }

  // piano: o teclado inteiro não cabe numa linha, então é dividido em fileiras de oitavas
  // (a mais grave embaixo), com o nº de oitavas por fileira que deixa as teclas maiores
  function pianoPlan() {
    const shown = visible();
    let lo = shown.length ? shown[0] : 60, hi = shown.length ? shown[shown.length - 1] : 72;
    if (BLACK.has(lo % 12)) lo -= 1;  // começa e termina em tecla branca
    if (BLACK.has(hi % 12)) hi += 1;
    const octaves = [];
    for (let p = lo; p <= hi; p++) {
      if (p === lo || p % 12 === 0) octaves.push([]);
      octaves[octaves.length - 1].push(p);
    }
    let perRow = 1, score = 0;
    for (let n = 1; n <= octaves.length; n++) {
      // tecla de piano: cerca de 3,5 vezes mais alta que larga
      const s = Math.min((kb.clientWidth / (7 * n)) * 3.5, kb.clientHeight / Math.ceil(octaves.length / n));
      if (s > score) { score = s; perRow = n; }
    }
    return { octaves, perRow, shown: new Set(shown) };
  }
  function buildPiano() {
    const { octaves, perRow, shown } = pianoPlan();
    const rows = [];
    for (let i = 0; i < octaves.length; i += perRow) rows.push(octaves.slice(i, i + perRow).flat());
    const whites = (r) => r.filter((p) => !BLACK.has(p % 12)).length;
    const width = 100 / Math.max(...rows.map(whites));  // mesma largura de tecla em todas as fileiras
    for (const notesRow of rows) {  // CSS empilha de baixo para cima
      const row = document.createElement("div");
      row.className = "krow";
      let n = 0;  // teclas brancas já colocadas
      for (const p of notesRow) {
        const black = BLACK.has(p % 12), b = makeKey(p, "key " + (black ? "black" : "white"));
        b.style.left = (black ? n - 0.32 : n) * width + "%";
        b.style.width = (black ? 0.64 : 1) * width + "%";
        if (!black) n += 1;
        // com "Ajustar à faixa", a tecla não calibrada continua no desenho, mas não toca
        if (!shown.has(p)) { b.classList.add("off"); b.disabled = true; }
        row.appendChild(b);
      }
      kb.appendChild(row);
    }
  }

  function paint() {
    for (const k of kb.querySelectorAll("[data-p]")) {
      const p = +k.dataset.p;
      k.classList.toggle("on", voices.has(p) || playing.has(p));
      k.classList.toggle("base", octOf(p) === octOf(base));
    }
    const on = [...voices.keys()].sort((a, b) => a - b);
    if (!on.length) info.textContent = view() === "piano" ? "Toque uma ou mais teclas." : "Toque uma ou mais pads.";
    else if (on.length > 1) info.textContent = on.map(noteName).join(" · ");
    else info.textContent = `${noteName(on[0])} · ${hz(on[0]).toFixed(1)} Hz${outOfRange(on[0]) ? " · fora da faixa calibrada" : ""}`;
  }

  // --- áudio -----------------------------------------------------------------------------------
  const audio = () => { ctx = ctx || new AudioContext(); ctx.resume(); return ctx; };
  function voice(c, p) {
    const o = c.createOscillator(), g = c.createGain();
    o.type = "square"; o.frequency.value = hz(p); g.gain.value = 0;
    o.connect(g).connect(c.destination);
    return [o, g];
  }
  const level = (g, v, t) => { g.gain.cancelScheduledValues(t); g.gain.setTargetAtTime(v, t, 0.002); };

  // faz as vozes soando baterem com o que está apertado
  function sync() {
    const want = new Set([...inputs.values()].filter((p) => p !== null));
    for (const [p, [o, g]] of voices) {
      if (want.has(p)) continue;
      level(g, 0, ctx.currentTime); o.stop(ctx.currentTime + 0.03);
      voices.delete(p); recOff(p);
    }
    for (const p of want) {
      if (voices.has(p)) continue;
      const v = voice(audio(), p); v[0].start();
      voices.set(p, v); recOn(p);
    }
    for (const [, g] of voices.values()) level(g, amp / voices.size, ctx.currentTime);
    paint();
  }
  // toque rápido: a nota ainda soa por um tempo mínimo (caixas e receptores Bluetooth engolem
  // sons muito curtos). A "sobra" é cortada assim que outra nota é apertada.
  const since = new Map();  // entrada -> quando apertou a nota atual (ms)
  const tails = new Map();  // sobra ("t<n>") -> temporizador
  let tailId = 0;
  function dropTails() {
    for (const [id, timer] of tails) { clearTimeout(timer); inputs.delete(id); }
    tails.clear();
  }
  function press(id, p) {
    if (inputs.get(id) === p) return;
    if (p !== null && tails.size) { dropTails(); sync(); }  // repetir a mesma nota rearticula
    inputs.set(id, p); since.set(id, performance.now()); sync();
  }
  function lift(id) {
    if (!inputs.has(id)) return;
    const p = inputs.get(id), left = +$r("hold").value - (performance.now() - since.get(id));
    inputs.delete(id); since.delete(id);
    if (p !== null && left > 0 && ![...inputs.values()].some((q) => q !== null)) {
      const tail = "t" + tailId++;
      inputs.set(tail, p);
      tails.set(tail, setTimeout(() => { tails.delete(tail); inputs.delete(tail); sync(); }, left));
    }
    sync();
  }
  const stopAll = () => { dropTails(); inputs.clear(); since.clear(); sync(); };

  // --- gravação e reprodução -------------------------------------------------------------------
  let recording = false, t0 = 0, notes = [];
  const open = new Map();  // nota soando -> início
  const now = () => performance.now() / 1000;
  function recOn(p) {
    if (!recording) return;
    if (!notes.length && !open.size) t0 = now();  // a gravação começa na 1ª nota
    open.set(p, now() - t0);
  }
  function recOff(p) {
    if (!open.has(p)) return;
    const start = open.get(p), end = now() - t0;
    open.delete(p);
    if (end - start >= 0.02) notes.push({ start, end, pitch: p });  // menos que isso o servidor recusa
    refresh();
  }
  const first = () => Math.min(...notes.map((n) => n.start));
  const total = () => (notes.length ? Math.max(...notes.map((n) => n.end)) - first() : 0);
  function refresh() {
    const has = notes.length > 0;
    $r("rec-btn").setAttribute("aria-pressed", recording);
    $r("rec-btn").textContent = recording ? "⏹ Parar" : "⏺ Gravar";
    $r("rec-info").textContent = recording && !has ? "Gravando: toque uma pad para começar"
      : has ? `${recording ? "Gravando · " : ""}${notes.length} notas · ${total().toFixed(1)} s` : "Sem gravação";
    $r("play-btn").disabled = !has || recording;
    $r("clear-btn").disabled = (!has && !recording) || playVoices.length > 0;
    for (const b of root.querySelectorAll(".exp")) b.disabled = !has || recording;
  }
  const reset = () => { stopPlay(); notes = []; open.clear(); };
  $r("rec-btn").onclick = () => {
    stopAll();
    if (recording) { for (const p of [...open.keys()]) recOff(p); recording = false; }
    else { reset(); recording = true; }
    refresh(); kb.focus();
  };
  $r("clear-btn").onclick = () => { reset(); recording = false; refresh(); };

  const playing = new Set();  // pads acesas durante a reprodução
  let playTimers = [], playVoices = [];
  function stopPlay() {
    playTimers.forEach(clearTimeout); playTimers = [];
    for (const [o, g] of playVoices) { level(g, 0, ctx.currentTime); o.stop(ctx.currentTime + 0.03); }
    playVoices = [];
    playing.clear(); $r("play-btn").textContent = "▶ Reproduzir"; paint(); refresh();
  }
  $r("play-btn").onclick = () => {
    if (playVoices.length) { stopPlay(); return; }
    stopAll();
    const c = audio(), t = c.currentTime + 0.05, off = first();
    // cada mudança no conjunto de notas ajusta o volume das vozes (pico dividido entre elas)
    const edges = [...new Set(notes.flatMap((n) => [n.start, n.end]))].sort((a, b) => a - b);
    for (const n of notes) {
      const a = t + n.start - off, b = t + n.end - off;
      const [o, g] = voice(c, n.pitch);
      for (const e of edges) {
        if (e < n.start || e >= n.end) continue;
        const count = notes.filter((m) => m.start <= e && m.end > e).length;
        g.gain.setTargetAtTime(amp / count, t + e - off, 0.002);
      }
      g.gain.setTargetAtTime(0, b, 0.002);
      o.start(a); o.stop(b + 0.03);
      playVoices.push([o, g]);
      playTimers.push(setTimeout(() => { playing.add(n.pitch); paint(); }, (a - c.currentTime) * 1000));
      playTimers.push(setTimeout(() => { playing.delete(n.pitch); paint(); }, (b - c.currentTime) * 1000));
    }
    playTimers.push(setTimeout(stopPlay, (t + total() - c.currentTime) * 1000 + 100));
    $r("play-btn").textContent = "⏹ Parar"; refresh();
  };

  for (const btn of root.querySelectorAll(".exp")) {
    btn.onclick = async () => {
      const fmt = btn.dataset.fmt, label = btn.textContent;
      btn.disabled = true; btn.textContent = "…";
      try {
        const res = await fetch("/api/live/export", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ format: fmt, fit: $r("fit").checked, notes }),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(typeof body.detail === "string" ? body.detail : "erro " + res.status);
        }
        const a = document.createElement("a");
        a.href = URL.createObjectURL(await res.blob());
        a.download = `soundpad.${fmt}`; a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 10000);
      } catch (e) { $r("rec-info").textContent = "Erro ao exportar: " + e.message; }
      btn.textContent = label; refresh();
    };
  }

  // --- entrada ---------------------------------------------------------------------------------
  const padAt = (x, y) => {
    const el = document.elementFromPoint(x, y);
    const key = el && el.closest("[data-p]");
    return key && !key.disabled && kb.contains(key) ? +key.dataset.p : null;
  };
  // cada dedo (pointerId) segura a sua própria nota
  kb.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    kb.setPointerCapture(e.pointerId); kb.focus();
    press("p" + e.pointerId, padAt(e.clientX, e.clientY));
  });
  kb.addEventListener("pointermove", (e) => {
    const id = "p" + e.pointerId;
    if (inputs.has(id)) press(id, padAt(e.clientX, e.clientY));
  });
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) {
    kb.addEventListener(ev, (e) => lift("p" + e.pointerId));
  }
  kb.addEventListener("contextmenu", (e) => e.preventDefault());  // toque longo não abre menu

  function shift(dir) { base = Math.min(Math.max(base + 12 * dir, LOW), HIGH - 11); paint(); }
  document.addEventListener("keydown", (e) => {
    if (e.target.closest && e.target.closest("input, select, textarea, button")) return;
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    const k = e.key.toLowerCase();
    if (k === "z") { shift(-1); e.preventDefault(); return; }
    if (k === "x") { shift(1); e.preventDefault(); return; }
    if (!(k in KEYS) || e.repeat) return;
    const p = base + KEYS[k];
    if (!visible().includes(p)) return;
    press("k" + k, p); e.preventDefault();
  });
  document.addEventListener("keyup", (e) => lift("k" + e.key.toLowerCase()));
  window.addEventListener("blur", stopAll);
  $r("fit").addEventListener("change", () => { stopAll(); build(); });
  // preferências deste aparelho
  for (const id of ["view", "hold"]) {
    const sel = $r(id);
    try {
      const saved = localStorage.getItem("coil.kb." + id);
      if ([...sel.options].some((o) => o.value === saved)) sel.value = saved;
    } catch (e) { /* sem storage */ }
    sel.addEventListener("change", () => {
      try { localStorage.setItem("coil.kb." + id, sel.value); } catch (e) { /* sem storage */ }
      stopAll();
      if (id === "view") build();
      sel.blur(); kb.focus();
    });
  }
  new ResizeObserver(() => { if (shapeNow() !== shape) build(); }).observe(kb);

  window.LiveKeyboard = {
    configure({ peakDb, minHz, maxHz, notes: list }) {
      if (typeof peakDb === "number") amp = 10 ** (peakDb / 20);
      range = minHz && maxHz ? [minHz, maxHz] : null;
      heard = list && list.length ? new Set(list) : null;
      build();
    },
  };
  build(); refresh();
})();
