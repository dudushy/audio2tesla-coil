// Utilidades e navegação compartilhadas pelas páginas.
const $ = (id) => document.getElementById(id);
const NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
// altura fracionária (voz com afinação exata) mostra a nota mais próxima
const noteName = (p) => { const r = Math.round(p); return NOTES[((r % 12) + 12) % 12] + (Math.floor(r / 12) - 1); };

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

// erro de rede (conexão caiu, celular bloqueou a tela...) vem com .network; do servidor, com .status
async function getJSON(url, opts) {
  let res;
  try { res = await fetch(url, opts); }
  catch (e) {
    const err = new Error("Sem conexão com o servidor (o computador está ligado e na mesma rede?).");
    err.network = true;
    throw err;
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(typeof body.detail === "string" ? body.detail : "erro " + res.status);
    err.status = res.status;
    throw err;
  }
  return body;
}
const putJSON = (url, data) => getJSON(url, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
const postJSON = (url, data) => getJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
const loadState = () => getJSON("/api/state").catch(() => null);
