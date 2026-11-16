"use strict";
/* Piano roll monofônico em canvas: selecionar, mover, redimensionar, excluir, criar, desfazer.
   Sem dependências. Os tempos são em segundos e as alturas em números MIDI. */
(function () {
  const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const MIN_DUR = 0.02, SNAP = 0.01, EDGE = 6, LABEL_W = 38, RULER_H = 18, TAP_MS = 350;
  const noteName = (p) => NAMES[p % 12] + (Math.floor(p / 12) - 1);
  const isBlack = (p) => [1, 3, 6, 8, 10].includes(p % 12);
  const snap = (t) => Math.round(t / SNAP) * SNAP;
  const clone = (n) => ({ id: n.id, start: n.start, end: n.end, pitch: n.pitch });

  class PianoRoll {
    constructor(canvas, opts = {}) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.onChange = opts.onChange || (() => {});
      this.onSeek = opts.onSeek || (() => {});
      this.onPreview = opts.onPreview || (() => {});
      this.notes = []; this.generated = [];
      this.minPitch = 48; this.maxPitch = 84;
      this.pps = 80; this.scrollX = 0; this.playTime = 0; this.duration = 10;
      this.sel = new Set();
      this.undoStack = []; this.redoStack = [];
      this.drag = null; this.lastTap = null; this.tmp = 0; this.dirty = false;
      canvas.tabIndex = 0;
      canvas.addEventListener("pointerdown", (e) => this.down(e));
      canvas.addEventListener("pointermove", (e) => this.move(e));
      canvas.addEventListener("pointerup", (e) => this.up(e));
      canvas.addEventListener("pointercancel", () => { this.drag = null; });
      canvas.addEventListener("keydown", (e) => this.key(e));
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
    deleteSelected() {
      if (!this.sel.size) return;
      this.pushUndo(this.snapshot());
      this.notes = this.notes.filter((n) => !this.sel.has(n.id));
      this.sel.clear(); this.markDirty();
    }
    nudge(dt, dp) {
      const group = this.notes.filter((n) => this.sel.has(n.id));
      if (!group.length) return;
      const snap = this.snapshot();
      if (!this.shift(group, group.map(clone), dt, dp)) return;
      this.pushUndo(snap); this.markDirty();
    }
    // aplica dt/dp ao grupo a partir dos originais, respeitando início >= 0 e a faixa da bobina
    shift(group, orig, dt, dp) {
      dt = Math.max(dt, -Math.min(...orig.map((n) => n.start)));
      dp = Math.max(this.minPitch - Math.min(...orig.map((n) => n.pitch)), Math.min(this.maxPitch - Math.max(...orig.map((n) => n.pitch)), dp));
      let changed = false;
      group.forEach((n, i) => {
        const s = +(orig[i].start + dt).toFixed(3), e = +(orig[i].end + dt).toFixed(3), p = orig[i].pitch + dp;
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
    zoom(f) {
      const center = (this.canvas.clientWidth - LABEL_W) / 2;
      const t = (this.scrollX + center) / this.pps;
      this.pps = Math.min(400, Math.max(15, this.pps * f));
      this.scrollX = Math.max(0, t * this.pps - center); this.draw();
    }
    setPlayhead(t) { this.playTime = t; this.draw(); }
    setAudioDuration(d) { this.audioDuration = d; this.updateDuration(); this.draw(); }

    // ---------- geometria ----------
    get rowH() { return Math.max(8, Math.min(18, Math.floor(420 / (this.maxPitch - this.minPitch + 1)))); }
    x(t) { return LABEL_W + t * this.pps - this.scrollX; }
    t(x) { return (x - LABEL_W + this.scrollX) / this.pps; }
    y(p) { return RULER_H + (this.maxPitch - p) * this.rowH; }
    pitchAt(y) { return this.maxPitch - Math.floor((y - RULER_H) / this.rowH); }
    noteAt(x, y) {
      for (let i = this.notes.length - 1; i >= 0; i--) {
        const n = this.notes[i], ny = this.y(n.pitch);
        if (x >= this.x(n.start) && x <= this.x(n.end) && y >= ny && y < ny + this.rowH) return n;
      }
      return null;
    }
    pos(e) { const r = this.canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }

    // ---------- entrada ----------
    down(e) {
      this.canvas.focus(); this.canvas.setPointerCapture(e.pointerId);
      const [x, y] = this.pos(e);
      if (y < RULER_H && x > LABEL_W) { this.drag = { mode: "seek" }; this.onSeek(Math.max(0, this.t(x))); return; }
      const hit = this.noteAt(x, y);
      if (hit) {
        if (e.shiftKey) { this.sel.has(hit.id) ? this.sel.delete(hit.id) : this.sel.add(hit.id); }
        else if (!this.sel.has(hit.id)) this.sel = new Set([hit.id]);
        const w = this.x(hit.end) - this.x(hit.start);
        let mode = "move";
        if (this.sel.size === 1 && w >= 3 * EDGE) {
          if (x > this.x(hit.end) - EDGE) mode = "resize-end"; else if (x < this.x(hit.start) + EDGE) mode = "resize-start";
        }
        const group = this.notes.filter((n) => this.sel.has(n.id));
        this.drag = { mode, x0: x, y0: y, group, orig: group.map(clone), snap: this.snapshot(), pushed: false, moved: false, lastPitch: hit.pitch };
        this.onPreview(hit.pitch);
      } else {
        if (!e.shiftKey) this.sel.clear();
        this.drag = { mode: "pan", x0: x, y0: y, scroll0: this.scrollX, moved: false };
      }
      this.draw();
    }
    move(e) {
      const [x, y] = this.pos(e), d = this.drag;
      this.canvas.style.cursor = d ? (d.mode === "pan" ? "grabbing" : "") : this.cursorAt(x, y);
      if (!d) return;
      if (d.mode === "seek") { this.onSeek(Math.max(0, this.t(x))); return; }
      const dx = x - d.x0, dy = y - d.y0;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) d.moved = true;
      if (d.mode === "pan") {
        this.scrollX = Math.max(0, Math.min(d.scroll0 - dx, this.duration * this.pps)); this.draw(); return;
      }
      if (!d.moved) return;
      const dt = snap(dx / this.pps);
      let changed = false;
      if (d.mode === "move") {
        changed = this.shift(d.group, d.orig, dt, -Math.round(dy / this.rowH));
        const p = d.group[0].pitch;
        if (p !== d.lastPitch) { d.lastPitch = p; this.onPreview(p); }
      } else if (d.mode === "resize-end") {
        const n = d.group[0], end = Math.max(d.orig[0].start + MIN_DUR, +(d.orig[0].end + dt).toFixed(3));
        changed = end !== n.end; n.end = end;
      } else if (d.mode === "resize-start") {
        const n = d.group[0], start = Math.min(d.orig[0].end - MIN_DUR, Math.max(0, +(d.orig[0].start + dt).toFixed(3)));
        changed = start !== n.start; n.start = start;
      }
      if (changed) { if (!d.pushed) { this.pushUndo(d.snap); d.pushed = true; } this.dirty = true; this.updateDuration(); this.onChange(); this.draw(); }
    }
    up(e) {
      const d = this.drag; this.drag = null;
      const [x, y] = this.pos(e);
      if (!d || d.mode === "seek") return;
      if (d.mode === "pan" && !d.moved && y >= RULER_H) {
        const now = performance.now();
        if (this.lastTap && now - this.lastTap.t < TAP_MS && Math.hypot(x - this.lastTap.x, y - this.lastTap.y) < 12) {
          this.lastTap = null; this.addNote(x, y);
        } else { this.lastTap = { t: now, x, y }; this.onSeek(Math.max(0, this.t(x))); }
      }
      this.draw();
    }
    addNote(x, y) {
      const p = this.pitchAt(y);
      if (p < this.minPitch || p > this.maxPitch) return;
      this.pushUndo(this.snapshot());
      const start = Math.max(0, snap(this.t(x))), id = "n" + (++this.tmp);
      this.notes.push({ id, start, end: +(start + 0.2).toFixed(3), pitch: p });
      this.sel = new Set([id]); this.onPreview(p); this.markDirty();
    }
    cursorAt(x, y) {
      const n = this.noteAt(x, y);
      if (!n) return y < RULER_H ? "pointer" : "grab";
      if (this.sel.size <= 1 && (x > this.x(n.end) - EDGE || x < this.x(n.start) + EDGE)) return "ew-resize";
      return "move";
    }
    key(e) {
      const k = e.key, big = e.shiftKey ? 0.1 : SNAP * 10;
      let used = true;
      if (k === "Delete" || k === "Backspace") this.deleteSelected();
      else if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === "z") e.shiftKey ? this.redo() : this.undo();
      else if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === "y") this.redo();
      else if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === "a") { this.sel = new Set(this.notes.map((n) => n.id)); this.draw(); }
      else if (k === "ArrowUp") this.nudge(0, 1);
      else if (k === "ArrowDown") this.nudge(0, -1);
      else if (k === "ArrowLeft") this.nudge(-big, 0);
      else if (k === "ArrowRight") this.nudge(big, 0);
      else used = false;
      if (used) e.preventDefault();
    }
    wheel(e) {
      e.preventDefault();
      if (e.ctrlKey) this.zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15);
      else { this.scrollX = Math.max(0, Math.min(this.scrollX + (e.deltaX || e.deltaY), this.duration * this.pps)); this.draw(); }
    }

    // ---------- desenho ----------
    resize() {
      const dpr = window.devicePixelRatio || 1, w = this.canvas.clientWidth;
      const h = RULER_H + (this.maxPitch - this.minPitch + 1) * this.rowH;
      this.canvas.style.height = h + "px";
      this.canvas.width = Math.round(w * dpr); this.canvas.height = Math.round(h * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0); this.draw();
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
      const step = this.pps >= 60 ? 1 : this.pps >= 30 ? 2 : 5;
      for (let s = Math.floor(this.t(LABEL_W) / step) * step; this.x(s) < w; s += step) {
        const x = this.x(s); if (x < LABEL_W) continue;
        c.fillStyle = line; c.fillRect(x, RULER_H, 1, h - RULER_H);
        c.fillStyle = muted; c.fillText(s + "s", x + 3, RULER_H / 2);
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
        c.fillStyle = over.has(n.id) ? bad : accent; c.fillRect(x, y + 1, nw, rh - 2);
        if (this.sel.has(n.id)) { c.strokeStyle = fg; c.lineWidth = 2; c.strokeRect(x + 1, y + 2, nw - 2, rh - 4); }
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
    }
  }
  window.PianoRoll = PianoRoll;
})();
