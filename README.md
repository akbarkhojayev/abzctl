# abzctl

Loyihangizni serverga **bitta buyruq** bilan chiqaradi. Docker, Caddy va SSL (HTTPS)
ni o'zi o'rnatadi va sozlaydi. Sizdan faqat kerakli narsalarni ketma-ket so'raydi.

---

## 1-qadam: O'rnatish (bir marta)

**Linux / macOS** — terminalda:

```bash
bash install.sh
```

**Windows** — `install.bat` faylini ikki marta bosing.

O'rnatuvchi Python'ni tekshiradi (yo'q bo'lsa o'rnatadi), kerakli hamma narsani o'rnatadi
va `abzctl` buyrug'ini tayyorlaydi. Tugagach, **terminalni yopib qayta oching**.

---

## 2-qadam: Deploy

```bash
abzctl
```

Hammasi shu. abzctl savollarni birma-bir beradi:

```text
── 1. Server ──────────────────────────
Server IP manzili: 203.0.113.10
Foydalanuvchi (root):
SSH port (22):
root@203.0.113.10 paroli (ko'rinmaydi):
✔ Serverga ulanish
✔ Serverni tekshirish

── 2. Loyiha ──────────────────────────
GitHub/GitLab repo havolasi: https://github.com/user/my-app.git
Domen ulaymizmi? [y/n] (n): y
Domen: app.example.com
Boshlaymizmi? [y/n] (y):

── 3. Kerakli dasturlar ───────────────
✔ curl, git
✔ Docker
✔ Docker Compose
✔ Caddy (HTTPS)

── 4. Deploy ──────────────────────────
✔ Kodni yuklab olish
ℹ Port aniqlandi: 3000 (web servisi)
✔ Build va ishga tushirish
✔ Ilova javob berishini kutish

── 5. Domen va HTTPS ──────────────────
✔ Caddy: app.example.com → 3000

╭─────────── 🚀 Tayyor! ───────────╮
│  Manzil: https://app.example.com │
╰──────────────────────────────────╯
```

Qavs ichidagi qiymatni qabul qilish uchun shunchaki **Enter** bosing.

Parolni xato kiritsangiz, qayta so'raladi. Kompyuteringizda SSH kalit bo'lsa, abzctl
uni o'zi topadi va parol o'rniga uni ishlatishni taklif qiladi.

---

## Yangilash

Kodni GitHub'ga push qilgandan keyin yana yozing:

```bash
abzctl
```

abzctl oxirgi deploy'ni eslab qoladi va faqat bitta narsani so'raydi:

```text
ℹ Oxirgi deploy: my-app → root@203.0.113.10 (https://app.example.com)
Shu loyihani yangilaymizmi? [y/n] (y):
```

Parollar hech qachon saqlanmaydi. Ular har safar so'raladi.

---

## Qanday loyihalarni deploy qila oladi

Loyihangizga hech narsa qo'shish shart emas. abzctl loyihani o'zi tanib oladi:

| Repo'da nima bor | abzctl nima qiladi |
|---|---|
| `compose.yaml` / `docker-compose.yml` | Sizning compose faylingizni ishlatadi |
| faqat `Dockerfile` | Uni build qiladi, portni `EXPOSE` qatoridan oladi |
| hech biri yo'q | Loyiha turini o'zi aniqlaydi va image'ni koddan yasaydi ([Railpack](https://railpack.com)) |

Avtomatik aniqlanadigan loyiha turlari: **Node.js** (Express, Next.js, Nuxt, Vite, Astro...),
**Python** (Django, Flask, FastAPI), **Go**, **PHP** (Laravel), **Ruby** (Rails), **Java**,
**Rust**, **.NET**, **Deno**, **Elixir** va **statik HTML** saytlar.

Bundan tashqari abzctl o'zi:

- **Port tanlaydi:** serverda bo'sh portni topadi va ilovaga `PORT` muhit o'zgaruvchisi
  orqali beradi. Keyingi deploy'larda ham shu port saqlanadi.
- **`.env` yaratadi:** repo'da `.env.example` bo'lsa, undagi har bir sozlamani so'raydi.
  Maxfiy kalitlarni (`SECRET_KEY`, `JWT_SECRET` va h.k.) avtomatik yaratib beradi.
- **Swap qo'shadi:** server RAM'i 2 GB dan kam bo'lsa, build xotira yetmay
  yiqilmasligi uchun.
- **Log ko'rsatadi:** ilova ishga tushmasa, xatoni topishingiz uchun konteyner loglarini
  chiqaradi.

> Ilovangiz portni koddan qat'iy belgilamasdan, `PORT` muhit o'zgaruvchisidan olishi
> kerak. Masalan Node.js'da: `app.listen(process.env.PORT || 3000)`.
> Ko'pchilik framework'lar buni o'zi qiladi.

Agar loyiha turi aniqlanmasa, abzctl buni aytadi. Bunday holda repo'ga `Dockerfile` qo'shing.

### Ma'lumotlar bazasi

abzctl loyiha kutubxonalariga qarab qaysi baza kerakligini o'zi aniqlaydi va uni
serverda ishga tushiradi:

| Kodda topilsa | Ishga tushiriladi | Ilovaga beriladi |
|---|---|---|
| `pg`, `psycopg`, `asyncpg`, Prisma (postgresql), `DB_CONNECTION=pgsql`... | PostgreSQL | `DATABASE_URL`, `DB_HOST`, `DB_PASSWORD`, `PG*`... |
| `mysql2`, `pymysql`, `mysqlclient`, `DB_CONNECTION=mysql`... | MySQL | `DATABASE_URL`, `DB_*`, `MYSQL_*` |
| `mongoose`, `pymongo`, `motor`... | MongoDB | `MONGODB_URI`, `MONGO_URL` |
| `redis`, `ioredis`, `bullmq`, `predis`... | Redis | `REDIS_URL`, `REDIS_HOST` |

Parollar avtomatik yaratiladi. Ma'lumotlar qayta deploy qilganda ham saqlanib qoladi.
Parollar serverda `/var/www/<loyiha>/.abzctl/services.json` faylida turadi.

### Telegram botlar va fon jarayonlari

Faqat bitta `main.py` (yoki `bot.py`, `index.js`) dan iborat bot ham deploy bo'ladi:

- **Kutubxonalar:** `requirements.txt` bo'lmasa, abzctl uni `import` qatorlaridan
  yaratadi (`aiogram`, `python-telegram-bot`, `pyTelegramBotAPI`, `python-dotenv`...).
- **Sozlamalar:** koddagi `os.getenv("BOT_TOKEN")` kabi qatorlarni topib, tokenni
  so'raydi. Tokenni bo'sh qoldirib bo'lmaydi.
- **Port va domen kerak emas:** abzctl botni o'zi taniydi (aiogram, telebot, telegraf,
  grammy, discord.py...) va domen so'ramaydi.
