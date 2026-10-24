import json
import os
import re

# Aramalar ve ayarlar searches.json'da (Telegram botu orayı günceller, elle de düzenlenebilir).
# Değerlerde 0 = "sınır yok".
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "searches.json"), encoding="utf-8") as _f:
    _data = json.load(_f)
_settings = _data["settings"]

# --- Konum / mesafe kuralları -------------------------------------------------
HOME_PLZ = "40789"            # Monheim am Rhein
MAX_DIST_PRIVATE_KM = _settings["max_dist_private_km"] or None  # Privat satıcı: en fazla bu kadar uzak
MAX_DIST_DEALER_KM = _settings["max_dist_dealer_km"] or None    # Händler: None = sınırsız

MIN_PRICE = _settings["min_price"]  # Parça/scam ilanlarını ele
MAX_DETAIL_FETCH_PER_RUN = 15 # Bir koşuda en fazla kaç detay sayfası çekilsin

# --- Satıcıya hazır mesaj ("📋 Mesajı kopyala" butonu) -----------------------
# Toplam 256 karakteri geçemez (Telegram sınırı); geçerse SOH sorusu düşer.
MSG_GREETING = "Hallo, ist das Auto noch verfügbar?"
Q_BATTERY_OWNED = "Ist die Batterie gekauft (keine Batteriemiete)? Gibt es dafür einen Nachweis?"
Q_SOH = "Wie ist der aktuelle SOH der Batterie?"
MSG_CLOSING = "Viele Grüße"

# --- Aramalar -------------------------------------------------------------------
# "battery_check": metinde bunlardan biri yoksa "satın alınmış batarya ibaresi yok" UYARISI düşer
BATTERY_OWNED_TERMS = [
    "kaufakku", "batterie inklusive", "inkl. batterie", "inklusive batterie",
    "batterie gekauft", "gekaufte batterie", "eigene batterie", "batterie im kaufpreis",
    "eigentumsbatterie", "mit batterie verkauft",
]

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "é": "e", "ë": "e", "è": "e"})


def search_url(s: dict) -> str:
    """https://www.kleinanzeigen.de/s-autos/preis:MIN:MAX/<arama>/k0c216"""
    slug = re.sub(r"[^a-z0-9]+", "-", s["query"].lower().translate(_UMLAUTS)).strip("-")
    lo, hi = s.get("min_price") or "", s.get("max_price") or ""
    price = f"preis:{lo}:{hi}/" if lo or hi else ""
    return f"https://www.kleinanzeigen.de/s-autos/{price}{slug}/k0c216"


SEARCHES = [
    {
        **s,
        "url": search_url(s),
        "require_any": BATTERY_OWNED_TERMS if s.get("battery_check") else None,
    }
    for s in _data["searches"]
]


# --- Metin filtreleri (ilan başlığı + açıklama, lowercase taranır) ---------------
# Bunlardan biri geçiyorsa ilan ELENİR:
REJECT_TERMS = [
    "unfall", "beschädigt", "totalschaden", "motorschaden", "getriebeschaden",
    "defekt", "nicht fahrbereit", "bastler", "ersatzteil", "schlachtfest",
    "export", "us-import", "us import", "us papiere",
] + [t.lower() for t in _settings.get("extra_reject_terms", [])]
# "unfall" taramasından ÖNCE metinden silinecek masum ifadeler:
REJECT_NEGATIONS = [
    "unfallfrei", "unfall frei", "kein unfall", "keine unfälle",
    "ohne unfall", "unfallfreies",
    # Detay tablosundaki "Fahrzeugzustand: Unbeschädigtes Fahrzeug" "beschädigt"e takılmasın
    "unbeschädigt", "nicht beschädigt", "nicht defekt", "kein export", "nicht für export",
]

# Kiralık batarya tespiti (Leaf/Zoe için kritik):
RENT_TERMS = [
    "batteriemiete", "mietbatterie", "akkumiete", "batterie gemietet",
    "leasing-batterie", "hv-leasing", "batterie leasing", "akku miete",
]
# Kira terimi geçse bile bunlardan biri varsa "kapatılmış" olabilir → UYARI ile bildir:
RENT_RESOLVED_TERMS = [
    "abgelöst", "abgelöste", "abloese", "ablösung", "batterie gekauft",
    "kaufakku", "batterie inklusive", "herausgekauft",
    "eigentumsbatterie", "inkl. batterie", "inklusive batterie", "mit batterie verkauft",
    "ohne batteriemiete", "ohne eine batteriemiete", "keine batteriemiete",
    "ohne mietbatterie", "keine mietbatterie", "für eine mietbatterie",
]
