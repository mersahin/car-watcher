// searches.json'un doğrulaması ve insan okunur farkı. Gemini'nin çıktısına güvenmeyiz:
// yazılan her şey buradan geçer, sınır dışı değer → hata (dosyaya yazılmaz).

const LIMITS = { price: [0, 200000], km: [0, 1000000], year: [0, 2100], dist: [0, 2000] };
const MAX_SEARCHES = 15;

function int(v, [lo, hi], field) {
  const n = Number(v);
  if (!Number.isInteger(n) || n < lo || n > hi) throw new Error(`Geçersiz ${field}: ${v}`);
  return n;
}

function str(v, field, max = 60) {
  const s = String(v ?? "").trim();
  if (!s || s.length > max) throw new Error(`Geçersiz ${field}: "${s}"`);
  return s;
}

export function validateConfig(c) {
  if (!c || !c.settings || !Array.isArray(c.searches)) throw new Error("Ayar yapısı bozuk.");
  if (c.searches.length > MAX_SEARCHES) throw new Error(`En fazla ${MAX_SEARCHES} arama olabilir.`);
  const s = c.settings;
  const settings = {
    max_dist_private_km: int(s.max_dist_private_km, LIMITS.dist, "özel satıcı mesafesi"),
    max_dist_dealer_km: int(s.max_dist_dealer_km, LIMITS.dist, "galeri mesafesi"),
    min_price: int(s.min_price, LIMITS.price, "en düşük fiyat"),
    extra_reject_terms: [...new Set((s.extra_reject_terms || []).map((t) => str(t, "eleme kelimesi", 40).toLowerCase()))].slice(0, 30),
  };
  const names = new Set();
  const searches = c.searches.map((x) => {
    const name = str(x.name, "arama adı");
    if (names.has(name.toLowerCase())) throw new Error(`"${name}" iki kez var.`);
    names.add(name.toLowerCase());
    const q = {
      name,
      query: str(x.query, "arama metni").toLowerCase(),
      min_price: int(x.min_price, LIMITS.price, `${name} en düşük fiyat`),
      max_price: int(x.max_price, LIMITS.price, `${name} en yüksek fiyat`),
      max_km: int(x.max_km, LIMITS.km, `${name} km`),
      min_year: int(x.min_year, LIMITS.year, `${name} yıl`),
      battery_check: Boolean(x.battery_check),
      ask_soh: Boolean(x.ask_soh),
      enabled: Boolean(x.enabled),
    };
    if (q.max_price && q.min_price > q.max_price) throw new Error(`${name}: en düşük fiyat tavandan büyük.`);
    if (q.min_year && q.min_year < 1990) throw new Error(`${name}: yıl ${q.min_year} olamaz.`);
    return q;
  });
  return { settings, searches };
}

const LABELS = {
  query: "arama metni", min_price: "en düşük fiyat", max_price: "fiyat tavanı", max_km: "km sınırı",
  min_year: "en eski yıl", battery_check: "batarya kontrolü", ask_soh: "SOH sorusu", enabled: "durum",
  max_dist_private_km: "özel satıcı mesafesi", max_dist_dealer_km: "galeri mesafesi",
  min_price_global: "en düşük ilan fiyatı", extra_reject_terms: "ek eleme kelimeleri",
};

function show(key, v) {
  if (key === "enabled") return v ? "açık" : "kapalı";
  if (typeof v === "boolean") return v ? "evet" : "hayır";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "—";
  if (v === 0 && key !== "min_price") return "sınırsız";
  if (/price/.test(key)) return `${v.toLocaleString("de-DE")} €`;
  if (key === "max_km") return `${v.toLocaleString("de-DE")} km`;
  if (/dist/.test(key)) return `${v} km`;
  return String(v);
}

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export function diffConfig(a, b) {
  const out = [];
  for (const k of Object.keys(b.settings)) {
    const label = LABELS[k === "min_price" ? "min_price_global" : k] || k;
    if (JSON.stringify(a.settings[k]) !== JSON.stringify(b.settings[k])) {
      out.push(`⚙️ ${label}: ${esc(show(k, a.settings[k]))} → <b>${esc(show(k, b.settings[k]))}</b>`);
    }
  }
  const byName = (list) => new Map(list.map((s) => [s.name.toLowerCase(), s]));
  const A = byName(a.searches), B = byName(b.searches);
  for (const [n, s] of B) {
    const old = A.get(n);
    if (!old) {
      out.push(`➕ <b>${esc(s.name)}</b> eklendi (${esc(describe(s))})`);
      continue;
    }
    for (const k of Object.keys(s)) {
      if (k === "name" || JSON.stringify(old[k]) === JSON.stringify(s[k])) continue;
      out.push(`✏️ ${esc(s.name)} — ${LABELS[k] || k}: ${esc(show(k, old[k]))} → <b>${esc(show(k, s[k]))}</b>`);
    }
  }
  for (const [n, s] of A) if (!B.has(n)) out.push(`➖ <b>${esc(s.name)}</b> silindi`);
  return out;
}

function describe(s) {
  const p = [];
  if (s.min_price && s.max_price) p.push(`${show("min_price", s.min_price)}–${show("max_price", s.max_price)}`);
  else if (s.max_price) p.push(`≤ ${show("max_price", s.max_price)}`);
  else if (s.min_price) p.push(`≥ ${show("min_price", s.min_price)}`);
  if (s.max_km) p.push(`≤ ${show("max_km", s.max_km)}`);
  if (s.min_year) p.push(`${s.min_year}+`);
  if (s.battery_check) p.push("batarya kontrolü");
  if (!s.enabled) p.push("kapalı");
  return p.join(", ") || "sınırsız";
}

export function formatConfig(c) {
  const st = c.settings;
  const lines = ["📋 <b>Aramalar</b>"];
  for (const s of c.searches) lines.push(`${s.enabled ? "🟢" : "⚪️"} <b>${esc(s.name)}</b> — ${esc(describe(s))}`);
  lines.push(
    "",
    `📍 Özel satıcı ≤ ${esc(show("max_dist_private_km", st.max_dist_private_km))}, galeri ${esc(show("max_dist_dealer_km", st.max_dist_dealer_km))}`,
    `💶 ${esc(show("min_price", st.min_price))} altı yok sayılıyor`,
  );
  if (st.extra_reject_terms.length) lines.push(`🚫 Ek eleme: ${esc(st.extra_reject_terms.join(", "))}`);
  return lines.join("\n");
}
