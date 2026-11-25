// Utilidades e navegação compartilhadas pelas páginas.
const $ = (id) => document.getElementById(id);
const NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const noteName = (p) => NOTES[p % 12] + (Math.floor(p / 12) - 1);

(() => {
  const here = location.pathname;
  const tabs = [["/", "Gerar"], ["/scores", "Partituras"], ["/soundpad", "Soundpad"], ["/calibrate", "Calibrar"]];
  const nav = document.createElement("nav");
  nav.className = "nav";
  nav.setAttribute("aria-label", "Principal");
  const inner = document.createElement("div");
  inner.className = "nav-in";
  const brand = document.createElement("a");
  brand.className = "brand"; brand.href = "/"; brand.textContent = "coil-prep";
  inner.appendChild(brand);
  for (const [href, text] of tabs) {
    const a = document.createElement("a");
    a.className = "tab"; a.href = href; a.textContent = text;
    // a página da faixa pertence ao fluxo "Gerar"
    if (here === href || (href === "/" && here.startsWith("/track/"))) a.setAttribute("aria-current", "page");
    inner.appendChild(a);
  }
  nav.appendChild(inner);
  document.body.prepend(nav);
})();

async function getJSON(url, opts) {
  const res = await fetch(url, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "erro " + res.status);
  return body;
}
const putJSON = (url, data) => getJSON(url, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
const postJSON = (url, data) => getJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
const loadState = () => getJSON("/api/state").catch(() => null);
