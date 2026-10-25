// Telegram webhook → Gemini → searches.json (GitHub) → watch.yml'ı hemen tetikle.
// Yalnızca ALLOWED_CHAT_ID'den gelen mesajlar işlenir; geri kalanı sessizce yok sayılır.
import { validateConfig, diffConfig, formatConfig } from "./config.js";

const CONFIG_PATH = "searches.json";

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("car-watcher-bot");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    const update = await request.json();
    const msg = update.message;
    if (!msg || String(msg.chat.id) !== String(env.ALLOWED_CHAT_ID)) return new Response("ok");

    // Telegram'a hemen 200 dön (yoksa aynı update'i tekrar yollar); işi arka planda yap.
    ctx.waitUntil(
      handle(msg, env).catch((e) => tg(env, `❌ Hata: ${escapeHtml(e.message).slice(0, 500)}`)),
    );
    return new Response("ok");
  },
};

async function handle(msg, env) {
  const text = (msg.text || "").trim();
  const cmd = text.split(/\s+/)[0].toLowerCase().replace(/@.*/, "");

  if (cmd === "/start" || cmd === "/yardim" || cmd === "/help") return tg(env, HELP);
  if (cmd === "/liste") {
    const { config } = await readConfig(env);
    return tg(env, formatConfig(config));
  }
  if (cmd === "/calistir") {
    await dispatchWatch(env);
    return tg(env, "▶️ Arama başlatıldı, yeni ilan varsa birkaç dakika içinde gelir.");
  }
  if (cmd === "/geri") return undoLast(env);

  if (!text && !msg.voice && !msg.audio) return tg(env, "Yazı ya da sesli mesaj gönder. /yardim");

  await tgAction(env, "typing");
  const { config, sha } = await readConfig(env);
  const audio = msg.voice || msg.audio ? await downloadAudio(env, msg.voice || msg.audio) : null;
  const ai = await askGemini(env, config, text, audio);

  const heard = audio && ai.transcript ? `🎙 <i>${escapeHtml(ai.transcript)}</i>\n\n` : "";
  if (!ai.changed) return tg(env, heard + escapeHtml(ai.reply || "Bir değişiklik anlamadım."));

  const next = validateConfig(ai.config); // geçersizse throw → kullanıcıya hata
  const diff = diffConfig(config, next);
  if (!diff.length) return tg(env, heard + "Aramalar zaten böyle, değişiklik yok.");

  const summary = diff.join("\n");
  await writeConfig(env, next, sha, `bot: ${stripTags(summary).slice(0, 200)}`);
  // Kayıt yapıldı; aramayı hemen başlatamamak onu geçersiz kılmaz, saatlik koşu zaten alır.
  let started = "▶️ Yeni kriterlerle arama başlatıldı.";
  try {
    await dispatchWatch(env);
  } catch (e) {
    started = `⚠️ Kaydedildi ama arama hemen başlatılamadı, bir sonraki saatlik koşuda uygulanacak.\n<i>${escapeHtml(e.message).slice(0, 200)}</i>`;
  }
  return tg(env, `${heard}✅ Güncellendi:\n${summary}\n\n${started} Geri almak için /geri`);
}

const HELP = `🚗 <b>car-watcher botu</b>

Aramaları yazarak ya da sesli mesajla değiştirebilirsin, örneğin:
• "Zoe'nin tavanını 8000 yap"
• "Hyundai Ioniq ekle, 9000 euroya kadar, 120 bin km'den az"
• "Leaf'i kapat"
• "Özel satıcılar için mesafeyi 150 km yap"
• "Export ve Motorschaden geçenleri ele"

Komutlar:
/liste — mevcut aramalar
/calistir — şimdi ara
/geri — son değişikliği geri al`;

