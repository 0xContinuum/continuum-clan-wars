# Continuum Clan Wars

Telegram Mini App: 4 saatlik reaktör deposu, 60 saniyelik arcade turu (Node Defender), klan sezonu ve kanal sahipleri için %25 gelir paylaşımı. Reklamlar Adsgram ile, tamamen isteğe bağlı ve ödüllü.

## Klasör yapısı

```
continuum-clan-wars/
├── backend/
│   ├── __init__.py
│   ├── main.py          FastAPI: kimlik, depo, enerji, tur, reklam, klan, ödeme
│   └── database.py      SQLAlchemy (Postgres veya yerelde SQLite)
├── frontend/
│   └── index.html       Tüm arayüz, oyun, efekt ve ses (tek dosya)
├── assets/              Görselleriniz (bg.png, badge.png ...). Şimdilik boş.
├── requirements.txt
├── render.yaml          Render ücretsiz web servisi tanımı
├── .env.example         Ortam değişkenleri şablonu
├── .gitignore
└── README.md
```

Eski `models.py`, `schemas.py`, `cyberwars_database.json` ve `web3` bağımlılığı artık gerekmiyor.

## Kurulum

### 1. Veritabanı (zorunlu)
Render ücretsiz katmanında disk kalıcı değildir; her yeniden başlatmada silinir. Neon (neon.tech) veya Supabase'de ücretsiz bir Postgres açın ve bağlantı adresini `DATABASE_URL` olarak kullanın.

### 2. Telegram
BotFather'da botunuza bir Mini App tanımlayın ve URL olarak Render adresinizi girin. Mini App'e kısa ad verdiyseniz `APP_NAME` olarak yazın. Davet linkleri `https://t.me/BOT/APP?startapp=KLAN_ID` biçiminde üretilir.

### 3. Adsgram
Adsgram panelinde reklam biriminin **Reward URL** alanına şunu yazın:

```
https://SERVISINIZ.onrender.com/api/adsgram/reward?userid=[userId]&secret=ADSGRAM_SECRET_DEGERI
```

2× ödüller ve enerji yenileme yalnızca bu sunucu bildirimi geldiğinde verilir. Bu ayar yoksa reklam butonları ödül vermez; bu bilinçlidir.

### 4. Render
Repoyu GitHub'a yükleyin, Render'da **New → Blueprint** ile `render.yaml`'ı seçin. `sync: false` olan değişkenleri panelden girin (`.env.example`'a bakın). Servis 10 dakikada bir kendi `/healthz` adresine istek atar. Ek güvence için cron-job.org veya UptimeRobot ile aynı adresi 5 dakikada bir çağırtın. Render'ın güncel ücretsiz katman limitlerini kontrol edin.

### 5. Yerelde çalıştırma
```
pip install -r requirements.txt
DEV_MODE=1 ADMIN_IDS=1 uvicorn backend.main:app --reload
```
`DEV_MODE=1` iken istekler `X-Init-Data: dev:<id>` başlığıyla kimlik doğrular. **Canlıda asla açmayın.**

## Ekonomi ayarları (`backend/main.py` başı)

| Ayar | Varsayılan | Anlamı |
|---|---|---|
| `DEPOT_SECONDS` | 14400 | Depo dolum süresi (4 saat) |
| `EMAX`, `EREGEN` | 5, 1200 | Enerji tavanı, 1 enerji = 20 dk |
| `ECPM_USD` | 0 | Adsgram panelindeki gerçek eCPM. Ortak payı buna göre tahmin edilir |
| `MIN_PAYOUT_USD` | 100 | Ortak ödeme talebi alt sınırı |

Ortak payı = onaylı reklam sayısı × eCPM / 1000 × %25. Rakam tahmindir; ödemeden önce Adsgram panelindeki gerçek gelirle karşılaştırın.

## Ödeme akışı
Ortak, Klan sekmesinde cüzdan adresini girip talep eder. Süper admin aynı sekmede bekleyen talepleri görür, ödemeyi kendi cüzdanından yapar, işlem hash'ini girip **Ödendi** der. Reddedilen talebin tutarı bakiyeye geri döner.

## Bilinen sınırlar
- Skor doğrulaması süre ve saniyede en fazla skor sınırına bakar; girdi kaydıyla tam tekrar doğrulaması yok.
- Klan puanı sezonluk sıfırlanmıyor.
- Cüzdan sahipliği imzayla doğrulanmıyor. Mainnet token dağıtımından önce eklenmeli.
- Mainnet token dağıtımı bu projeye bağlı değil.
- Veritabanı şeması değişirse (`create_all` yalnızca eksik tabloları oluşturur) eski tabloyu silin ya da Alembic ekleyin.
