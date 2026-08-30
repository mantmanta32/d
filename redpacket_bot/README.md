# RedPacketBot — Otonom Avcı 🟥

Binance Red Packet kodlarını **kendi kendine** bulan, filtreleyen, önceliklendiren,
kullanan ve **öğrenen** tamamen modüler bir otonom sistem.

> **Felsefe**
> - **Elle müdahale yok:** kaynakları kendisi tarar, kodları doğrular, oturumu yeniler,
>   başarısızlıkları teşhis eder.
> - **Modüler:** her parça bağımsız çalışır, değiştirilebilir, genişletilebilir.
> - **Güvenlik şeffaflığı:** ne yaptığını günlükler ve ölçümlerle kanıtlar; şüpheli
>   durumda (ban, ölü oturum) **durur ve raporlar**.

---

## 🚀 Hızlı Başlangıç

```bash
cd redpacket_bot

# 1) Oturumu içe aktar (tarayıcı DevTools → Network → Export HAR)
python3 main.py import-har data/session.har

# 2) Otonom döngüyü başlat (VARSYILAN: SİMÜLASYON — ağa çıkmaz, güvenli)
python3 main.py

# 3) CANLI mod (gerçek istekler! oturum hazırken kullan)
python3 main.py --live

# Yardımcılar
python3 main.py status      # JSON durum özeti
python3 main.py --once      # tek tarama + tek plan turu (test)
python3 main.py resume      # acil duruştan sonra devam ettir
```

**Bağımlılık:** yalnızca Python 3.10+ standart kütüphanesi.
İsteğe bağlı: `pip install cryptography` → oturum çerezleri güçlü Fernet ile şifrelenir.

> ⚠️ **Güvenlik:** `mode: "simulation"` iken sistem **hiçbir gerçek istek atmaz**;
> claim sonuçlarını olasılıklı olarak simüle eder. Böylece tüm otonom beyni
> gerçek hesabı riske atmadan test edersin. `--live` verdiğinde oturum çerezlerin
> gerçek isteklerde kullanılır.

---

## 🧠 Otonomi nasıl çalışır?

Kendini yönetme döngüsü (`brain/planner.py` — otonominin kalbi):

```
1. DEĞERLENDİR   → oturum sağlığı, kuyruk, başarı oranı, hız limitleri
2. HEDEF BELİRLE → en kârlı kodları seç (öncelik: success > banned > rate_limited …)
3. EYLEM PLANLA  → gecikme süreleri, sıra, acil durum kontrolleri
4. UYGULA & GÖZLEMLE → claim et, sonucu puanla, hafızaya yaz
5. ÖĞREN & GELİŞ → hangi kaynak daha kaliteli? hangi saat daha verimli?
                   hangi gecikme aralığı güvenli? → sonraki planı yeniden kur
```

**Hız denetleyici** (`brain/adaptive_delay.py`) — "ne çok hızlanıp engel yiyelim,
ne de yavaş kalalım":

| Olay | Tepki |
|---|---|
| `success` | gecikme ×0.92 (temkinli hızlanma) |
| `rate_limited` | gecikme ×1.6 + strike sayacı |
| `banned` | gecikme ×3 + **acil durdurma** (insan onayı ister) |
| `session_expired` | gecikme korunur, oturum yenileme tetiklenir |

**Oturum yaşam belirteci** (`core/session_manager.py`): oturum süresi dolmak
üzereyken (varsayılan: 1 saat kala) otomatik yenileme dener; başarısızsa
sistemi durdurur — ölü oturumla asla istek atılmaz.

---

## ⚔️ Kurnazlığa Karşı Kurnaz: Savunma & İstihbarat

> Ortam düşmanlıklıdır: bot orduları bilerek sahte kod üretir, popüler kanalları
> kirletir, rakibi 10 hatalı girişte kilitlemek için tuzak yayar. Bu yüzden
> artık **deneme-yanılma yok — kanıtla doğrulama var**.

**1) Can Sayacı** (`defense/error_counter.py`) — asla 10'a vardırmaz:

| Hata | Can maliyeti | Tepki |
|---|---|---|
| `invalid` (kesin çöp) | **1.0** | kaynak cezalandırılır |
| `expired` (gerçekti, geç kaldık) | **0.5** | kaynak itibarı korunur |
| `rate_limited` | 0 | geri çekilme, hız düşürme |
| `banned` | — | **acil duruş** (manuel onay ister) |

Son **2 can rezervdir** — asla harcanmaz. Kritik eşik (7) aşılırsa yalnızca
düşük riskli denemelere izin verilir. Canlar zamanla geri gelir (tıpkı
Binance'te sayacın sıfırlanması gibi).

**2) Desen Yargıcı** (`intelligence/pattern_judge.py`) — denemeden ÖNCE yargılar:
- BP öneki + uzunluk, Shannon entropisi, harf/rakam dengesi,
  tekrarlı karakterler, ardışık diziler (ABC/123), klavye desenleri
- **Öğrenen kara liste:** hangi önek/rakam-oranı/entropi aralığı tarihte çok
  "invalid" ürettiyse yeni kodlar hafif cezalandırılır (bilinçli zayıf ceza —
  kendi kendini zehirlemeyi önler; asıl silah kaynak karantinasıdır)

**3) Kaynak İtibarı** (`intelligence/source_trust.py`) — "en çok paylaşılan =
en çok kirletilen":
- invalid → itibar düşer, success → artar, expired → değişmez, banned → sıfır
- eşik altı kaynak **karantinaya** girer: kodları ancak çok yüksek desen
  skoruyla denenebilir
- **burst** (kısa sürede çok kod) ve **flash** (aynı kod aniden çok kaynakta)
  tespiti → koordineli tuzak/бот üretimi şüphesi

**4) Savaşçı Beyni** (`brain/safe_planner.py`) — her kod 3 kapıdan geçer:

```
GATE 1 desen:  "junk" → asla denenmez (can yaktırmaz)
GATE 2 kaynak: karantina + düşük skor → ertelenir
GATE 3 can:    her istek ÖNCESİ can kontrolü (tur içinde bile rezerv korunur)
→ risk/değer oranına göre sıralama: en verimli kod önce
```

Tüm savunma davranışları testlerle kilitlendi:
`tests/test_defense_intel.py` + e2e senaryoları (tuzak ayıklama, can rezervi,
karantina, öğrenen kara liste).

---

## 📂 Proje Yapısı

```
redpacket_bot/
│
├── main.py                    # Tek giriş noktası: başlat/durdur/import-har/status
│
├── scanner/                   # 1) KAYNAK TARAMA
│   ├── base.py                #    ortak taban: indirme, filtre, güven puanı
│   ├── binance_square.py      #    Binance Square (resmi kaynak, güven 0.80)
│   ├── telegram_channel.py    #    t.me/s aynaları (güven 0.90 — en hızlı)
│   ├── forums.py              #    DuckDuckGo + X/Reddit/forum/bölgesel aramalar (0.40)
│   └── aggregators.py         #    MEXC/MiningCombo/redpacketcode (0.55)
│
├── core/                      # 2) ÇALIŞMA ÇEKİRDEĞİ
│   ├── session_manager.py     #    oturum canlılık, yenileme, şifreli saklama
│   ├── api_connector.py       #    istek/yanıt + hata sınıflandırma
│   └── queue_engine.py        #    kalıcı kod kuyruğu + öncelik
│
├── brain/                     # 3) KARAR & PLANLAMA (OTONOMİNİN KALBİ)
│   ├── planner.py             #    kendini yönetme döngüsü (seçim + sonuç işleme kancaları)
│   ├── safe_planner.py        #    SAVAŞÇI BEYNİ: desen→kaynak→can 3 kapısı, risk/değer sıralama
│   ├── memory.py              #    SQLite öğrenme hafızası (kaynak/saat istatistikleri)
│   └── adaptive_delay.py      #    kendini ayarlayan hız denetleyici
│
├── defense/                   # 4) SAVUNMA (CAN YÖNETİMİ)
│   └── error_counter.py       #    kesin sayaç: 10 can, 2 rezerv, kritik eşik, acil duruş
│
├── intelligence/              # 5) İSTİHBARAT (ÖN-DOĞRULAMA)
│   ├── pattern_judge.py       #    desen yargıcı: entropi/anomali + öğrenen kara liste
│   └── source_trust.py        #    kaynak itibarı: karantina, burst/flash tuzak tespiti
│
├── observer/                  # 6) GÖZLEM, RAPOR & UYARI
│   ├── logger.py              #    konsol + dosya + olay akışı (events.jsonl)
│   ├── dashboard.py           #    terminal panosu + web paneli (0.0.0.0:8899)
│   └── notifier.py            #    yalnızca önemli olayları bildirir (throttle'lı)
│
├── utils/                     # 7) ARAÇLAR
│   ├── har_parser.py          #    HAR → çerez/csrf/UA/endpoint çıkarımı
│   ├── crypto.py              #    Fernet şifreleme (+ XOR geri düşüş)
│   └── helpers.py             #    kod filtreleme, VirtualClock, redact
│
├── config/settings.json       # tüm davranış ayarları (defense & intelligence bölümleri dahil)
├── data/                      # çalışma zamanı: kuyruk, hafıza, şifreli oturum, günlükler
└── tests/                     # pytest testleri (çekirdek + savunma + uçtan uca simülasyon)
```