// ------------------------------------------------------------------ Gemini
const SYSTEM_PROMPT = `Sen bir araba ilanı takip botunun ayar asistanısın. Kullanıcı Türkçe (bazen Almanca) yazar ya da konuşur.
Görevin: kullanıcının isteğini mevcut JSON ayarlarına uygulamak ve TAM yeni ayarı döndürmek.

Ayar alanları:
- settings.max_dist_private_km: özel satıcı için en fazla mesafe (km, Monheim 40789'dan). 0 = sınırsız.
- settings.max_dist_dealer_km: galeri (Händler) için en fazla mesafe. 0 = sınırsız.
- settings.min_price: bunun altındaki ilanlar yok sayılır (parça/dolandırıcılık filtresi).
- settings.extra_reject_terms: ilan metninde geçerse ilanı eleyen ek Almanca kelimeler (küçük harf).
- searches[]: her biri bir Kleinanzeigen araması.
  - name: görünen ad (ör. "Hyundai Ioniq").
  - query: Kleinanzeigen arama metni, marka + model, küçük harf (ör. "hyundai ioniq").
  - min_price / max_price: euro, 0 = sınırsız.
  - max_km: en fazla kilometre, 0 = sınırsız. min_year: en eski ilk tescil yılı, 0 = sınırsız.
  - battery_check: true ise "batarya satın alınmış mı" kontrolü ve uyarısı (kiralık batarya riski olan Renault Zoe/Kangoo/Twizy için true).
  - ask_soh: elektrikli araçlar için true (satıcıya batarya SOH'u sorulur).
  - enabled: aramayı aç/kapat. "Kapat/durdur" dendiğinde silme, enabled=false yap. Sadece açıkça "sil" denirse listeden çıkar.

Kurallar:
- Sadece istenen şeyi değiştir, gerisini AYNEN koru.
- "8 bin", "sekiz bin", "8k" = 8000. "120 bin km" = 120000.
- Yaş → min_year: "en fazla 5 yaşında / 5 yaşından genç" = bugünün yılı − 5 (2026'da 2021). Yılı mutlaka verilen bugünün tarihinden hesapla.
- Yeni araç eklerken mantıklı varsayılanlar kullan (min_price 0, max_km 0, min_year 0, enabled true).
- İstek bir ayar değişikliği değilse (soru, selam, belirsiz) changed=false yap, config'i olduğu gibi döndür ve reply'da kısa Türkçe cevap ver ya da netleştirici soru sor.
- reply: yaptığın değişikliğin tek cümlelik Türkçe özeti.
- Sesli mesaj geldiyse transcript alanına duyduğunu yaz, yoksa boş bırak.`;

const SEARCH_SCHEMA = {
  type: "OBJECT",
  properties: {
    name: { type: "STRING" },
    query: { type: "STRING" },
    min_price: { type: "INTEGER" },
    max_price: { type: "INTEGER" },
    max_km: { type: "INTEGER" },
    min_year: { type: "INTEGER" },
    battery_check: { type: "BOOLEAN" },
    ask_soh: { type: "BOOLEAN" },
    enabled: { type: "BOOLEAN" },
  },
  required: ["name", "query", "min_price", "max_price", "max_km", "min_year", "battery_check", "ask_soh", "enabled"],
};

const RESPONSE_SCHEMA = {
  type: "OBJECT",
  properties: {
    transcript: { type: "STRING" },
    changed: { type: "BOOLEAN" },
    reply: { type: "STRING" },
    config: {
      type: "OBJECT",
      properties: {
        settings: {
          type: "OBJECT",
          properties: {
            max_dist_private_km: { type: "INTEGER" },
            max_dist_dealer_km: { type: "INTEGER" },
            min_price: { type: "INTEGER" },
            extra_reject_terms: { type: "ARRAY", items: { type: "STRING" } },
          },
          required: ["max_dist_private_km", "max_dist_dealer_km", "min_price", "extra_reject_terms"],
        },
        searches: { type: "ARRAY", items: SEARCH_SCHEMA },
      },
      required: ["settings", "searches"],
    },
  },
  required: ["transcript", "changed", "reply", "config"],
};