- **Doim ishlab turadi:** bot yiqilsa yoki server qayta yoqilsa, o'zi qayta ishga tushadi.

Token noto'g'ri bo'lsa, abzctl buni aytadi. Tokenni serverda tuzating
(`nano /var/www/<bot>/.env`) va yana `abzctl` deb yozing.

---

## Domen ulash uchun

1. Domen provayderingizda **A-rekord** yarating: `app.example.com` → server IP.
2. Serverda **80** va **443** portlari ochiq bo'lsin. Bulut provayderlarida (AWS,
   Hetzner, DigitalOcean) bu portlarni provayder panelida oching.

SSL sertifikat avtomatik olinadi. DNS hali sozlanmagan bo'lsa, abzctl oxirida nima qilish
kerakligini ko'rsatadi.

---

## Server talablari

- **Ubuntu** yoki **Debian**
- `root` foydalanuvchi, yoki `sudo` huquqiga ega foydalanuvchi

---

## Muammo bo'lsa

Xatolik qizil oynada ko'rsatiladi, uning ostida 💡 belgisi bilan nima qilish kerakligi
yoziladi.

| Muammo | Yechim |
|---|---|
| Serverga ulanib bo'lmadi | IP va portni tekshiring, server yoqilganmi? |
| Parol xato | Qayta kiriting (3 marta urinish mumkin) |
| Loyiha turi aniqlanmadi | Repo'ga `Dockerfile` qo'shing |
| Ilova javob bermadi | Chiqarilgan loglarni o'qing; ilova `PORT` o'zgaruvchisini tinglayotganini tekshiring |
| Private (yopiq) repo klonlanmadi | Havolada token ishlating: `https://<TOKEN>@github.com/user/app.git` |

Batafsil log ko'rish uchun: `abzctl deploy -v`

Serverni deploy'siz faqat tekshirish uchun: `abzctl check-server`

---

## Qo'shimcha: savol-javobsiz ishlatish (CI/skriptlar uchun)

Barcha qiymatlarni flaglar bilan berish mumkin. Bunda abzctl hech narsa so'ramaydi:

```bash
ABZCTL_PASSWORD='parol' abzctl deploy -h 203.0.113.10 -r https://github.com/user/app.git -d app.example.com -y
```

Barcha flaglar ro'yxati: `abzctl deploy --help`
