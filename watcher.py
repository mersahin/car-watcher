#!/usr/bin/env python3
"""Kleinanzeigen araba takipçisi — yeni ilan ve fiyat düşüşlerini Telegram'a bildirir.

Çalışma mantığı:
  1. config.SEARCHES içindeki arama sayfalarını çeker (sadece 1. sayfa).
  2. Daha önce görülmemiş ilanların detay sayfasını açar.
  3. Metin filtreleri (kaza, defekt, kiralık batarya...) + mesafe kuralını uygular.
  4. Geçenleri Telegram'a yollar; fiyat düşüşlerini de bildirir.
  5. state.json'a yazar (GitHub Actions workflow'u bunu repoya commit eder).
"""

import html
import json
import math
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

import config

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
PRICE_DROP_THRESHOLD = 100  # € — bu kadar düşerse tekrar bildir

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
}

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")


# --------------------------------------------------------------------------- util
def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"ads": {}, "blocked_runs": 0}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


def tg_send(text: str, buttons: list[list[dict]] | None = None) -> None:
    if not TG_TOKEN or not TG_CHAT:
        log("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID eksik — mesaj atlanıyor:")
        log(text)
        if buttons:
            log(f"butonlar: {json.dumps(buttons, ensure_ascii=False)}")
        return
    payload = {"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
               "link_preview_options": {"is_disabled": True}}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    r = requests.post(url, json=payload, timeout=20)
    if r.status_code != 200 and buttons:
        # Buton reddedilirse bildirimi kaybetme: butonsuz tekrar dene.
        log(f"Telegram butonları reddetti {r.status_code}: {r.text[:200]} — butonsuz deneniyor")
        payload.pop("reply_markup")
        r = requests.post(url, json=payload, timeout=20)
    if r.status_code != 200:
        log(f"Telegram hatası {r.status_code}: {r.text[:200]}")


def seller_message(search: dict, notes: list[str]) -> str:
    """Satıcıya yapıştırılacak hazır Almanca mesaj (Telegram copy_text sınırı: 256 karakter)."""
    parts = [config.MSG_GREETING]
    battery_unclear = search.get("require_any") or any("atarya" in n for n in notes)
    if battery_unclear:
        parts.append(config.Q_BATTERY_OWNED)
    if search.get("ask_soh"):
        parts.append(config.Q_SOH)
    parts.append(config.MSG_CLOSING)
    msg = " ".join(parts)
    while len(msg) > 256 and len(parts) > 2:  # sığmazsa sondan bir önceki soruyu at
        parts.pop(-2)
        msg = " ".join(parts)
    return msg[:256]


def ad_buttons(url: str, seller_msg: str | None = None) -> list[list[dict]]:
    row = []
    if seller_msg:
        row.append({"text": "📋 Mesajı kopyala", "copy_text": {"text": seller_msg}})
    row.append({"text": "🔗 İlanı aç", "url": url})
    return [row]


class Blocked(Exception):
    pass


def fetch(session: requests.Session, url: str) -> str:
    r = session.get(url, headers=HEADERS, timeout=30)
    if r.status_code in (403, 429) or "captcha" in r.text[:3000].lower():
        raise Blocked(f"{r.status_code} @ {url}")
    r.raise_for_status()
    return r.text


# ---------------------------------------------------------------------- geocoding
_geo = None