async function askGemini(env, config, text, audio) {
  const today = new Date().toISOString().slice(0, 10);
  const parts = [{ text: `Bugünün tarihi: ${today}\nMevcut ayarlar:\n${JSON.stringify(config, null, 1)}` }];
  if (text) parts.push({ text: `Kullanıcının mesajı: ${text}` });
  if (audio) {
    parts.push({ text: "Kullanıcının sesli mesajı ekte:" });
    parts.push({ inline_data: { mime_type: audio.mime, data: audio.base64 } });
  }
  const r = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${env.GEMINI_MODEL}:generateContent`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-goog-api-key": env.GEMINI_API_KEY },
      body: JSON.stringify({
        system_instruction: { parts: [{ text: SYSTEM_PROMPT }] },
        contents: [{ role: "user", parts }],
        generationConfig: {
          temperature: 0,
          responseMimeType: "application/json",
          responseSchema: RESPONSE_SCHEMA,
        },
      }),
    },
  );
  if (!r.ok) throw new Error(`Gemini ${r.status}: ${(await r.text()).slice(0, 300)}`);
  const data = await r.json();
  const out = data.candidates?.[0]?.content?.parts?.map((p) => p.text || "").join("") || "";
  try {
    return JSON.parse(out);
  } catch {
    throw new Error(`Gemini geçersiz JSON döndü: ${out.slice(0, 200)}`);
  }
}

// ------------------------------------------------------------------ Telegram
async function tg(env, text) {
  await tgApi(env, "sendMessage", {
    chat_id: env.ALLOWED_CHAT_ID,
    text: text.slice(0, 4000),
    parse_mode: "HTML",
    link_preview_options: { is_disabled: true },
  });
}

async function tgAction(env, action) {
  await tgApi(env, "sendChatAction", { chat_id: env.ALLOWED_CHAT_ID, action }).catch(() => {});
}

async function tgApi(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`Telegram ${method} ${r.status}: ${(await r.text()).slice(0, 200)}`);
  return (await r.json()).result;
}

async function downloadAudio(env, file) {
  if (file.file_size > 10 * 1024 * 1024) throw new Error("Ses kaydı çok uzun (10 MB üstü).");
  const info = await tgApi(env, "getFile", { file_id: file.file_id });
  const r = await fetch(`https://api.telegram.org/file/bot${env.TELEGRAM_BOT_TOKEN}/${info.file_path}`);
  if (!r.ok) throw new Error(`Ses dosyası indirilemedi (${r.status})`);
  const bytes = new Uint8Array(await r.arrayBuffer());
  return { mime: file.mime_type || "audio/ogg", base64: toBase64(bytes) };
}

// ------------------------------------------------------------------ GitHub
function gh(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "car-watcher-bot",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
    },
  });
}

async function readConfig(env, ref = env.GITHUB_BRANCH) {
  const r = await gh(env, `/contents/${CONFIG_PATH}?ref=${ref}`);
  if (!r.ok) throw new Error(`GitHub okuma ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const j = await r.json();
  return { config: JSON.parse(fromBase64(j.content)), sha: j.sha };
}

async function writeConfig(env, config, sha, message) {
  const r = await gh(env, `/contents/${CONFIG_PATH}`, {
    method: "PUT",
    body: JSON.stringify({
      message,
      content: toBase64(new TextEncoder().encode(JSON.stringify(config, null, 2) + "\n")),
      sha,
      branch: env.GITHUB_BRANCH,
    }),
  });
  if (!r.ok) throw new Error(`GitHub yazma ${r.status}: ${(await r.text()).slice(0, 200)}`);
}

async function dispatchWatch(env) {
  const r = await gh(env, `/actions/workflows/${env.WATCH_WORKFLOW}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: env.GITHUB_BRANCH }),
  });
  if (!r.ok) throw new Error(`Workflow tetiklenemedi ${r.status}: ${(await r.text()).slice(0, 200)}`);
}

async function undoLast(env) {
  const r = await gh(env, `/commits?path=${CONFIG_PATH}&sha=${env.GITHUB_BRANCH}&per_page=2`);
  if (!r.ok) throw new Error(`GitHub geçmiş ${r.status}`);
  const commits = await r.json();
  if (commits.length < 2) return tg(env, "Geri alınacak bir değişiklik yok.");
  const { config: current, sha } = await readConfig(env);
  const { config: previous } = await readConfig(env, commits[1].sha);
  const diff = diffConfig(current, previous);
  if (!diff.length) return tg(env, "Önceki sürümle aynı, geri alınacak bir şey yok.");
  await writeConfig(env, previous, sha, "bot: son değişiklik geri alındı");
  return tg(env, `↩️ Geri alındı:\n${diff.join("\n")}`);
}

// ------------------------------------------------------------------ util
function toBase64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

function fromBase64(b64) {
  const bin = atob(b64.replace(/\n/g, ""));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}

export function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function stripTags(s) {
  return s.replace(/<[^>]+>/g, "");
}
