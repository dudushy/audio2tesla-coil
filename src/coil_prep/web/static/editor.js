"use strict";
/* Piano roll monofônico em canvas. Sem dependências. Tempos em segundos, alturas em números MIDI.
   Ferramentas: Selecionar (arrasto = seleção em retângulo) e Desenhar (arrasto = cria nota).
   Mouse: botão direito exclui, Ctrl+arrasto copia, Alt ignora a grade, roda = rolar, Ctrl+roda = zoom.
   Toque: arrasto curto = rolar, segurar e arrastar = seleção em retângulo, pinça = zoom, 2 dedos = rolar. */
(function () {
  const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const MIN_DUR = 0.02, LABEL_W = 38, RULER_H = 18, TAP_MS = 350, HOLD_MS = 380, MIN_PPS = 5, MAX_PPS = 400;
  const noteName = (p) => NAMES[p % 12] + (Math.floor(p / 12) - 1);
  const isBlack = (p) => [1, 3, 6, 8, 10].includes(p % 12);
  const r3 = (v) => Math.round(v * 1000) / 1000;
  const clone = (n) => ({ id: n.id, start: n.start, end: n.end, pitch: n.pitch });
  const coarse = () => matchMedia("(pointer: coarse)").matches;

  class PianoRoll {
    constructor(canvas, opts = {}) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      for (const k of ["onChange", "onSeek", "onPreview", "onTogglePlay", "onSelect", "onTool"]) this[k] = opts[k] || (() => {});
      this.notes = []; this.generated = []; this.clip = [];
      this.minPitch = 48; this.maxPitch = 84;
      this.pps = 80; this.scrollX = 0; this.playTime = 0; this.duration = 10; this.audioDuration = 0;
      this.tool = "select"; this.snapStep = 0.05; this.follow = true; this.playing = false; this.lastLen = 0.2;
      this.sel = new Set();
      this.undoStack = []; this.redoStack = [];
      this.drag = null; this.lastTap = null; this.tmp = 0; this.dirty = false; this.lastSelKey = "";
      this.ptrs = new Map(); this.gesture = null; this.spaceDown = false; this.hold = null; this.raf = 0;
      canvas.tabIndex = 0;
      canvas.addEventListener("pointerdown", (e) => this.down(e));
      canvas.addEventListener("pointermove", (e) => this.move(e));
      canvas.addEventListener("pointerup", (e) => this.up(e));
      canvas.addEventListener("pointercancel", (e) => this.cancel(e));
      canvas.addEventListener("contextmenu", (e) => e.preventDefault());
      canvas.addEventListener("keydown", (e) => this.key(e));
      canvas.addEventListener("keyup", (e) => { if (e.key === " ") this.spaceDown = false; });
      canvas.addEventListener("wheel", (e) => this.wheel(e), { passive: false });
      new ResizeObserver(() => this.resize()).observe(canvas);
      matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => this.draw());
    }

    // ---------- dados ----------
    setData({ notes, generated, minPitch, maxPitch }) {
      this.notes = notes.map(clone);
      this.generated = generated.map(clone);
      this.minPitch = minPitch; this.maxPitch = maxPitch;
      this.sel.clear(); this.undoStack = []; this.redoStack = []; this.dirty = false;
      this.updateDuration(); this.scrollX = 0; this.resize();
    }
    // notas novas ainda não têm id inteiro: o servidor atribui
    getNotes() {
      return this.notes.map((n) => ({ id: Number.isInteger(n.id) ? n.id : null, start: n.start, end: n.end, pitch: n.pitch }));
    }
    updateDuration() {
      const end = this.notes.reduce((m, n) => Math.max(m, n.end), 0);
      this.duration = Math.max(end + 1, this.audioDuration || 0, 5);
    }
    markDirty() { this.dirty = true; this.updateDuration(); this.onChange(); this.draw(); }
    snapshot() { return JSON.stringify(this.notes); }
    pushUndo(snap) { this.undoStack.push(snap); if (this.undoStack.length > 200) this.undoStack.shift(); this.redoStack = []; }
    undo() {
      if (!this.undoStack.length) return;
      this.redoStack.push(this.snapshot());
      this.notes = JSON.parse(this.undoStack.pop()); this.sel.clear(); this.markDirty();
    }
    redo() {
      if (!this.redoStack.length) return;
      this.undoStack.push(this.snapshot());
      this.notes = JSON.parse(this.redoStack.pop()); this.sel.clear(); this.markDirty();
    }
    selected() { return this.notes.filter((n) => this.sel.has(n.id)); }
    selectAll() { this.sel = new Set(this.notes.map((n) => n.id)); this.draw(); }
    clearSel() { this.sel.clear(); this.draw(); }
    deleteSelected() {
      if (!this.sel.size) return;
      this.pushUndo(this.snapshot());
      this.notes = this.notes.filter((n) => !this.sel.has(n.id));
      this.sel.clear(); this.markDirty();
    }
    copy() {
      const g = this.selected();
      if (!g.length) return false;
      const t0 = Math.min(...g.map((n) => n.start));
      this.clip = g.map((n) => ({ start: r3(n.start - t0), end: r3(n.end - t0), pitch: n.pitch }));
      return true;
    }
    cut() { if (this.copy()) this.deleteSelected(); }
    // cola no cursor de reprodução; sem argumento usa a área de transferência interna
    paste(at = this.playTime, items = this.clip) {
      if (!items.length) return;
      this.pushUndo(this.snapshot());
      const t0 = Math.max(0, this.snapT(at, true)), ids = new Set();
      for (const c of items) {
        const id = "n" + (++this.tmp); ids.add(id);
        this.notes.push({ id, start: r3(t0 + c.start), end: r3(t0 + c.end), pitch: c.pitch });
      }
      this.sel = ids; this.markDirty(); this.revealTime(t0);
    }
    // duplica a seleção logo depois dela mesma (como Ctrl+D / Ctrl+B)
    duplicate() {
      const g = this.selected();
      if (!g.length) return;
      const t0 = Math.min(...g.map((n) => n.start)), t1 = Math.max(...g.map((n) => n.end));
      this.paste(t1, g.map((n) => ({ start: r3(n.start - t0), end: r3(n.end - t0), pitch: n.pitch })));
    }
    nudge(dt, dp) {
      const group = this.selected();
      if (!group.length) return;
      const snap = this.snapshot();
      if (!this.shift(group, group.map(clone), dt, dp)) return;
      this.pushUndo(snap); this.markDirty();
      if (dp) this.onPreview(group[0].pitch);
    }
    // aplica dt/dp ao grupo a partir dos originais, respeitando início >= 0 e a faixa da bobina
    shift(group, orig, dt, dp) {
      dt = Math.max(dt, -Math.min(...orig.map((n) => n.start)));
      dp = Math.max(this.minPitch - Math.min(...orig.map((n) => n.pitch)), Math.min(this.maxPitch - Math.max(...orig.map((n) => n.pitch)), dp));
      let changed = false;
      group.forEach((n, i) => {
        const s = r3(orig[i].start + dt), e = r3(orig[i].end + dt), p = orig[i].pitch + dp;
        if (n.start !== s || n.pitch !== p) changed = true;
        n.start = s; n.end = e; n.pitch = p;
      });
      return changed;
    }
    overlapping() {
      const bad = new Set();
      const s = [...this.notes].sort((a, b) => a.start - b.start);
      for (let i = 1; i < s.length; i++) if (s[i].start < s[i - 1].end - 1e-6) { bad.add(s[i].id); bad.add(s[i - 1].id); }
      return bad;
    }
    snapT(t, bypass) { return this.snapStep && !bypass ? r3(Math.round(t / this.snapStep) * this.snapStep) : r3(t); }

    // ---------- vista ----------
    get viewW() { return this.canvas.clientWidth - LABEL_W; }
    clampScroll() { this.scrollX = Math.max(0, Math.min(this.scrollX, Math.max(0, this.duration * this.pps - this.viewW * 0.5))); }
    // zoom mantendo o instante sob a coordenada ax parado na tela
    zoom(f, ax = LABEL_W + this.viewW / 2) {
      const t = this.t(ax);
      this.pps = Math.min(MAX_PPS, Math.max(MIN_PPS, this.pps * f));
      this.scrollX = t * this.pps - (ax - LABEL_W); this.clampScroll(); this.draw();
    }
    fit() {
      const end = this.notes.reduce((m, n) => Math.max(m, n.end), 0) || this.duration;
      this.pps = Math.min(MAX_PPS, Math.max(MIN_PPS, (this.viewW - 16) / end)); this.scrollX = 0; this.draw();
    }
    revealTime(t) {
      if (this.x(t) < LABEL_W || this.x(t) > this.canvas.clientWidth - 20) { this.scrollX = Math.max(0, t * this.pps - this.viewW * 0.25); this.clampScroll(); this.draw(); }
    }
    setTool(t) { this.tool = t; this.onTool(t); this.draw(); }
    setSnap(step) { this.snapStep = step; this.draw(); }
    setPlaying(on) { this.playing = on; }
    setPlayhead(t) {
      this.playTime = t;
      if (this.follow && this.playing && !this.drag && !this.gesture) {
        const px = this.x(t), w = this.canvas.clientWidth;
        if (px > w - 30 || px < LABEL_W) { this.scrollX = Math.max(0, t * this.pps - this.viewW * 0.15); this.clampScroll(); }
      }
      this.draw();
    }
    setAudioDuration(d) { this.audioDuration = d; this.updateDuration(); this.draw(); }

    // ---------- geometria ----------
    get rowH() { return coarse() ? Math.max(14, Math.min(22, Math.floor(480 / (this.maxPitch - this.minPitch + 1)))) : Math.max(8, Math.min(18, Math.floor(420 / (this.maxPitch - this.minPitch + 1)))); }
    x(t) { return LABEL_W + t * this.pps - this.scrollX; }
    t(x) { return (x - LABEL_W + this.scrollX) / this.pps; }
    y(p) { return RULER_H + (this.maxPitch - p) * this.rowH; }
    pitchAt(y) { return this.maxPitch - Math.floor((y - RULER_H) / this.rowH); }
    // slop: tolerância em px (dedo); escolhe a nota mais próxima do ponto
    noteAt(x, y, slop = 0) {
      let best = null, bd = Infinity;
      for (const n of this.notes) {
        const ny = this.y(n.pitch), x0 = this.x(n.start), x1 = Math.max(this.x(n.end), x0 + 4);
        const dx = x < x0 ? x0 - x : x > x1 ? x - x1 : 0, dy = y < ny ? ny - y : y > ny + this.rowH ? y - ny - this.rowH : 0;
        if (dx > slop || dy > slop) continue;
        const d = dx + dy;
        if (d < bd) { bd = d; best = n; }
      }
      return best;
    }
    pos(e) { const r = this.canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
    edge(e) { return e.pointerType === "touch" ? 14 : 6; }

    // ---------- entrada ----------
    down(e) {
      this.canvas.focus(); this.canvas.setPointerCapture(e.pointerId);
      const [x, y] = this.pos(e);
      this.ptrs.set(e.pointerId, { x, y });
      if (this.ptrs.size === 2) { this.startGesture(); return; }
      if (this.ptrs.size > 2) return;
      if (e.button === 2) { const n = this.noteAt(x, y); if (n) { this.sel = new Set([n.id]); this.deleteSelected(); } return; }
      if (e.button === 1 || this.spaceDown) { this.beginPan(x, y); this.canvas.style.cursor = "grabbing"; return; }
      if (x < LABEL_W && y >= RULER_H) { this.onPreview(this.pitchAt(y)); return; }
      if (y < RULER_H && x > LABEL_W) { this.drag = { mode: "seek" }; this.onSeek(Math.max(0, this.t(x))); return; }
      const touch = e.pointerType === "touch";
      this.last = { x, y, alt: e.altKey };
      const hit = this.noteAt(x, y, touch ? 8 : 0), edge = this.edge(e);
      const base = { x0: x, y0: y, t0: this.t(x), p0: this.pitchAt(y), moved: false, touch, pushed: false, snap: this.snapshot() };
      if (hit) {
        const add = e.shiftKey;
        if (add) { this.sel.has(hit.id) ? this.sel.delete(hit.id) : this.sel.add(hit.id); }
        else if (!this.sel.has(hit.id)) this.sel = new Set([hit.id]);
        if (!this.sel.has(hit.id)) { this.drag = null; this.draw(); return; }
        const w = this.x(hit.end) - this.x(hit.start);
        let mode = "move";
        if (w >= 2.5 * edge) {
          if (x > this.x(hit.end) - edge) mode = "resize-end"; else if (x < this.x(hit.start) + edge) mode = "resize-start";
        }
        let group = this.selected();
        if ((e.ctrlKey || e.metaKey) && mode === "move") {   // Ctrl+arrastar copia
          this.pushUndo(base.snap); base.pushed = true;
          const copies = group.map((n) => ({ ...clone(n), id: "n" + (++this.tmp) }));
          this.notes.push(...copies); this.sel = new Set(copies.map((n) => n.id));
          group = copies; this.markDirty();
        }
        this.drag = { ...base, mode, group, orig: group.map(clone), ref: clone(hit), lastPitch: hit.pitch };
        this.onPreview(hit.pitch);
      } else if (this.tool === "draw") {
        const p = this.pitchAt(y);
        if (p < this.minPitch || p > this.maxPitch) return;
        if (!e.shiftKey) this.sel.clear();
        this.pushUndo(base.snap);
        const start = Math.max(0, this.snapT(this.t(x), e.altKey)), id = "n" + (++this.tmp);
        const n = { id, start, end: r3(start + this.lastLen), pitch: p };
        this.notes.push(n); this.sel = new Set([id]); this.onPreview(p);
        this.drag = { ...base, mode: "draw", group: [n], orig: [clone(n)], ref: clone(n), pushed: true };
        this.markDirty();
      } else {
        if (!e.shiftKey) this.sel.clear();
        const d = { ...base, mode: "pending", scroll0: this.scrollX, baseSel: new Set(this.sel) };
        this.drag = d;
        if (!touch) d.mode = "marquee";
        else this.hold = setTimeout(() => {   // toque: segurar e arrastar = seleção em retângulo
          if (this.drag === d && !d.moved) { d.mode = "marquee"; navigator.vibrate && navigator.vibrate(12); this.draw(); }
        }, HOLD_MS);
      }
      this.startLoop(); this.draw();
    }
    beginPan(x, y) { this.drag = { mode: "pan", x0: x, y0: y, scroll0: this.scrollX, moved: false }; }
    move(e) {
      const [x, y] = this.pos(e);
      if (this.ptrs.has(e.pointerId)) this.ptrs.set(e.pointerId, { x, y });
      if (this.gesture) { this.updateGesture(); return; }
      const d = this.drag;
      this.canvas.style.cursor = d ? (d.mode === "pan" ? "grabbing" : d.mode === "marquee" ? "crosshair" : "") : this.cursorAt(x, y);
      if (!d) return;
      this.last = { x, y, alt: e.altKey };
      this.update();
    }
    update() {
      const d = this.drag, { x, y, alt } = this.last;
      if (!d) return;
      if (d.mode === "seek") { this.onSeek(Math.max(0, this.t(x))); return; }
      if (Math.abs(x - d.x0) > (d.touch ? 8 : 3) || Math.abs(y - d.y0) > (d.touch ? 8 : 3)) d.moved = true;
      if (d.mode === "pending" && d.moved) { clearTimeout(this.hold); d.mode = "pan"; }
      if (d.mode === "pan") { this.scrollX = d.scroll0 - (x - d.x0); this.clampScroll(); this.draw(); return; }
      if (d.mode === "marquee") { this.marquee(x, y); return; }
      if (!d.moved && d.mode !== "draw") return;
      const dtRaw = this.t(x) - d.t0;
      let changed = false;
      if (d.mode === "move") {
        const dt = this.snapT(d.ref.start + dtRaw, alt) - d.ref.start;
        changed = this.shift(d.group, d.orig, dt, this.pitchAt(y) - d.p0);
        const p = (d.group.find((n) => n.id === d.ref.id) || d.group[0]).pitch;
        if (p !== d.lastPitch) { d.lastPitch = p; this.onPreview(p); }
      } else if (d.mode === "resize-end") {
        const dt = Math.max(this.snapT(d.ref.end + dtRaw, alt) - d.ref.end, ...d.orig.map((o) => MIN_DUR - (o.end - o.start)));
        d.group.forEach((n, i) => { const e = r3(d.orig[i].end + dt); if (e !== n.end) { n.end = e; changed = true; } });
      } else if (d.mode === "resize-start") {
        const dt = Math.min(Math.max(this.snapT(d.ref.start + dtRaw, alt) - d.ref.start, -Math.min(...d.orig.map((o) => o.start))), ...d.orig.map((o) => o.end - o.start - MIN_DUR));
        d.group.forEach((n, i) => { const s = r3(d.orig[i].start + dt); if (s !== n.start) { n.start = s; changed = true; } });
      } else if (d.mode === "draw") {
        if (!d.moved) return;
        const n = d.group[0], end = Math.max(r3(n.start + (this.snapStep || MIN_DUR)), this.snapT(this.t(x), alt));
        if (end !== n.end) { n.end = end; changed = true; }
      }
      if (changed) {
        if (!d.pushed) { this.pushUndo(d.snap); d.pushed = true; }
        this.dirty = true; this.updateDuration(); this.onChange(); this.draw();
      }
    }
    marquee(x, y) {
      const d = this.drag, xa = Math.min(x, this.x(d.t0)), xb = Math.max(x, this.x(d.t0)), ya = Math.min(y, d.y0), yb = Math.max(y, d.y0);
      this.sel = new Set(d.baseSel);
      for (const n of this.notes) {
        const ny = this.y(n.pitch);
        if (this.x(n.end) >= xa && this.x(n.start) <= xb && ny + this.rowH >= ya && ny <= yb) this.sel.add(n.id);
      }
      this.draw();
    }
    // rola sozinho quando o ponteiro fica perto da borda durante um arrasto
    startLoop() {
      if (this.raf) return;
      const tick = () => {
        this.raf = 0;
        const d = this.drag;
        if (!d || !this.last) return;
        if (["move", "resize-end", "resize-start", "marquee", "draw"].includes(d.mode)) {
          const w = this.canvas.clientWidth, x = this.last.x;
          const v = x > w - 28 ? Math.min(28, x - (w - 28)) * 1.2 : x < LABEL_W + 28 ? -Math.min(28, LABEL_W + 28 - x) * 1.2 : 0;
          if (v && (v > 0 || this.scrollX > 0)) { this.scrollX = Math.max(0, this.scrollX + v); this.update(); }
        }
        this.raf = requestAnimationFrame(tick);
      };
      this.raf = requestAnimationFrame(tick);
    }
    up(e) {
      const [x, y] = this.pos(e);
      this.ptrs.delete(e.pointerId);
      clearTimeout(this.hold);
      if (this.gesture) { if (this.ptrs.size < 2) this.gesture = null; this.drag = null; return; }
      const d = this.drag; this.drag = null;
      if (!d || d.mode === "seek") return;
      if ((d.mode === "pending" || d.mode === "pan" || d.mode === "marquee") && !d.moved && y >= RULER_H && x >= LABEL_W) {
        const now = performance.now();
        if (this.lastTap && now - this.lastTap.t < TAP_MS && Math.hypot(x - this.lastTap.x, y - this.lastTap.y) < 14) {
          this.lastTap = null; this.addNote(x, y);
        } else { this.lastTap = { t: now, x, y }; this.onSeek(Math.max(0, this.t(x))); }
      }
      this.draw();
    }
    cancel(e) { this.ptrs.delete(e.pointerId); clearTimeout(this.hold); this.drag = null; if (this.ptrs.size < 2) this.gesture = null; this.draw(); }
    addNote(x, y) {
      const p = this.pitchAt(y);
      if (p < this.minPitch || p > this.maxPitch) return;
      this.pushUndo(this.snapshot());
      const start = Math.max(0, this.snapT(this.t(x))), id = "n" + (++this.tmp);
      this.notes.push({ id, start, end: r3(start + this.lastLen), pitch: p });
      this.sel = new Set([id]); this.onPreview(p); this.markDirty();
    }
    // pinça: zoom ancorado entre os dedos; arrastar com dois dedos também rola
    startGesture() {
      clearTimeout(this.hold);
      this.drag = null;
      const [a, b] = [...this.ptrs.values()];
      this.gesture = { d0: Math.max(20, Math.hypot(a.x - b.x, a.y - b.y)), pps0: this.pps, t0: this.t((a.x + b.x) / 2) };
    }
    updateGesture() {
      const [a, b] = [...this.ptrs.values()], g = this.gesture;
      if (!b) return;
      const cx = (a.x + b.x) / 2;
      this.pps = Math.min(MAX_PPS, Math.max(MIN_PPS, g.pps0 * Math.hypot(a.x - b.x, a.y - b.y) / g.d0));
      this.scrollX = g.t0 * this.pps - (cx - LABEL_W); this.clampScroll(); this.draw();
    }
    cursorAt(x, y) {
      if (this.spaceDown) return "grab";
      if (x < LABEL_W && y >= RULER_H) return "pointer";
      const n = this.noteAt(x, y), e = 6;
      if (!n) return y < RULER_H ? "pointer" : this.tool === "draw" ? "crosshair" : "default";
      if (x > this.x(n.end) - e || x < this.x(n.start) + e) return "ew-resize";
      return "move";
    }
    key(e) {
      const k = e.key, mod = e.ctrlKey || e.metaKey, lk = k.toLowerCase();
      const step = this.snapStep || 0.01, big = e.shiftKey ? step * 10 : step;
      let used = true;
      if (k === "Delete" || k === "Backspace") this.deleteSelected();
      else if (mod && lk === "z") e.shiftKey ? this.redo() : this.undo();
      else if (mod && lk === "y") this.redo();
      else if (mod && lk === "a") this.selectAll();
      else if (mod && lk === "c") this.copy();
      else if (mod && lk === "x") this.cut();
      else if (mod && lk === "v") this.paste();
      else if (mod && lk === "d") this.duplicate();
      else if (k === "Escape") this.clearSel();
      else if (k === " ") { this.spaceDown = true; if (!e.repeat) this.onTogglePlay(); }
      else if (k === "ArrowUp") this.nudge(0, e.shiftKey ? 12 : 1);
      else if (k === "ArrowDown") this.nudge(0, e.shiftKey ? -12 : -1);
      else if (k === "ArrowLeft") this.nudge(-big, 0);
      else if (k === "ArrowRight") this.nudge(big, 0);
      else if (!mod && lk === "e") this.setTool("select");
      else if (!mod && (lk === "p" || lk === "d")) this.setTool("draw");
      else if (!mod && lk === "f") this.fit();
      else if (k === "+" || k === "=") this.zoom(1.25);
      else if (k === "-") this.zoom(1 / 1.25);
      else used = false;
      if (used) e.preventDefault();
    }
    wheel(e) {
      e.preventDefault();
      const [x] = this.pos(e);
      if (e.ctrlKey || e.metaKey) this.zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15, x);
      else { this.scrollX += e.deltaX || e.deltaY; this.clampScroll(); this.draw(); }
    }

    // ---------- desenho ----------
    resize() {
      const dpr = window.devicePixelRatio || 1, w = this.canvas.clientWidth;
      const h = RULER_H + (this.maxPitch - this.minPitch + 1) * this.rowH;
      this.canvas.style.height = h + "px";
      this.canvas.width = Math.round(w * dpr); this.canvas.height = Math.round(h * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0); this.draw();
    }
    emitSel() {
      const g = this.selected(), key = JSON.stringify(g);
      if (key === this.lastSelKey) return;
      this.lastSelKey = key; this.onSelect(g);
    }
    draw() {
      const c = this.ctx, w = this.canvas.clientWidth, h = this.canvas.clientHeight, rh = this.rowH;
      const css = getComputedStyle(this.canvas);
      const col = (v) => css.getPropertyValue(v).trim();
      const bg = col("--bg"), card = col("--card"), line = col("--line"), fg = col("--fg"), muted = col("--muted"), accent = col("--accent"), bad = col("--bad");
      c.clearRect(0, 0, w, h); c.fillStyle = bg; c.fillRect(0, 0, w, h);
      for (let p = this.minPitch; p <= this.maxPitch; p++) {
        const y = this.y(p);
        if (isBlack(p)) { c.fillStyle = card; c.fillRect(LABEL_W, y, w - LABEL_W, rh); }
        if (p % 12 === 0) { c.fillStyle = line; c.fillRect(LABEL_W, y + rh - 1, w - LABEL_W, 1); }
      }
      c.font = "11px system-ui, sans-serif"; c.textBaseline = "middle";
      // linhas da grade: subdivisões (só se couberem) e segundos
      const sub = this.snapStep * this.pps;
      if (this.snapStep && sub >= 7) {
        c.globalAlpha = 0.45; c.fillStyle = line;
        for (let s = Math.floor(this.t(LABEL_W) / this.snapStep) * this.snapStep; this.x(s) < w; s += this.snapStep) { if (this.x(s) >= LABEL_W) c.fillRect(Math.round(this.x(s)), RULER_H, 1, h - RULER_H); }
        c.globalAlpha = 1;
      }
      const step = this.pps >= 60 ? 1 : this.pps >= 30 ? 2 : this.pps >= 12 ? 5 : 10;
      for (let s = Math.floor(this.t(LABEL_W) / step) * step; this.x(s) < w; s += step) {
        const x = this.x(s); if (x < LABEL_W) continue;
        c.fillStyle = line; c.fillRect(x, RULER_H, 1, h - RULER_H);
      }
      // notas geradas que mudaram ou foram apagadas
      c.setLineDash([4, 3]); c.lineWidth = 1; c.strokeStyle = muted;
      const cur = new Map(this.notes.map((n) => [n.id, n]));
      for (const g of this.generated) {
        const n = cur.get(g.id);
        if (n && n.start === g.start && n.end === g.end && n.pitch === g.pitch) continue;
        c.strokeRect(this.x(g.start) + 0.5, this.y(g.pitch) + 0.5, Math.max(2, (g.end - g.start) * this.pps), rh - 2);
      }
      c.setLineDash([]);
      const over = this.overlapping();
      for (const n of this.notes) {
        const x = this.x(n.start), nw = Math.max(2, (n.end - n.start) * this.pps), y = this.y(n.pitch);
        if (x + nw < LABEL_W || x > w) continue;
        const sel = this.sel.has(n.id);
        c.fillStyle = over.has(n.id) ? bad : accent; c.fillRect(x, y + 1, nw, rh - 2);
        if (sel) {
          c.strokeStyle = fg; c.lineWidth = 2; c.strokeRect(x + 1, y + 2, nw - 2, rh - 4);
          if (nw >= 30) { c.fillStyle = fg; c.globalAlpha = 0.55; c.fillRect(x + nw - 5, y + 3, 3, rh - 6); c.fillRect(x + 2, y + 3, 3, rh - 6); c.globalAlpha = 1; }
        }
        if (nw >= 34 && rh >= 11) { c.fillStyle = sel ? fg : bg; c.fillText(noteName(n.pitch), x + 9, y + rh / 2 + 0.5); }
      }
      // retângulo de seleção
      const d = this.drag;
      if (d && d.mode === "marquee" && this.last) {
        const xa = Math.min(this.last.x, this.x(d.t0)), ya = Math.min(this.last.y, d.y0);
        c.fillStyle = accent; c.globalAlpha = 0.15; c.fillRect(xa, ya, Math.abs(this.last.x - this.x(d.t0)), Math.abs(this.last.y - d.y0)); c.globalAlpha = 1;
        c.strokeStyle = accent; c.lineWidth = 1; c.strokeRect(xa + 0.5, ya + 0.5, Math.abs(this.last.x - this.x(d.t0)), Math.abs(this.last.y - d.y0));
      }
      // teclado e régua por cima (ficam fixos ao rolar)
      c.fillStyle = bg; c.fillRect(0, 0, LABEL_W, h); c.fillRect(LABEL_W, 0, w - LABEL_W, RULER_H);
      c.fillStyle = muted;
      for (let p = this.minPitch; p <= this.maxPitch; p++) {
        if (p % 12 === 0 || p === this.minPitch || p === this.maxPitch) c.fillText(noteName(p), 3, this.y(p) + rh / 2);
      }
      for (let s = Math.floor(this.t(LABEL_W) / step) * step; this.x(s) < w; s += step) {
        const x = this.x(s); if (x >= LABEL_W) c.fillText(s + "s", x + 3, RULER_H / 2);
      }
      c.fillStyle = line; c.fillRect(0, RULER_H - 1, w, 1); c.fillRect(LABEL_W - 1, 0, 1, h);
      const px = this.x(this.playTime);
      if (px >= LABEL_W && px <= w) { c.fillStyle = fg; c.fillRect(px, 0, 2, h); }
      this.emitSel();
    }
  }
  window.PianoRoll = PianoRoll;
})();
