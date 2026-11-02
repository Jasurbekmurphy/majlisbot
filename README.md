# Majlis davomati — Telegram bot

Hodim majlisga keladi → botdagi **«Majlisga kirish»** tugmasini bosadi → xonadagi QR kodni skanerlaydi → bot uni majlisga «+» qilib yozadi. Admin har kuni kim keldi, kim kech qoldi, kim kelmaganini botda ko'radi va Excel hisobot oladi.

## Aldashdan qanday himoyalangan

| Himoya | Nima qiladi |
|---|---|
| **Telefon raqami orqali ro'yxat** | Hodim Telegram'dagi raqamini tugma orqali yuboradi. Bot raqam aynan shu akkauntniki ekanini tekshiradi va admin kiritgan ro'yxat bilan solishtiradi. Bitta raqam = bitta akkaunt. |
| **Jonli kamera** | QR faqat bot ichidagi Telegram skaneri bilan o'qiladi. Galereyadan rasm tanlab bo'lmaydi. |
| **Joylashuv (GPS)** | Skanerlash paytida telefon joylashuvi olinadi. Ofisdan belgilangan masofadan (standart 150 m) uzoq bo'lsa, qabul qilinmaydi. QR rasmini uyga olib ketib skanerlasa ham ishlamaydi. |
| **Vaqt oynasi** | Faqat majlisdan oldin/keyin belgilangan vaqt ichida qabul qilinadi (standart: 30 daq oldin — 60 daq keyin). |
| **Vaqt serverda yoziladi** | Kelgan vaqt telefon soatidan emas, serverdan olinadi — o'zgartirib bo'lmaydi. |
| **Telegram imzosi** | Har bir so'rov Telegram imzosi bilan tekshiriladi. Boshqa odam nomidan so'rov yuborib bo'lmaydi. |
| **Shubhali urinishlar jurnali** | Rad etilgan har bir urinish (uzoqdan, noto'g'ri QR, kech) saqlanadi — admin `/urinishlar` va Excel'dagi «Rad etilganlar» varag'ida ko'radi. |

