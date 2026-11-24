// Teclado ao vivo: onda quadrada (OscillatorNode "square" = duty 50%) no navegador, 1 nota por vez
// como a bobina. Faixa C2-B6, a mesma do teclado de teslacoil.app; as teclas fora da faixa calibrada
// ficam esmaecidas, mas tocam. Nível = peak_db do servidor (o mesmo do WAV gerado).
(() => {
  const LOW = 36, HIGH = 95;  // C2..B6 (MIDI)
  const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const BLACK = new Set([1, 3, 6, 8, 10]);
  // teclas do computador (linha A-K = C a D+1; como no teslacoil.app: Z/X trocam a oitava)
  const KEYS = { a: 0, w: 1, s: 2, e: 3, d: 4, f: 5, t: 6, g: 7, y: 8, h: 9, u: 10, j: 11, k: 12, o: 13, l: 14 };
  const hz = (p) => 440 * 2 ** ((p - 69) / 12);
  const label = (p) => NAMES[p % 12] + (Math.floor(p / 12) - 1);

  const root = document.getElementById("kb-box");
  if (!root) return;
  const kb = root.querySelector(".kb"), info = root.querySelector("#kb-info");
  const octLabel = root.querySelector("#kb-oct");
  let base = 60;  // C4 = primeira tecla visível
  let amp = 10 ** (-8 / 20), range = null;  // range = [minHz, maxHz] calibrados
  let ctx = null, osc = null, gain = null, current = null, held = new Set();

  const full = root.dataset.full !== undefined;  // página dedicada: usa a largura toda
  // tecla branca com pelo menos ~44 px (alvo de toque); página dedicada mostra até as 5 oitavas
  const octaves = () => {
    const w = kb.clientWidth;
    if (full) return Math.max(1, Math.min(5, Math.floor(w / 308)));
    return w && w < 480 ? 1 : 2;
  };
  const top = () => Math.min(base + 12 * octaves() - 1, HIGH);

  let shown = 0;  // oitavas desenhadas
  function build() {
    shown = octaves();
    base = Math.min(Math.max(base, LOW), HIGH + 1 - 12 * octaves());
    kb.textContent = "";
    const last = top();
    const whites = [];
    for (let p = base; p <= last; p++) if (!BLACK.has(p % 12)) whites.push(p);
    const w = 100 / whites.length;
    const make = (p, isBlack, left) => {
      const b = document.createElement("button");
      b.type = "button"; b.tabIndex = -1; b.dataset.p = p;
      b.className = "key " + (isBlack ? "black" : "white");
      const f = hz(p);
      if (range && (f < range[0] || f > range[1])) b.classList.add("out");
      b.setAttribute("aria-label", `${label(p)} ${Math.round(f)} Hz`);
      b.style.left = left + "%"; b.style.width = (isBlack ? w * 0.6 : w) + "%";
      if (!isBlack) { const s = document.createElement("span"); s.textContent = label(p); b.appendChild(s); }
      return b;
    };
    whites.forEach((p, i) => kb.appendChild(make(p, false, i * w)));
    for (let p = base; p <= last; p++) {
      if (!BLACK.has(p % 12)) continue;
      const i = whites.indexOf(p - 1);  // a branca anterior (sempre existe: preta nunca é a 1ª tecla)
      if (i >= 0) kb.appendChild(make(p, true, (i + 1) * w - w * 0.3));
    }
    octLabel.textContent = `${label(base)}–${label(last)}`;
    paint();
  }

  function paint() {
    for (const k of kb.children) k.classList.toggle("on", current !== null && +k.dataset.p === current);
    if (current === null) { info.textContent = "Toque uma tecla (1 nota por vez)."; return; }
    const f = hz(current), out = range && (f < range[0] || f > range[1]);
    info.textContent = `${label(current)} · ${f.toFixed(1)} Hz${out ? " · fora da faixa calibrada" : ""}`;
  }

  function start(p) {
    if (p === current) return;
    ctx = ctx || new AudioContext();
    ctx.resume();
    const t = ctx.currentTime;
    if (!osc) {
      osc = ctx.createOscillator(); gain = ctx.createGain();
      osc.type = "square"; gain.gain.value = 0;
      osc.connect(gain).connect(ctx.destination); osc.start();
    }
    osc.frequency.setValueAtTime(hz(p), t);  // trocar de tecla com o dedo apertado = mesma voz
    gain.gain.cancelScheduledValues(t);
    gain.gain.setTargetAtTime(amp, t, 0.002);  // rampa de ~2 ms evita estalo (como o render)
    current = p; paint();
  }

  function stop() {
    if (!osc) { current = null; paint(); return; }
    const t = ctx.currentTime, o = osc;
    gain.gain.cancelScheduledValues(t);
    gain.gain.setTargetAtTime(0, t, 0.002);
    o.stop(t + 0.03);
    osc = gain = null; current = null; paint();
  }

  const keyAt = (x, y) => {
    const el = document.elementFromPoint(x, y);
    return el && el.classList.contains("key") && kb.contains(el) ? +el.dataset.p : null;
  };
  kb.addEventListener("pointerdown", (e) => {
    kb.setPointerCapture(e.pointerId); kb.focus();
    const p = keyAt(e.clientX, e.clientY);
    if (p !== null) start(p);
    e.preventDefault();
  });
  kb.addEventListener("pointermove", (e) => {
    if (!kb.hasPointerCapture(e.pointerId)) return;  // só desliza com o botão/dedo apertado
    const p = keyAt(e.clientX, e.clientY);
    if (p === null) stop(); else start(p);
  });
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) kb.addEventListener(ev, stop);

  function shift(dir) { base += 12 * dir; build(); }
  root.querySelector("#kb-down").onclick = () => shift(-1);
  root.querySelector("#kb-up").onclick = () => shift(1);
  const keys = full ? document : kb;  // na página dedicada o teclado do computador funciona sempre
  keys.addEventListener("keydown", (e) => {
    if (e.target.closest && e.target.closest("input, select, textarea")) return;
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    const k = e.key.toLowerCase();
    if (k === "z") { shift(-1); e.preventDefault(); return; }
    if (k === "x") { shift(1); e.preventDefault(); return; }
    if (!(k in KEYS) || e.repeat) return;
    const p = base + KEYS[k];
    if (p > HIGH) return;
    held.add(k); start(p); e.preventDefault();
  });
  keys.addEventListener("keyup", (e) => {
    const k = e.key.toLowerCase();
    if (!held.delete(k)) return;
    const rest = [...held].pop();  // soltou uma tecla com outra ainda apertada: volta para ela
    if (rest === undefined) stop(); else start(base + KEYS[rest]);
  });
  kb.addEventListener("blur", () => { held.clear(); stop(); });
  new ResizeObserver(() => { if (octaves() !== shown) build(); }).observe(kb);

  // API para a página: nível e faixa calibrada vêm do servidor
  window.LiveKeyboard = {
    configure({ peakDb, minHz, maxHz }) {
      if (typeof peakDb === "number") amp = 10 ** (peakDb / 20);
      range = minHz && maxHz ? [minHz, maxHz] : null;
      build();
    },
  };
  build();
})();