def dist_from_home(plz: str) -> float | None:
    """PLZ -> Monheim'dan kuş uçuşu km. Bilinmiyorsa None."""
    global _geo
    if _geo is None:
        import pgeocode

        _geo = pgeocode.Nominatim("de")
    try:
        home = _geo.query_postal_code(config.HOME_PLZ)
        there = _geo.query_postal_code(plz)
        if math.isnan(there.latitude) or math.isnan(home.latitude):
            return None
        lat1, lon1 = math.radians(home.latitude), math.radians(home.longitude)
        lat2, lon2 = math.radians(there.latitude), math.radians(there.longitude)
        a = (
            math.sin((lat2 - lat1) / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        )
        return 6371 * 2 * math.asin(math.sqrt(a))
    except Exception:
        return None


# ------------------------------------------------------------------------ parsing
def parse_search_page(html_text: str) -> list[dict]:
    # Kleinanzeigen 2026-10'da sonuç kartlarını Tailwind'e taşıdı: "aditem" class'ı yok,
    # bu yüzden class adlarına değil data-* niteliklerine ve metin kalıplarına bakıyoruz.
    soup = BeautifulSoup(html_text, "html.parser")
    ads = []
    seen_ids = set()
    for art in soup.select("article[data-adid]"):
        ad_id = art.get("data-adid", "").strip()
        if ad_id in seen_ids:  # öne çıkarılan ilanlar listede iki kez geçiyor
            continue
        seen_ids.add(ad_id)
        href = art.get("data-href") or ""
        links = [a for a in art.select("a[href]") if a.get_text(strip=True)]
        if not href and links:
            href = links[0].get("href", "")
        # Aynı href'e giden iki link var: fotoğraf (metni foto sayısı) ve başlık — uzun olanı al.
        same = [a.get_text(" ", strip=True) for a in links if a.get("href") == href]
        title = max(same, key=len) if same else "?"

        price, loc, tags = None, "", []
        for el in art.select("p, span, div"):
            if el.find(["p", "span", "div"]):
                continue
            t = el.get_text(" ", strip=True)
            if price is None and re.fullmatch(r"[\d.]+\s*€(\s*VB)?", t):
                price = parse_price(t)
            elif not loc and re.match(r"\d{5}\s", t):
                loc = t
            elif re.fullmatch(r"[\d.]+\s*km|EZ\s*\d{1,2}/\d{4}", t):
                tags.append(t)
        if ad_id and href:
            ads.append(
                {
                    "id": ad_id,
                    "url": "https://www.kleinanzeigen.de" + href,
                    "title": title,
                    "price": price,
                    "location": loc,
                    "tags": tags,
                }
            )
    return ads


def within_limits(search: dict, ad: dict) -> bool:
    """Arama sayfasındaki etiketlerden km / ilk tescil yılı sınırı (0 = sınır yok).
    Etiket yoksa ilanı elemeyiz — detayda yine filtreler çalışır."""
    for tag in ad.get("tags", []):
        km = re.fullmatch(r"([\d.]+)\s*km", tag)
        if km and search.get("max_km") and int(km.group(1).replace(".", "")) > search["max_km"]:
            return False
        ez = re.fullmatch(r"EZ\s*\d{1,2}/(\d{4})", tag)
        if ez and search.get("min_year") and int(ez.group(1)) < search["min_year"]:
            return False
    return True


def parse_price(text: str) -> int | None:
    m = re.search(r"([\d.]+)\s*€", text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(".", ""))
    except ValueError:
        return None


def parse_detail(html_text: str) -> dict:
    soup = BeautifulSoup(html_text, "html.parser")
    desc_el = soup.select_one("#viewad-description-text")
    desc = desc_el.get_text(" ", strip=True) if desc_el else ""
    # Donanım/detay listeleri de metne dahil (örn. "Fahrzeug mit HV-Leasing-Batterie")
    details = " ".join(
        li.get_text(" ", strip=True) for li in soup.select("#viewad-details li, .addetailslist--detail")
    )
    checktags = " ".join(t.get_text(" ", strip=True) for t in soup.select(".checktag"))
    page_text = soup.get_text(" ", strip=True)

    if "Gewerblicher Nutzer" in page_text:
        seller = "Händler"
    elif "Privater Nutzer" in page_text:
        seller = "Privat"
    else:
        seller = "?"

    plz = None
    loc_el = soup.select_one("#viewad-locality")
    loc_text = loc_el.get_text(" ", strip=True) if loc_el else ""
    m = re.search(r"\b(\d{5})\b", loc_text) or re.search(r"\b(\d{5})\b\s+[A-ZÄÖÜ]", page_text)
    if m:
        plz = m.group(1)

    return {
        "text": f"{desc} {details} {checktags}".lower(),
        "seller": seller,
        "plz": plz,
        "locality": loc_text,
    }


# ----------------------------------------------------------------------- analysis
def evaluate(search: dict, ad: dict, detail: dict) -> tuple[str, list[str]]:
    """returns (verdict, notes); verdict: 'notify' | 'reject'."""
    text = f"{ad['title'].lower()} {detail['text']}"
    for neg in config.REJECT_NEGATIONS:
        text = text.replace(neg, " ")

    notes: list[str] = []

    for term in config.REJECT_TERMS:
        if term in text:
            return "reject", [f"kara liste: '{term}'"]

    rent_hit = [t for t in config.RENT_TERMS if t in text]
    if rent_hit:
        if any(t in text for t in config.RENT_RESOLVED_TERMS):
            notes.append("⚠️ Batarya kirası geçiyor ama 'abgelöst/gekauft' da var — belgeyi iste!")
        else:
            return "reject", [f"kiralık batarya: '{rent_hit[0]}'"]

    req = search.get("require_any")
    if req and not any(t in text for t in req):
        notes.append("⚠️ Satın alınmış batarya ibaresi YOK — Batteriemiete olabilir, mutlaka sor.")

    # Mesafe kuralı
    dist = dist_from_home(detail["plz"]) if detail["plz"] else None
    ad["dist_km"] = round(dist) if dist is not None else None
    if dist is None:
        notes.append("⚠️ Mesafe hesaplanamadı (PLZ bulunamadı).")
    elif detail["seller"] != "Händler":
        if config.MAX_DIST_PRIVATE_KM and dist > config.MAX_DIST_PRIVATE_KM:
            return "reject", [f"privat + {round(dist)} km (> {config.MAX_DIST_PRIVATE_KM} km)"]
    elif config.MAX_DIST_DEALER_KM and dist > config.MAX_DIST_DEALER_KM:
        return "reject", [f"händler + {round(dist)} km (> {config.MAX_DIST_DEALER_KM} km)"]

    if not detail["text"]:
        notes.append("⚠️ İlan metni okunamadı — filtreler uygulanamadı.")
    return "notify", notes


def format_message(search: dict, ad: dict, detail: dict, notes: list[str], seller_msg: str = "") -> str:
    title = html.escape(ad["title"])
    price = f"{ad['price']:,} €".replace(",", ".") if ad["price"] else "? €"
    tags = " | ".join(ad.get("tags", [])[:3])
    dist = f"~{ad['dist_km']} km" if ad.get("dist_km") is not None else "mesafe ?"
    loc = html.escape(detail.get("locality") or ad.get("location", ""))
    lines = [
        f"🚗 <b>{html.escape(search['name'])}</b> — {title}",
        f"💶 <b>{price}</b>" + (f"  |  {tags}" if tags else ""),
        f"📍 {loc} — {dist}  ({detail['seller']})",
    ]
    lines += notes if notes else ["✅ Metinde kırmızı bayrak yok."]
    if seller_msg:
        # <code> içindeki metin Telegram'da dokununca da kopyalanır (eski istemciler için yedek).
        lines.append(f"\n✉️ <code>{html.escape(seller_msg)}</code>")
    lines.append(ad["url"])
    return "\n".join(lines)


# --------------------------------------------------------------------------- main
def send_test() -> int:
    """Gerçek bir ilanın sahte bildirimini yollar: butonları denemek için (state'e dokunmaz)."""
    search = next(s for s in config.SEARCHES if s.get("require_any"))
    ad = {"title": "TEST — örnek ilan", "price": 6500, "tags": ["80.000 km", "EZ 03/2017"],
          "dist_km": 25, "location": "", "url": "https://www.kleinanzeigen.de/s-autos/c216"}
    detail = {"locality": "40789 Monheim am Rhein", "seller": "Privat"}
    notes = ["⚠️ Satın alınmış batarya ibaresi YOK — Batteriemiete olabilir, mutlaka sor."]
    smsg = seller_message(search, notes)
    tg_send(format_message(search, ad, detail, notes, smsg), ad_buttons(ad["url"], smsg))
    log("Test bildirimi gönderildi.")
    return 0


def main() -> int:
    if "--test" in sys.argv:
        return send_test()
    state = load_state()
    seen = state["ads"]
    session = requests.Session()
    detail_budget = config.MAX_DETAIL_FETCH_PER_RUN
    new_count = 0
    empty_searches = 0
    active = [s for s in config.SEARCHES if s.get("enabled", True)]

    try:
        for search in config.SEARCHES:
            if not search.get("enabled", True):
                continue
            log(f"Arama: {search['name']}")
            ads = parse_search_page(fetch(session, search["url"]))
            log(f"  {len(ads)} ilan bulundu")
            empty_searches += 0 if ads else 1

            for ad in ads:
                if ad["price"] is not None and ad["price"] < config.MIN_PRICE:
                    continue
                if not within_limits(search, ad):
                    continue
                prev = seen.get(ad["id"])

                # Fiyat düşüşü bildirimi (daha önce bildirilenler için)
                if prev:
                    if (
                        prev.get("notified")
                        and ad["price"]
                        and prev.get("price")
                        and prev["price"] - ad["price"] >= PRICE_DROP_THRESHOLD
                    ):
                        tg_send(
                            f"📉 <b>Fiyat düştü:</b> {html.escape(ad['title'])}\n"
                            f"{prev['price']:,} € → <b>{ad['price']:,} €</b>\n{ad['url']}".replace(",", "."),
                            ad_buttons(ad["url"]),
                        )
                        prev["price"] = ad["price"]
                    continue

                if detail_budget <= 0:
                    continue
                detail_budget -= 1
                time.sleep(random.uniform(3, 6))
                try:
                    detail = parse_detail(fetch(session, ad["url"]))
                except Blocked:
                    raise
                except Exception as e:
                    detail = {"text": "", "seller": "?", "plz": None, "locality": ""}
                    log(f"  detay okunamadı ({ad['id']}): {e}")

                verdict, notes = evaluate(search, ad, detail)
                seen[ad["id"]] = {
                    "price": ad["price"],
                    "title": ad["title"],
                    "notified": verdict == "notify",
                    "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "reason": "; ".join(notes) if verdict == "reject" else "",
                }
                if verdict == "notify":
                    smsg = seller_message(search, notes)
                    tg_send(format_message(search, ad, detail, notes, smsg), ad_buttons(ad["url"], smsg))
                    new_count += 1
                    log(f"  ✓ bildirildi: {ad['title'][:60]}")
                else:
                    log(f"  ✗ elendi ({notes[0]}): {ad['title'][:60]}")

            time.sleep(random.uniform(3, 6))

        state["blocked_runs"] = 0
        # Hiçbir aramada ilan yoksa büyük ihtimalle sayfa yapısı değişti, parser kırıldı.
        if active and empty_searches == len(active):
            state["empty_runs"] = state.get("empty_runs", 0) + 1
            if state["empty_runs"] == 3:
                tg_send("⚠️ Üst üste 3 koşudur hiçbir aramada ilan bulunamadı — "
                        "Kleinanzeigen sayfa yapısı değişmiş olabilir, parser'a bak.")
        else:
            state["empty_runs"] = 0
    except Blocked as e:
        state["blocked_runs"] = state.get("blocked_runs", 0) + 1
        log(f"ENGELLENDİ: {e} (üst üste {state['blocked_runs']}. koşu)")
        if state["blocked_runs"] == 3:
            tg_send(
                "🛑 Kleinanzeigen GitHub Actions IP'sini üst üste 3 koşudur engelliyor. "
                "Scripti ev/sunucu IP'sinden (cron) çalıştırmayı düşün."
            )

    save_state(state)
    log(f"Bitti — {new_count} yeni bildirim.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