---

## ⚙️ Konfigürasyon (`config/settings.json`)

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `general.mode` | `simulation` | `live` için `--live` bayrağı |
| `general.sim_speed` | `60` | sanal saat ölçeği (1 gerçek sn ≈ 1 sanal dk) |
| `session.session_lifetime_hours` | `6` | oturum ömrü |
| `session.refresh_threshold_hours` | `1` | bu kadar kala yenileme tetiklenir |
| `claimer.endpoint` | gift-box/code/query | hedef uç nokta (esnek) |
| `claimer.min/max_delay_seconds` | `1 / 30` | adaptif gecikme sınırları |
| `brain.batch_size` | `5` | tur başına denenen kod sayısı |
| `observer.web_port` | `8899` | web paneli portu |

**Kaynaklar:** `scanner.sources.*` bölümünden kanal listelerini, yerel HTML
kayıt yollarını ve API adreslerini doldur; `enabled: false` olanlar atlanır.

---

## 🔍 Gözlem & Raporlama

- **Web paneli:** http://localhost:8899 (otomatik yenilenir; JSON API: `/api/status`)
- **Terminal panosu:** her turda tek satır özet; yeni kod bulununca tam pano
- **Günlükler:** `data/logs/redpacketbot.log` (dönen), `data/events.jsonl` (olay akışı)
- **Öğrenme hafızası:** `data/memory.db` — kaynak kalitesi, saat verimliliği, başarı oranı
- **Bildirimler:** yalnızca `SUCCESS` (rekor kazanç), `WARNING`, `ERROR`, `CRITICAL`;
  webhook ile Discord/Slack'e bağlanabilir (`observer.webhook_url`)

## 🧪 Testler

```bash
cd redpacket_bot
python3 -m pytest tests/ -v        # pip install pytest gerekir
```

Testler şunları doğrular: kod filtreleme, kuyruk önceliği, şifreleme turu,
HAR ayrıştırma, öğrenme hafızası, adaptif gecikme sınırları ve **uçtan uca
otonom simülasyon** (tarayıcı → kuyruk → claim → öğrenme; ölü oturumda
istek atılmadığı güvenlik kontrolü dahil).

---

## 🛡️ Güvenlik Notları

- Oturum çerezleri diskte **şifreli** tutulur (`data/session.enc`, 0600 anahtar).
- Günlüklerde uzun belirteçler otomatik maskelenir (`***`).
- `mode: simulation` iken ağa **hiç çıkılmaz**; rate-limit ve ban korumaları
  simülasyonda da devrededir (beyin gerçek koşullara hazırlanır).
- Ölü oturum / yenileme başarısızlığı → sistem kendini durdurur ve CRITICAL raporlar.
- Canlı modda bile istek hızı adaptif denetleyiciyle sınırlıdır; `--live` kullanımı
  tamamen kullanıcının sorumluluğundadır (Binance Hizmet Şartları'nı kontrol edin).
