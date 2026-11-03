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
- **Domen** — Telegram Mini App HTTPS talab qiladi. O'z domeningiz bo'lsa (masalan `davomat.kompaniya.uz`), DNS'da server IP'siga A-yozuv qo'shing. **Bo'lmasa — shart emas:** skript domen so'raganda Enter bossangiz, bepul `IP-manzil.sslip.io` manzili ishlatiladi (masalan `185-203-118-42.sslip.io`). Keyin o'z domeningizga o'tish uchun `.env` dagi `WEBAPP_URL` ni o'zgartirib, `install.sh` ni qayta ishga tushirasiz.

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

Botga `/start` bosing — pastda admin menyusi chiqadi. Hamma narsa tugmalar orqali:

1. **⚙️ Sozlamalar → 📍 Ofis joylashuvi** — majlis xonasida turib bosing.
2. **⚙️ Sozlamalar → 🕗 Majlis vaqti** — masalan `08:00` (kirish tugmasi avtomatik 07:45 da boradi).
3. **⚙️ Sozlamalar → 📅 Majlis kunlari** — kunlarni bosib yoqing/o'chiring.
4. **🔳 QR kod** — rasmni A4 ga chop etib, majlis xonasiga osing.
5. Bot havolasini hodimlarga tashlang. Har biri: `/start` → «📱 Telefon raqamni yuborish» → familiya-ism → tayyor.
   Har bir yangi hodim haqida sizga xabar keladi (begona bo'lsa — «🗑 O'chirish»).
6. Botga kirmaganlarni: **👥 Hodimlar → ➕ Hodim qo'shish** (har qatorda `+998901234567 Familiya Ism`).

## Kundalik ish

**Hodim** (hech qanday buyruq yo'q):
majlisdan 15 daqiqa oldin botdan «🔔 Majlis … boshlanadi» xabari keladi → **📷 Majlisga kirish** → ilova o'zi joylashuvni tekshiradi
(o'chiq bo'lsa «Joylashuvni yoqing» deydi) → kamera ochiladi → QR skanerlanadi → «✅ Davomatga qo'shildingiz».

**Admin menyusi:**
| Tugma | Nima qiladi |
|---|---|
| 📋 Davomat | Bugungi majlis: vaqtida / kech qolgan / kelmaganlar. **✏️ Tahrirlash** — ismni bosib «kelmadi» (QR'ni olib ketib skanerlaganlar uchun) yoki «keldi» (telefoni o'chganlar uchun) qilasiz. «Kelmadi» qilingan hodim shu majlisga qayta skanerlay olmaydi. |
| ➕ Majlis qo'shish | Kun → soat → mavzu → **E'lon qilish**. E'lon darhol hamma hodimga boradi, majlisdan 15 daqiqa oldin esa kirish tugmasi bilan ikkinchi xabar. |
| 📅 Majlislar | Bugungi va kelgusi majlislar; bekor qilish (hodimlarga xabar boradi). |
| 📊 Hisobot | Bugun / shu hafta / shu oy / o'tgan oy — Excel. |
| 👥 Hodimlar | Ro'yxat, hodim kartasi (shu oydagi davomati), o'chirish, akkauntni ajratish, qo'shish. |
| ⚙️ Sozlamalar | Kunlik majlis vaqti va kunlari, eslatma vaqti, kechikish, yopilish vaqti, ofis, masofa. |
| 🔳 QR kod | Joriy QR (chop etish uchun) va «Hozir yangilash». |

**Avtomatik:**
- Har bir majlis tugagach (standart: boshlanganidan 60 daqiqa keyin) davomat hisoboti adminga keladi.
- **QR har 2 kunda soat 16:00 da yangilanadi** — yangi QR rasmi adminga keladi, uni chop etib eskisining o'rniga osasiz. Eski QR darhol ishlamay qoladi.

Excel hisobotda: **Majlislar bo'yicha** (har majlis, har hodim), **Jami** (davomat foizi), **Rad etilganlar** (shubhali urinishlar), **Ma'lumot**.

## Loyiha tuzilishi
```
bot/
  __main__.py      ishga tushirish, kunlik avtomatik hisobot
  config.py        .env sozlamalari
  db.py            SQLite baza
  checkin.py       belgilanish qoidalari (QR, majlis vaqti, GPS)
  web.py           Mini App sahifasi va /api/checkin
  reports.py       matnli va Excel hisobotlar
  scheduler.py     eslatma, e'lon, avtomatik hisobot, QR yangilash
  keyboards.py     menyu tugmalari
  handlers/user.py   hodim: ro'yxatdan o'tish
  handlers/admin.py  admin paneli (tugmalar)
  webapp/index.html  skaner sahifasi (Telegram Mini App)
deploy/
  install.sh       serverga o'rnatish
  update.sh        yangilash
  backup.sh        zaxira nusxa
```

Zaxira nusxa: `davomat.db` faylini vaqti-vaqti bilan nusxalab qo'ying — barcha ma'lumot shu faylda.
