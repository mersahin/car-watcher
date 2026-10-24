# car-watcher

Kleinanzeigen'de ucuz EV ilanlarını (Leaf, Zoe Kaufakku, iOn/C-Zero/i-MiEV) izler,
filtreden geçenleri Telegram'a bildirir. GitHub Actions üzerinde saatte bir koşar (private repo dakika kotası için).

## Kurulum (~10 dk)

1. **Telegram bot:** Telegram'da `@BotFather` → `/newbot` → token'ı al.
2. **Chat ID:** Bota herhangi bir mesaj at, sonra tarayıcıda
   `https://api.telegram.org/bot<TOKEN>/getUpdates` → `message.chat.id` değeri.
3. **Repo:** Bu klasörü **private** bir GitHub reposuna pushla
   (`.github/workflows/watch.yml` dahil).
4. **Secrets:** Repo → Settings → Secrets and variables → Actions:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. **Test:** Actions sekmesi → `car-watcher` → *Run workflow*. İlk koşuda mevcut
   ilanların hepsi "yeni" sayılır — bir kerelik toplu bildirim normaldir.

## Davranış

- Privat satıcı: **≤100 km** (Monheim 40789'dan kuş uçuşu), Händler: sınırsız — `config.py`.
- Kara liste: Unfall/defekt/Export/US-Import/Bastler + **Batteriemiete** (ablöse ibaresi yoksa).
- Zoe'de "Kaufakku / Batterie inklusive" geçmiyorsa uyarıyla bildirir.
- Fiyat ≥100 € düşerse tekrar bildirir.
- Her bildirimde iki buton: **📋 Mesajı kopyala** (satıcıya hazır Almanca soru — müsait mi,
  batarya satın alınmış mı, SOH kaç — panoya kopyalanır) ve **🔗 İlanı aç**. Kleinanzeigen
  mesaj kutusunu hazır metinle açan bir link sunmadığı için akış: kopyala → aç → Nachricht → yapıştır.
  Metinler `config.py` → `MSG_GREETING`, `Q_BATTERY_OWNED`, `Q_SOH`, `MSG_CLOSING`.
- Butonları denemek için: Actions → car-watcher → Run workflow → **test** kutusunu işaretle.
- Görülen ilanlar `state.json`'da tutulur ve workflow tarafından repoya commitlenir.

## Bilinen riskler

- **IP engeli:** Kleinanzeigen datacenter IP'lerini engelleyebilir. Script 403/429/captcha
  görürse koşuyu sessizce bitirir; üst üste 3 engelde Telegram'dan haber verir.
  Çözüm: aynı scripti evde/sunucuda cron ile çalıştır (hiçbir değişiklik gerekmez,
  sadece iki env değişkeni + `python watcher.py`).
- GitHub, 60 gün commit olmayan repolarda scheduled workflow'u durdurur —
  state commitleri bunu zaten engelliyor.
- Yedek olarak Kleinanzeigen'in kendi **Suchauftrag**'ını da kur (app push, anında).

## Ayar

Aramalar, fiyat tavanları, mesafe limitleri ve kelime listeleri: `config.py`.
`VW eco up CNG` araması hazır ama kapalı (`enabled: False`).