**Cheklovlar (halol aytib qo'yamiz):** Android'dagi «soxta GPS» dasturlari bilan joylashuvni aldash nazariy jihatdan mumkin; hodim telefonini hamkasbiga berib yuborsa ham tizim bilmaydi. Rad etilganlar jurnali va hamkasblar nazorati bunday holatlarni ko'rsatadi.

**Svet o'chsa:** Bot ofisdagi kompyuterda emas, internetdagi serverda (VPS) ishlaydi. QR qog'ozga bosilgan, hodimlar mobil internetdan foydalanadi — ofisda svet bo'lmasa ham ishlayveradi.

---

## O'rnatish

### 1. Kerakli narsalar
- **Telegram bot**: @BotFather → `/newbot` → token olasiz.
- **Admin ID**: @userinfobot ga yozing — u sizning Telegram ID raqamingizni beradi.
- **VPS server** (Ubuntu 22.04/24.04, 1 GB RAM yetadi) — masalan, mahalliy yoki xorijiy hosting.
- **Domen** (masalan `davomat.kompaniya.uz`) — DNS'da serveringiz IP manziliga A-yozuv qo'shing. Telegram Mini App uchun HTTPS majburiy.

### 2. Serverga o'rnatish (bitta skript)
Serverga SSH orqali kiring va:
```bash
sudo apt update && sudo apt install -y git
sudo git clone https://github.com/Jasurbekmurphy/majlisbot.git /opt/davomat-bot
sudo bash /opt/davomat-bot/deploy/install.sh
```
Skript domen, bot tokeni va admin ID'ni so'raydi, keyin o'zi:
Python muhitini, HTTPS (Caddy + bepul sertifikat), systemd xizmatini va har kecha 03:30 dagi zaxira nusxani sozlaydi.

> Repo **private** bo'lsa, `git clone` login so'raydi: GitHub → Settings → Developer settings → Personal access tokens → *Fine-grained* token (faqat shu repo, Contents: Read) yarating va parol o'rniga shuni kiriting.

Tekshirish: brauzerda `https://domeningiz/health` → `{"ok": true}`.

### 3. Kundalik boshqaruv
```bash
sudo journalctl -u davomat -f                  # loglar
sudo systemctl restart davomat                 # qayta ishga tushirish
sudo bash /opt/davomat-bot/deploy/update.sh    # GitHub'dan yangi versiyani olish
sudo nano /opt/davomat-bot/.env                # sozlamalar (keyin restart)
```
Zaxira nusxalar: `/opt/davomat-bot/backups/` (30 kun saqlanadi).

---

## Birinchi sozlash (admin, botda)

1. Botga `/start` bosing, keyin `/admin` — barcha buyruqlar chiqadi.
2. **Ofis joylashuvi:** majlis xonasida turib `/ofis` → «📍 Shu yerni ofis deb belgilash».
3. **Majlis vaqti:** `/vaqt 09:00`
4. **Majlis kunlari:** `/ish_kunlari 1,2,3,4,5` (6 kunlik bo'lsa `1,2,3,4,5,6`)
5. **QR:** `/qr` → rasmni A4 ga chop eting, laminatsiya qilib majlis xonasiga osing.
6. **Hodimlar:** bot havolasini hodimlarga (yoki umumiy guruhga) tashlang. Har biri:
   `/start` → «📱 Telefon raqamni yuborish» → familiya va ismini yozadi → bazaga o'zi tushadi.
   - Har bir yangi ro'yxatdan o'tgan odam haqida adminga xabar keladi. Begona bo'lsa — «🗑 O'chirish» tugmasi.
   - Ismni noto'g'ri yozgan bo'lsa, admin to'g'rilaydi: `/hodim_qosh +998901234567 To'g'ri Ism`
7. **Botga kirmaganlar:** `/hodimlar` da ⭕ belgisi bilan ko'rinadi. Ularni qo'lda qo'shasiz:
   ```
   /hodim_qosh
   +998901234567 Aliyev Vali
   +998911112233 Karimova Nilufar
   ```
   Keyinchalik shu odam botga kirsa, ism so'ralmaydi — raqami bo'yicha darhol ulanadi.

## Kundalik foydalanish

**Hodim:** botdagi «Majlisga kirish» tugmasi (yoki pastdagi menyu) → GPS'ga ruxsat → QR'ni skanerlash → «✅ Majlisga qo'shildingiz. Vaqt: 08:57:14».

**Admin:**
- `/bugun` — bugungi ro'yxat: vaqtida / kech qolgan / kelmagan
- `/hisobot` — shu oyning Excel hisoboti (`/hisobot 2026-10` yoki `/hisobot 2026-10-01 2026-10-15`)
- `/urinishlar` — bugun rad etilgan urinishlar
- Har kuni belgilanish vaqti tugashi bilan adminlarga hisobot **avtomatik** keladi.

Excel hisobotda 4 varaq bor: **Kunlik** (har kun, har hodim), **Jami** (har hodimning davomat foizi), **Rad etilganlar**, **Ma'lumot**.

## Boshqa sozlamalar
| Buyruq | Ma'nosi |
|---|---|
| `/radius 150` | Ofisdan ruxsat etilgan masofa, metr. Bino katta bo'lsa yoki GPS yomon ushlasa 200–300 qiling. |
| `/oyna 30 60` | Majlisdan 30 daq oldin ochiladi, boshlangandan 60 daq keyin yopiladi. |
| `/kechikish 5` | Majlis boshlangandan 5 daqiqa o'tib kelganlar «kech qoldi». |
| `/qr_yangi` | QR rasmi tarqalib ketgan deb o'ylasangiz — yangisini yaratadi, eskisi ishlamaydi. |
| `/hodim_reset +998…` | Hodim telefon/akkauntini almashtirsa, eski akkauntdan ajratadi. |
| `/hodim_ochir +998…` | Ishdan ketgan hodimni o'chiradi (tarixi saqlanadi). |

## Loyiha tuzilishi
```
bot/
  __main__.py      ishga tushirish, kunlik avtomatik hisobot
  config.py        .env sozlamalari
  db.py            SQLite baza
  checkin.py       belgilanish qoidalari (QR, vaqt, GPS)
  web.py           Mini App sahifasi va /api/checkin
  reports.py       matnli va Excel hisobotlar
  handlers/user.py   hodim: ro'yxatdan o'tish, /tarix
  handlers/admin.py  admin buyruqlari
  webapp/index.html  skaner sahifasi (Telegram Mini App)
deploy/
  install.sh       serverga o'rnatish
  update.sh        yangilash
  backup.sh        zaxira nusxa
```

Zaxira nusxa: `davomat.db` faylini vaqti-vaqti bilan nusxalab qo'ying — barcha ma'lumot shu faylda.
