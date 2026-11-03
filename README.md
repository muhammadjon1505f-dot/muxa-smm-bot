# 🤖 Muxa SMM Telegram Bot — 24/7 Render.com Hosting

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/muhammadjon1505f-dot/muxa-smm-bot)

Muxa SMM Bot — Telegram orqali SMM xizmatlari (obunachilar, layklar, ko'rishlar, reaksiyalar) sotuvini avtomatlashtiruvchi zamonaviy bot va Telegram Web App integratsiyasi.

---

## ⚡ Asosiy Imkoniyatlar

1. **Telegram Web App va Inline Menyu**: Foydalanuvchilar to'g'ridan-to'g'ri chiroyli interfeys orqali xizmatlarni tanlashlari va buyurtma berishlari mumkin.
2. **Kengaytirilgan SMM Xizmatlar Boshqaruvi**: Har bir xizmat uchun alohida ID, toifa, minimal/maksimal miqdor va narxlar.
3. **Avtomatik Status Tekshiruvchi**: Buyurtmalar holati (bajarilmoqda, bajarildi, bekor qilindi) fonda avtomatik monitoring qilinadi.
4. **24/7 Render.com Web Server**: Ichki yengil HTTP health-check serveri (`/healthz` va `/`) Render bepul tarifida bot uzluksiz ishlashi uchun sozlangan.

---

## 🚀 Render.com ga Joylashtirish (Deploy)

### 1-usul: Bir bosishda (1-Click Deploy)
Yuqoridagi **Deploy to Render** tugmasini bosing:
1. Render hisobingizga kiring.
2. `BOT_TOKEN` maydoniga `@BotFather` dan olgan bot tokeningizni kiriting.
3. **Apply** tugmasini bosing — Render avtomatik tarzda botni o'rnatadi va ishga tushiradi!

---

### 2-usul: Qo'lda Sozlash (Manual Web Service)
1. [Render.com](https://render.com) ga kiring va **New +** -> **Web Service** ni tanlang.
2. GitHub'dagi `muxa-smm-bot` repozitoriyangizni ulang.
3. Sozlamalarni quyidagicha kiriting:
   - **Name:** `muxa-smm-bot`
   - **Region:** `Frankfurt (EU Central)`
   - **Branch:** `main`
   - **Runtime:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
   - **Instance Type:** `Free`
4. **Environment Variables (Muhit O'zgaruvchilari)** bo'limiga qo'shing:
   - `BOT_TOKEN`: `8711890959:AAElL-L53XhZ1YgpQ6XN0YhWv6FWzixrwrc` (yoki sizning yangi bot tokeningiz)
   - `OWNER_ID`: `8191930658`
   - `WEBAPP_URL`: `https://muhammadjon1505f-dot.github.io/smm-webapp/`
   - `PORT`: `10000`
5. **Create Web Service** tugmasini bosing.

---

## ⏰ Render Free Plan ni 24/7 Uxlamaydigan Qilish (Keep-Alive)

Render bepul xizmatlari 15 daqiqa davomida so'rov kelmasa uxlab qoladi (spin down bo'ladi). Botimizda ichki `/healthz` HTTP serveri mavjudligi sababli buni osonlikcha 24/7 faol ushlab turish mumkin:

1. [cron-job.org](https://cron-job.org) yoki [uptimerobot.com](https://uptimerobot.com) saytiga kiring (bepul ro'yxatdan o'ting).
2. Yangi monitor / cronjob qo'shing:
   - **URL:** `https://muxa-smm-bot-xxxx.onrender.com/healthz` (Render bergan domen)
   - **Interval:** Har 5 yoki 10 daqiqada
3. Shu orqali botingiz hech qachon to'xtab qolmaydi va 24/7 bir tekis ishlaydi!

---

## 🛠 Mahalliy Ishga Tushirish (Local / VPS)

```bash
# Repozitoriyga kiring
cd muxa-smm-bot

# .env fayliga bot tokeningizni kiriting
cp .env.example .env
nano .env

# Kutubxonalarni o'rnating
pip install -r requirements.txt

# Botni ishga tushiring
python3 bot.py
```
