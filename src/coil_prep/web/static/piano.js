// Soundpad: uma pad por nota (C2-B6, a mesma faixa do teclado de teslacoil.app), onda quadrada
// (OscillatorNode "square" = duty 50%) no navegador, 1 nota por vez como a bobina. Pads fora da
// faixa calibrada ficam esmaecidas, mas tocam. Nível = peak_db do servidor (o mesmo do WAV gerado).
// Gravação: lista de notas {start,end,pitch}; exporta pelo servidor (/api/live/export).
(() => {
  const LOW = 36, HIGH = 95;  // C2..B6 (MIDI)
  const BLACK = new Set([1, 3, 6, 8, 10]);
  const HUES = [215, 160, 95, 40, 350];  // uma cor por oitava (C2..C6; B6 usa a última)
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
  let amp = 10 ** (-8 / 20), range = null;
  let ctx = null, osc = null, gain = null, current = null;
  const held = [];

  // --- grade -----------------------------------------------------------------------------------
  let cols = 0;
  const colsNow = () => (kb.clientWidth < 600 ? 6 : 12);  // celular em pé: 2 linhas por oitava
  function build() {
    cols = colsNow();
    kb.textContent = "";
    kb.style.setProperty("--cols", cols);
    const rows = Math.ceil((HIGH - LOW + 1) / cols);
    kb.style.setProperty("--rows", rows);
    for (let p = LOW; p <= HIGH; p++) {
      const i = p - LOW, b = document.createElement("button");
      b.type = "button"; b.tabIndex = -1; b.dataset.p = p;
      b.className = "pad" + (BLACK.has(p % 12) ? " sharp" : "");
      b.style.setProperty("--h", hue(p));
      b.style.gridColumn = (i % cols) + 1;
      b.style.gridRow = rows - Math.floor(i / cols);  // grave embaixo
      const f = hz(p);
      if (range && (f < range[0] || f > range[1])) b.classList.add("out");
      b.setAttribute("aria-label", `${noteName(p)} ${Math.round(f)} Hz`);
      b.innerHTML = `<b>${noteName(p)}</b><small>${Math.round(f)} Hz</small>`;
      kb.appendChild(b);
    }
    paint();
  }
  const padOf = (p) => kb.children[p - LOW];

  function paint() {
    for (const k of kb.children) {
      const p = +k.dataset.p;
      k.classList.toggle("on", p === current || playing.has(p));
      k.classList.toggle("base", octOf(p) === octOf(base));
    }
    if (current === null) { info.textContent = "Toque uma pad (1 nota por vez)."; return; }
    const f = hz(current), out = range && (f < range[0] || f > range[1]);
    info.textContent = `${noteName(current)} · ${f.toFixed(1)} Hz${out ? " · fora da faixa calibrada" : ""}`;
  }

  // --- áudio -----------------------------------------------------------------------------------
  const audio = () => { ctx = ctx || new AudioContext(); ctx.resume(); return ctx; };
  function voice(c) {
    const o = c.createOscillator(), g = c.createGain();
    o.type = "square"; g.gain.value = 0;
    o.connect(g).connect(c.destination); o.start();
    return [o, g];
  }
  const attack = (g, t) => { g.gain.cancelScheduledValues(t); g.gain.setTargetAtTime(amp, t, 0.002); };
  const release = (g, t) => { g.gain.cancelScheduledValues(t); g.gain.setTargetAtTime(0, t, 0.002); };

  function start(p) {
    if (p === current) return;
    const c = audio(), t = c.currentTime;
    if (!osc) [osc, gain] = voice(c);
    osc.frequency.setValueAtTime(hz(p), t);  // trocar de pad com o dedo apertado = mesma voz
    attack(gain, t);
    recOn(p); current = p; paint();
  }
  function stop() {
    recOff();
    if (!osc) { current = null; paint(); return; }
    const t = ctx.currentTime, o = osc;
    release(gain, t); o.stop(t + 0.03);
    osc = gain = null; current = null; paint();
  }

  // --- gravação e reprodução -------------------------------------------------------------------
  let recording = false, t0 = 0, notes = [], open = null;
  const now = () => performance.now() / 1000;
  function recOn(p) {
    if (!recording) return;
    recOff();
    if (!notes.length) t0 = now();  // a gravação começa na 1ª nota
    open = { start: now() - t0, pitch: p };
  }
  function recOff() {
    if (!open) return;
    open.end = now() - t0;
    if (open.end - open.start >= 0.02) notes.push(open);  // menos que isso o servidor recusa
    open = null; refresh();
  }
  const total = () => (notes.length ? notes[notes.length - 1].end - notes[0].start : 0);
  function refresh() {
    const has = notes.length > 0;
    $r("rec-btn").setAttribute("aria-pressed", recording);
    $r("rec-btn").textContent = recording ? "⏹ Parar" : "⏺ Gravar";
    $r("rec-info").textContent = recording && !has ? "Gravando: toque uma pad para começar"
      : has ? `${recording ? "Gravando · " : ""}${notes.length} notas · ${total().toFixed(1)} s` : "Sem gravação";
    $r("play-btn").disabled = !has || recording;
    $r("clear-btn").disabled = (!has && !recording) || playing.size > 0;
    for (const b of root.querySelectorAll(".exp")) b.disabled = !has || recording;
  }
  $r("rec-btn").onclick = () => {
    if (recording) { recOff(); recording = false; }
    else { stopPlay(); notes = []; open = null; recording = true; }
    refresh(); kb.focus();
  };
  $r("clear-btn").onclick = () => { stopPlay(); notes = []; open = null; recording = false; refresh(); };

  const playing = new Set();  // pads acesas durante a reprodução
  let playTimers = [], pOsc = null, pGain = null;
  function stopPlay() {
    playTimers.forEach(clearTimeout); playTimers = [];
    if (pOsc) { release(pGain, ctx.currentTime); pOsc.stop(ctx.currentTime + 0.03); pOsc = pGain = null; }
    playing.clear(); $r("play-btn").textContent = "▶ Reproduzir"; paint(); refresh();
  }
  $r("play-btn").onclick = () => {
    if (pOsc) { stopPlay(); return; }
    stop();
    const c = audio(), t = c.currentTime + 0.05, off = notes[0].start;
    [pOsc, pGain] = voice(c);
    for (const n of notes) {
      const a = t + n.start - off, b = t + n.end - off;
      pOsc.frequency.setValueAtTime(hz(n.pitch), a);
      pGain.gain.setTargetAtTime(amp, a, 0.002);
      pGain.gain.setTargetAtTime(0, b, 0.002);
      playTimers.push(setTimeout(() => { playing.clear(); playing.add(n.pitch); paint(); }, (a - c.currentTime) * 1000));
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
    return el && el.classList.contains("pad") && kb.contains(el) ? +el.dataset.p : null;
  };
  kb.addEventListener("pointerdown", (e) => {
    kb.setPointerCapture(e.pointerId); kb.focus();
    const p = padAt(e.clientX, e.clientY);
    if (p !== null) start(p);
    e.preventDefault();
  });
  kb.addEventListener("pointermove", (e) => {
    if (!kb.hasPointerCapture(e.pointerId)) return;
    const p = padAt(e.clientX, e.clientY);
    if (p === null) stop(); else start(p);
  });
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) kb.addEventListener(ev, stop);

  function shift(dir) { base = Math.min(Math.max(base + 12 * dir, 36), 84); paint(); }
  document.addEventListener("keydown", (e) => {
    if (e.target.closest && e.target.closest("input, select, textarea, button")) return;
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    const k = e.key.toLowerCase();
    if (k === "z") { shift(-1); e.preventDefault(); return; }
    if (k === "x") { shift(1); e.preventDefault(); return; }
    if (!(k in KEYS) || e.repeat) return;
    const p = base + KEYS[k];
    if (p > HIGH) return;
    held.push(k); start(p); e.preventDefault();
  });
  document.addEventListener("keyup", (e) => {
    const k = e.key.toLowerCase(), i = held.indexOf(k);
    if (i < 0) return;
    held.splice(i, 1);
    if (!held.length) stop(); else start(base + KEYS[held[held.length - 1]]);
  });
  window.addEventListener("blur", () => { held.length = 0; stop(); });
  new ResizeObserver(() => { if (colsNow() !== cols) build(); }).observe(kb);

  window.LiveKeyboard = {
    configure({ peakDb, minHz, maxHz }) {
      if (typeof peakDb === "number") amp = 10 ** (peakDb / 20);
      range = minHz && maxHz ? [minHz, maxHz] : null;
      build();
    },
  };
  build(); refresh();
})();
