# --- Konum / mesafe kuralları -------------------------------------------------
HOME_PLZ = "40789"            # Monheim am Rhein
MAX_DIST_PRIVATE_KM = 100     # Privat satıcı: en fazla bu kadar uzak
MAX_DIST_DEALER_KM = None     # Händler: None = sınırsız (örn. 300 yazarsan 300 km)

MIN_PRICE = 1000              # Parça/scam ilanlarını ele
MAX_DETAIL_FETCH_PER_RUN = 15 # Bir koşuda en fazla kaç detay sayfası çekilsin

# --- Satıcıya hazır mesaj ("📋 Mesajı kopyala" butonu) -----------------------
# Toplam 256 karakteri geçemez (Telegram sınırı); geçerse SOH sorusu düşer.
MSG_GREETING = "Hallo, ist das Auto noch verfügbar?"
Q_BATTERY_OWNED = "Ist die Batterie gekauft (keine Batteriemiete)? Gibt es dafür einen Nachweis?"
Q_SOH = "Wie ist der aktuelle SOH der Batterie?"
MSG_CLOSING = "Viele Grüße"

# --- Aramalar -------------------------------------------------------------------
# URL formatı: https://www.kleinanzeigen.de/s-autos/preis:MIN:MAX/<arama>/k0c216
SEARCHES = [
    {
        "name": "Nissan Leaf",
        "ask_soh": True,
        "url": "https://www.kleinanzeigen.de/s-autos/preis::6500/nissan-leaf/k0c216",
        "enabled": True,
    },
    {
        "name": "Renault Zoe",
        "ask_soh": True,
        "url": "https://www.kleinanzeigen.de/s-autos/preis::7000/renault-zoe/k0c216",
        # Zoe'de satın alınmış batarya şart — metinde bunlardan biri yoksa UYARI düşer
        "require_any": [
            "kaufakku", "batterie inklusive", "inkl. batterie", "inklusive batterie",
            "batterie gekauft", "gekaufte batterie", "eigene batterie", "batterie im kaufpreis",
            "eigentumsbatterie", "mit batterie verkauft",
        ],
        "enabled": True,
    },
    {
        "name": "Peugeot iOn",
        "ask_soh": True,
        "url": "https://www.kleinanzeigen.de/s-autos/preis::5000/peugeot-ion/k0c216",
        "enabled": True,
    },
    {
        "name": "Citroën C-Zero",
        "ask_soh": True,
        "url": "https://www.kleinanzeigen.de/s-autos/preis::5000/citroen-c-zero/k0c216",
        "enabled": True,
    },
    {
        "name": "Mitsubishi i-MiEV",
        "ask_soh": True,
        "url": "https://www.kleinanzeigen.de/s-autos/preis::5000/mitsubishi-i-miev/k0c216",
        "enabled": True,
    },
    {
        "name": "VW eco up CNG",
        "url": "https://www.kleinanzeigen.de/s-autos/preis::6500/vw-eco-up/k0c216",
        "enabled": False,  # Monheim çevresinde CNG istasyonu netleşince aç
    },
]

# --- Metin filtreleri (ilan başlığı + açıklama, lowercase taranır) ---------------
# Bunlardan biri geçiyorsa ilan ELENİR:
REJECT_TERMS = [
    "unfall", "beschädigt", "totalschaden", "motorschaden", "getriebeschaden",
    "defekt", "nicht fahrbereit", "bastler", "ersatzteil", "schlachtfest",
    "export", "us-import", "us import", "us papiere",
]
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
