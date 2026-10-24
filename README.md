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

Aramalar, fiyat aralıkları, km/yıl sınırları, mesafe limitleri: `searches.json` (0 = sınırsız).
Bunları Telegram botuna yazarak ya da sesli mesajla da değiştirebilirsin (aşağıda).
Kelime listeleri ve satıcıya gidecek hazır mesaj: `config.py`.

## Telegram komut botu (isteğe bağlı)

Bota "Zoe'nin tavanını 8000 yap", "Hyundai Ioniq ekle, 120 bin km altı" gibi yazılı ya da
**sesli** mesaj atarsın; Gemini isteği `searches.json`'a çevirir, değişiklik repoya commitlenir,
bot ne değiştiğini yazar ve aramayı hemen yeniden başlatır. `/liste`, `/calistir`, `/geri` komutları var.

Akış: Telegram → Cloudflare Worker (`worker/`) → Gemini → GitHub API (`searches.json` commit +
`watch.yml` tetikleme). Worker yalnızca `TELEGRAM_CHAT_ID`'den gelen mesajları işler ve Telegram'ın
webhook sırrını doğrular; Gemini'nin çıktısı yazılmadan önce `worker/src/config.js`'te doğrulanır.

Kurulum: aşağıdaki secret'ları ekle, sonra Actions → **deploy-bot** → Run workflow.

| Secret | Nereden |
|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| `CLOUDFLARE_ACCOUNT_ID` | dash.cloudflare.com → sağ kenarda *Account ID* |
| `CLOUDFLARE_API_TOKEN` | My Profile → API Tokens → *Edit Cloudflare Workers* şablonu |
| `BOT_GITHUB_TOKEN` | GitHub → Settings → Developer settings → Fine-grained token; yalnız bu repo, *Contents: Read and write* + *Actions: Read and write* |

Not: webhook kurulunca bot `getUpdates`'e cevap vermez (Telegram'ın kuralı); chat ID gerekirse
`@userinfobot` kullan.
