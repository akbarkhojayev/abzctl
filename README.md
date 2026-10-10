# abzctl

**abzctl** — loyihani bitta buyruq bilan serverga chiqaruvchi CLI vosita.

Siz server rekvizitlari (IP, foydalanuvchi, parol yoki SSH kalit) va Git repozitoriy
havolasini berasiz. abzctl esa quyidagilarni avtomatik bajaradi:

1. Serverga SSH orqali ulanadi.
2. Kerakli dasturlarni (`curl`, `git`, Docker, Docker Compose, Caddy) tekshiradi va
   yetishmaganlarini o'rnatadi.
3. Loyihani `/var/www/<loyiha_nomi>` papkasiga klonlaydi (keyingi safar `git pull` qiladi).
4. `docker compose up -d --build` buyrug'i bilan loyihani ishga tushiradi.
5. Domen berilgan bo'lsa, Caddy orqali uni ulaydi va Let's Encrypt SSL sertifikatini
   avtomatik oladi.

---

## Mundarija

- [Talablar](#talablar)
- [O'rnatish](#ornatish)
- [Tezkor boshlash](#tezkor-boshlash)
- [Loyihani deploy'ga tayyorlash](#loyihani-deployga-tayyorlash)
- [`abzctl check-server` buyrug'i](#abzctl-check-server-buyrugi)
- [`abzctl deploy` buyrug'i](#abzctl-deploy-buyrugi)
- [Autentifikatsiya: parol, SSH kalit, sudo](#autentifikatsiya-parol-ssh-kalit-sudo)
- [Domen va SSL](#domen-va-ssl)
- [Yopiq (private) repozitoriylar](#yopiq-private-repozitoriylar)
- [Qayta deploy qilish va yangilash](#qayta-deploy-qilish-va-yangilash)
- [Bitta serverda bir nechta loyiha](#bitta-serverda-bir-nechta-loyiha)
- [CI/CD va skriptlarda ishlatish](#cicd-va-skriptlarda-ishlatish)
- [Serverda nima o'zgaradi](#serverda-nima-ozgaradi)
- [Xatoliklar va ularni hal qilish](#xatoliklar-va-ularni-hal-qilish)
- [Loyihani serverdan o'chirish](#loyihani-serverdan-ochirish)
- [abzctl'ni yangilash va o'chirish](#abzctlni-yangilash-va-ochirish)
- [Loyiha tuzilishi](#loyiha-tuzilishi)

---

## Talablar

**abzctl ishlaydigan kompyuterda:**

- Python **3.9** yoki undan yangi versiya
- Server bilan tarmoq aloqasi (SSH porti ochiq bo'lishi kerak)

Kompyuterda `ssh`, `git` yoki Docker o'rnatilgan bo'lishi shart emas: barcha ishlar
serverda bajariladi.

**Serverda:**

- **Ubuntu** (20.04+) yoki **Debian** (11+). Boshqa distributivlar hozircha qo'llab-quvvatlanmaydi.
- `root` foydalanuvchisi yoki `sudo` huquqiga ega foydalanuvchi
- Internet aloqasi (paketlar va Docker image'larni yuklab olish uchun)
- Kamida ~1 GB RAM tavsiya etiladi (Docker image build qilish uchun)

---

## O'rnatish

### Linux / macOS

```bash
git clone <abzctl-repo-havolasi> ~/abzctl   # yoki papkani nusxalang
cd ~/abzctl

python3 -m venv .venv
source .venv/bin/activate
pip install -e .

abzctl --version
```

`.venv` faollashtirilmagan terminalda ham `abzctl` ishlashi uchun `pipx` dan foydalaning:

```bash
sudo apt install pipx        # macOS: brew install pipx
pipx ensurepath              # terminalni qayta oching
pipx install -e ~/abzctl
```

> **Eslatma:** Ubuntu 23.04+ va Debian 12+ da `pip install` to'g'ridan-to'g'ri ishlatilsa
> `externally-managed-environment` xatosi chiqadi. Bu normal holat: virtual muhit yoki
> `pipx` dan foydalaning.

### Windows

1. [python.org](https://www.python.org/downloads/) dan Python o'rnating. O'rnatishda
   **"Add python.exe to PATH"** belgisini qo'ying.
2. PowerShell'da:

   ```powershell
   cd C:\Users\<siz>\abzctl
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e .
   abzctl --version
   ```

   Agar `Activate.ps1` skriptlar bloklangani haqida xato bersa:
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

3. Rangli chiqish va spinner to'g'ri ko'rinishi uchun **Windows Terminal** yoki
   **PowerShell 7** ishlating. Eski `cmd.exe` oynasida emoji'lar buzilib ko'rinishi mumkin.

Windows'da SSH kalit yo'li odatda `C:\Users\<siz>\.ssh\id_ed25519` bo'ladi, `~/.ssh/id_ed25519`
ko'rinishida yozsangiz ham ishlaydi.

### O'rnatishni tekshirish

```bash
abzctl --help
abzctl deploy --help
abzctl check-server --help
```

> `-h` qisqa flagi `--host` uchun band qilingan. Yordam uchun faqat `--help` ishlating.

---

## Tezkor boshlash

```bash
# 1. Server tayyorligini tekshiring
abzctl check-server -h 203.0.113.10

# 2. Deploy qiling (yetishmagan ma'lumotlar so'raladi)
abzctl deploy

# yoki hammasini flaglar bilan bering:
abzctl deploy -h 203.0.113.10 -k ~/.ssh/id_ed25519 \
  -r https://github.com/user/my-app.git \
  --app-port 3000 -d app.example.com
```

---

## Loyihani deploy'ga tayyorlash

abzctl istalgan tildagi loyihani (Node.js, Python, Go, PHP, ...) deploy qila oladi, faqat
shartlar quyidagicha:

### 1. Repo ildizida compose fayli bo'lishi kerak

abzctl quyidagi fayllarni shu tartibda qidiradi va birinchi topilganini ishlatadi:

```
compose.yaml  →  compose.yml  →  docker-compose.yaml  →  docker-compose.yml
```

Fayl repo **ildizida** bo'lishi shart (ichki papkada emas).

### 2. Ilova `--app-port` portiga publish qilingan bo'lishi kerak

abzctl ilovani serverning `localhost:<app-port>` manzili orqali tekshiradi, Caddy ham
so'rovlarni aynan shu manzilga yo'naltiradi. Shuning uchun compose faylida port
`"<app-port>:<konteyner_ichidagi_port>"` ko'rinishida ochilgan bo'lishi kerak.

**Domen bilan ishlatilganda (tavsiya etiladi)** — portni faqat localhost'ga oching, shunda
ilovaga tashqaridan faqat Caddy (HTTPS) orqali kiriladi:

```yaml
services:
  web:
    build: .
    ports:
      - "127.0.0.1:3000:3000"
    restart: unless-stopped
```

**Domensiz (IP:port orqali) ishlatilganda** — port tashqariga ochiq bo'lishi kerak:

```yaml
services:
  web:
    build: .
    ports:
      - "3000:3000"
    restart: unless-stopped
```

Ichki port boshqa bo'lishi mumkin, masalan Django uchun `"127.0.0.1:8000:8000"` va
`--app-port 8000`, yoki `"127.0.0.1:3000:80"` va `--app-port 3000`.

### 3. To'liq misol (Node.js)

`Dockerfile`:

```dockerfile
FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY . .
EXPOSE 3000
CMD ["node", "server.js"]
```

`compose.yaml`:

```yaml
services:
  web:
    build: .
    ports:
      - "127.0.0.1:3000:3000"
    environment:
      - NODE_ENV=production
    restart: unless-stopped
```

### 4. Maxfiy o'zgaruvchilar (`.env`)

`.env` faylini repoga qo'shmang. Uni birinchi deploy'dan keyin serverda qo'lda yarating:

```bash
ssh root@203.0.113.10
nano /var/www/my-app/.env
```

So'ng qayta deploy qiling. `.env` fayli `git pull` paytida o'chib ketmaydi, agar u
`.gitignore` da bo'lsa. Compose faylida unga `env_file: .env` orqali murojaat qiling.

---

## `abzctl check-server` buyrug'i

Serverga ulanadi va hech narsani o'zgartirmasdan uning holatini ko'rsatadi. Deploy'dan
oldin ulanish va huquqlarni tekshirish uchun qulay.

```bash
abzctl check-server -h 203.0.113.10                          # parol so'raladi
abzctl check-server -h 203.0.113.10 -k ~/.ssh/id_ed25519
abzctl check-server -h 203.0.113.10 -u deploy -p 2222 --password 'parol'
```

Natijada ikkita jadval chiqadi:

- **Server:** hostname, OS, kernel, uptime, CPU yadrolari soni, RAM, disk, huquqlar
  (`root` / `sudo mavjud` / `sudo yo'q`)
- **Dasturlar:** `curl`, `git`, `docker`, `docker compose`, `caddy` versiyalari va
  xizmatlar holati (`active` / `inactive`)

Flaglar: `--host/-h`, `--user/-u`, `--port/-p`, `--password`, `--key-file/-k`,
`--key-passphrase`, `--verbose/-v` (tavsifi [deploy bo'limida](#barcha-flaglar)).

---

## `abzctl deploy` buyrug'i

### Interaktiv (wizard) rejim

`--host` yoki `--repo` berilmasa, abzctl yetishmayotgan ma'lumotlarni ketma-ket so'raydi:

```text
$ abzctl deploy
ℹ Interaktiv rejim: yetishmayotgan ma'lumotlarni kiriting.

Server IP manzili: 203.0.113.10
SSH foydalanuvchi (root):
SSH port (22):
Autentifikatsiya usuli [password/key] (password): key
SSH kalit yo'li (~/.ssh/id_ed25519):
Git repozitoriy havolasi: https://github.com/user/my-app.git
Ilovaning ichki porti (3000):
Domen (ixtiyoriy, Enter — o'tkazib yuborish): app.example.com
```

Qavs ichidagi qiymat default hisoblanadi: uni qabul qilish uchun Enter bosing.
Parol kiritilayotganda ekranda ko'rinmaydi.

### Flaglar bilan

```bash
abzctl deploy \
  --host 203.0.113.10 \
  --user root \
  --port 22 \
  --key-file ~/.ssh/id_ed25519 \
  --repo https://github.com/user/my-app.git \
  --branch main \
  --app-port 3000 \
  --domain app.example.com
```

Qisqa shakli:

```bash
abzctl deploy -h 203.0.113.10 -k ~/.ssh/id_ed25519 -r https://github.com/user/my-app.git -d app.example.com
```

Wizard va flaglarni aralashtirish mumkin: masalan faqat `-h` va `-r` bersangiz, parol
so'raladi, qolgan qiymatlar default bo'ladi.

### Barcha flaglar

| Flag | Qisqa | Tavsif | Default |
|---|---|---|---|
| `--host` | `-h` | Server IP manzili yoki host nomi | wizard so'raydi |
| `--user` | `-u` | SSH foydalanuvchi nomi | `root` |
| `--port` | `-p` | SSH porti | `22` |
| `--password` | | SSH paroli; root bo'lmagan foydalanuvchi uchun sudo paroli ham shu. `ABZCTL_PASSWORD` muhit o'zgaruvchisi orqali ham beriladi | kalit bo'lmasa yashirin so'raladi |
| `--key-file` | `-k` | SSH private key yo'li | — |
| `--key-passphrase` | | Shifrlangan kalit paroli (`ABZCTL_KEY_PASSPHRASE`) | kerak bo'lsa so'raladi |
| `--repo` | `-r` | Git repozitoriy havolasi (`https://`, `git@`, `ssh://`) | wizard so'raydi |
| `--branch` | `-b` | Deploy qilinadigan branch | repo'ning asosiy branchi |
| `--name` | | Loyiha nomi, ya'ni `/var/www/<name>` | repo nomidan olinadi |
| `--app-port` | | Ilovaning serverdagi porti (1–65535) | `3000` |
| `--domain` | `-d` | Domen nomi, masalan `app.example.com` | — (domensiz) |
| `--skip-provision` | | curl/git/Docker/Caddy tekshirish va o'rnatish bosqichlarini o'tkazib yuborish | o'chiq |
| `--yes` | `-y` | "Davom etamizmi?" tasdig'ini so'ramaslik | o'chiq |
| `--verbose` | `-v` | Serverdagi buyruqlarning (apt, git, docker build) to'liq chiqishini ko'rsatish | o'chiq |

Loyiha nomi repo havolasidan avtomatik olinadi: `https://github.com/user/My-App.git` →
`my-app`. Nomda faqat kichik harflar, raqamlar, `.`, `_` va `-` qoladi.

### Deploy jarayoni bosqichma-bosqich

Ishga tushirgandan keyin abzctl avval **deploy rejasi** jadvalini ko'rsatadi va tasdiq
so'raydi. So'ng har bir bosqich jonli spinner bilan bajariladi, oxirida esa
`✔ bosqich nomi (vaqt)` yoki `✖ bosqich nomi` chiqadi:

| # | Bosqich | Nima qiladi |
|---|---|---|
| 1 | Serverga ulanish | SSH ulanish o'rnatiladi |
| 2 | OS va huquqlarni tekshirish | Ubuntu/Debian ekanligi va root/sudo huquqi tekshiriladi |
| 3 | curl va git paketlari | Yetishmasa `apt-get` orqali o'rnatiladi |
| 4 | Docker Engine | Yo'q bo'lsa rasmiy `get.docker.com` skripti bilan o'rnatiladi va xizmat yoqiladi |
| 5 | Docker Compose | `docker compose` (v2) plagini tekshiriladi va kerak bo'lsa o'rnatiladi |
| 6 | Caddy *(faqat `--domain` bilan)* | Rasmiy Caddy reposidan o'rnatiladi. Repo ishlamasa, OS reposidan o'rnatiladi |
| 7 | Papkani tayyorlash | `/var/www/<nom>` yaratiladi |
| 8 | Kodni olish | Birinchi marta `git clone`, keyingi safar `git pull --ff-only` |
| 9 | Compose faylini qidirish | Repo ildizidan compose fayli topiladi |
| 10 | `docker compose up -d --build` | Image build qilinadi va konteynerlar ishga tushadi |
| 11 | Ilovani tekshirish | `localhost:<app-port>` dan HTTP javob kutiladi (30 soniyagacha) |
| 12 | Firewall *(domen bilan)* | UFW faol bo'lsa 80 va 443 portlari ochiladi |
| 13 | Caddy konfiguratsiyasi *(domen bilan)* | Caddyfile'ga blok yoziladi, tekshiriladi, Caddy reload qilinadi |

Oxirida **"Deploy yakunlandi"** paneli chiqadi: unda loyiha nomi, commit va ilova manzili
(`https://domen` yoki `http://IP:port`) ko'rsatiladi.

Uzoq bosqichlarda (Docker o'rnatish, image build) spinner yonida serverdagi oxirgi
chiqish qatori ko'rinib turadi. To'liq logni ko'rish uchun `-v` qo'shing.

Ilova 11-bosqichda javob bermasa, bu xato hisoblanmaydi: deploy davom etadi, lekin
ogohlantirish chiqadi. Odatda sabab compose faylida port noto'g'ri publish qilingani
yoki ilova sekin ishga tushayotgani bo'ladi.

---

## Autentifikatsiya: parol, SSH kalit, sudo

### Parol bilan

```bash
abzctl deploy -h 203.0.113.10 -r <repo>              # parol yashirin so'raladi (tavsiya)
ABZCTL_PASSWORD='parol' abzctl deploy -h 203.0.113.10 -r <repo>
abzctl deploy -h 203.0.113.10 --password 'parol' -r <repo>   # shell tarixida qoladi!
```

PowerShell'da muhit o'zgaruvchisi: `$env:ABZCTL_PASSWORD = 'parol'`.

### SSH kalit bilan (tavsiya etiladi)

Agar sizda kalit bo'lmasa, uni yarating va serverga nusxalang:

```bash
ssh-keygen -t ed25519
ssh-copy-id root@203.0.113.10          # Windows'da quyidagi buyruq ishlatiladi
```

Windows PowerShell'da:

```powershell
ssh-keygen -t ed25519
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh root@203.0.113.10 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

So'ng:

```bash
abzctl deploy -h 203.0.113.10 -k ~/.ssh/id_ed25519 -r <repo>
```

Kalit parol (passphrase) bilan shifrlangan bo'lsa, abzctl uni so'raydi yoki
`--key-passphrase` / `ABZCTL_KEY_PASSPHRASE` orqali berish mumkin.

`--key-file` ham, `--password` ham berilmasa, interaktiv rejimda usul tanlash so'raladi.

### Root bo'lmagan foydalanuvchi (sudo)

abzctl foydalanuvchi root emasligini avtomatik aniqlaydi va kerakli buyruqlarni `sudo`
bilan bajaradi. Bunda ikki holat bor:

- **Parol berilgan bo'lsa** — o'sha parol sudo uchun ham ishlatiladi.
- **Faqat kalit berilgan bo'lsa** — parolsiz sudo kerak. Serverda sozlash:

  ```bash
  echo 'deploy ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/deploy
  sudo chmod 440 /etc/sudoers.d/deploy
  ```

  Yoki kalit bilan birga `--password` ham bering: kalit SSH uchun, parol sudo uchun
  ishlatiladi.

Foydalanuvchi `sudo` guruhida bo'lishi kerak: `usermod -aG sudo deploy`.

### Server host kaliti

Birinchi ulanishda server host kaliti avtomatik qabul qilinadi (qo'shimcha savol
berilmaydi). Kalit `~/.ssh/known_hosts` faylida bo'lsa, abzctl undan foydalanadi.

---

## Domen va SSL

### 1. DNS A-rekordini sozlang

Domen provayderingiz (Cloudflare, Namecheap, ahost.uz va h.k.) panelida:

| Turi | Nom | Qiymat |
|---|---|---|
| A | `app` (yoki asosiy domen uchun `@`) | `203.0.113.10` |

Tekshirish: `nslookup app.example.com` yoki `dig +short app.example.com` server
IP'sini qaytarishi kerak. DNS yangilanishi bir necha daqiqadan bir necha soatgacha
vaqt olishi mumkin.

> **Cloudflare ishlatsangiz:** dastlab proxy (to'q sariq bulut) ni o'chirib, "DNS only"
> rejimini tanlang. Aks holda abzctl DNS tekshiruvida Cloudflare IP'sini ko'radi.

### 2. Portlar ochiq bo'lishi kerak

Let's Encrypt sertifikat berishi uchun serverning **80** va **443** portlari internetdan
ochiq bo'lishi shart.

- Serverda **UFW** faol bo'lsa, abzctl bu portlarni o'zi ochadi.
- Bulut provayderlarida (AWS Security Group, Hetzner Firewall, DigitalOcean Cloud
  Firewall, Oracle Cloud va h.k.) portlarni **provayder panelida qo'lda** oching.

### 3. Deploy qiling

```bash
abzctl deploy -h 203.0.113.10 -k ~/.ssh/id_ed25519 -r <repo> --app-port 3000 -d app.example.com
```

Oxirida abzctl DNS'ni tekshiradi:

- `✔ DNS to'g'ri` — sertifikat bir necha soniyada olinadi, sayt `https://` orqali ochiladi.
- `⚠ DNS hali sozlanmagan` — A-rekordni sozlang. Caddy sertifikat olishga o'zi qayta
  urinadi, abzctl'ni qayta ishga tushirish shart emas.

Domenni `http://` yoki `/` bilan yozsangiz ham bo'ladi: `https://App.Example.com/`
avtomatik `app.example.com` ga aylantiriladi.

### Caddyfile qanday yangilanadi

abzctl `/etc/caddy/Caddyfile` ga quyidagi blokni qo'shadi:

```caddy
# abzctl:begin app.example.com
app.example.com {
    reverse_proxy localhost:3000
}
# abzctl:end app.example.com
```

- Faqat `# abzctl:begin/end` markerlari orasidagi blok o'zgartiriladi. Siz qo'lda
  yozgan boshqa bloklarga tegilmaydi.
- Xuddi shu domen qayta deploy qilinsa (masalan boshqa port bilan), blok yangilanadi,
  yangi blok qo'shilmaydi.
- Shu domen uchun **qo'lda yozilgan** blok bo'lsa, abzctl takrorlanishning oldini olish
  uchun to'xtaydi va ogohlantiradi.
- Yangi fayl avval `caddy validate` bilan tekshiriladi. Xato bo'lsa, asl fayl o'zgarmaydi.
- Eski fayl `/etc/caddy/Caddyfile.abzctl.bak` ga saqlanadi. Reload muvaffaqiyatsiz
  bo'lsa, u avtomatik tiklanadi.

Blokka qo'shimcha sozlamalar (masalan `encode gzip`) qo'shmoqchi bo'lsangiz, abzctl
markerlarini o'chirib, blokni o'zingiz boshqaring. Lekin shunda o'sha domen uchun abzctl
Caddyfile'ga tegmaydi va xato beradi: keyingi deploy'larda `--domain` ni bermang.

---

## Yopiq (private) repozitoriylar

Kod serverda klonlanadi, shuning uchun repoga **server** kirish huquqiga ega bo'lishi
kerak (sizning kompyuteringiz emas). Git SSH orqali ulangan foydalanuvchi nomidan
ishlaydi (`root` yoki `-u` bilan berilgan foydalanuvchi).

### 1-usul: Deploy key (tavsiya etiladi)

Serverda:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub                      # shu kalitni nusxalang
ssh-keyscan github.com >> ~/.ssh/known_hosts   # birinchi ulanishdagi savolni oldini oladi
```

GitHub'da: **Repo → Settings → Deploy keys → Add deploy key** (faqat o'qish huquqi yetarli).
GitLab'da: **Settings → Repository → Deploy keys**.

So'ng SSH havola bilan deploy qiling:

```bash
abzctl deploy -h 203.0.113.10 -r git@github.com:user/private-app.git
```

### 2-usul: Token bilan HTTPS

```bash
abzctl deploy -h 203.0.113.10 -r https://<TOKEN>@github.com/user/private-app.git
```

GitHub'da token: **Settings → Developer settings → Fine-grained tokens** (faqat shu repo
uchun, `Contents: Read-only`).

> Token serverdagi `/var/www/<nom>/.git/config` faylida ochiq holda saqlanadi.
> Iloji bo'lsa, deploy key ishlating.

Repoga kirish imkoni bo'lmasa, git parol so'rab qotib qolmaydi: abzctl darhol xato
chiqaradi.

---

## Qayta deploy qilish va yangilash

Koddagi o'zgarishlarni serverga chiqarish uchun xuddi o'sha buyruqni qayta ishga tushiring:

```bash
abzctl deploy -h 203.0.113.10 -k ~/.ssh/id_ed25519 -r https://github.com/user/my-app.git -d app.example.com -y
```

Bu safar:

- O'rnatilgan dasturlar qayta o'rnatilmaydi, faqat tekshiriladi. Tezlashtirish uchun
  `--skip-provision` qo'shing.
- `git pull --ff-only` bilan yangi commit'lar olinadi.
- `docker compose up -d --build --remove-orphans` image'ni qayta build qiladi va faqat
  o'zgargan konteynerlarni qayta yaratadi. Compose faylidan olib tashlangan servislar
  o'chiriladi.
- Caddy bloki o'zgarmagan bo'lsa, Caddy reload qilinmaydi.

**Boshqa branchga o'tish:** `-b staging` qo'shing.

**`git pull` xato bersa** — odatda serverda kimdir fayllarni qo'lda o'zgartirgani
sababli. Tekshirish:

```bash
ssh root@203.0.113.10 "git -C /var/www/my-app status"
```

---

## Bitta serverda bir nechta loyiha

Har bir loyiha o'z papkasida, o'z portida va o'z domenida ishlaydi:

```bash
abzctl deploy -h 203.0.113.10 -r https://github.com/user/frontend.git --app-port 3000 -d example.com
abzctl deploy -h 203.0.113.10 -r https://github.com/user/backend.git  --app-port 8000 -d api.example.com
```

Natija:

```
/var/www/frontend   →  localhost:3000  →  https://example.com
/var/www/backend    →  localhost:8000  →  https://api.example.com
```

Muhim:

- Har bir loyihaning porti **turlicha** bo'lishi kerak (compose fayllarida ham).
- Bitta repo'ni ikki marta (masalan production va staging) deploy qilish uchun `--name`
  bilan har xil nom bering:

  ```bash
  abzctl deploy -h ... -r <repo> -b main    --name app-prod    --app-port 3000 -d app.example.com
  abzctl deploy -h ... -r <repo> -b staging --name app-staging --app-port 3001 -d staging.example.com
  ```

  Bunda compose fayllari bir xil port ishlatmasligi kerak (masalan portni `.env` orqali
  bering: `"127.0.0.1:${APP_PORT}:3000"`).

---

## CI/CD va skriptlarda ishlatish

abzctl terminal interaktiv emasligini (masalan CI'da) o'zi aniqlaydi:

- Hech narsa so'ramaydi. Majburiy ma'lumot yetishmasa, darhol xato beradi.
- Tasdiq so'ralmaydi.

Barcha qiymatlarni flaglar va muhit o'zgaruvchilari orqali bering:

```bash
export ABZCTL_PASSWORD="$SERVER_PASSWORD"
abzctl deploy -h "$SERVER_IP" -r "$REPO_URL" --app-port 3000 -d app.example.com -y
```

**Exit code'lar:**

| Kod | Ma'nosi |
|---|---|
| `0` | Muvaffaqiyatli |
| `1` | Xatolik (ulanish, autentifikatsiya, buyruq xatosi va h.k.) |
| `2` | Noto'g'ri flag yoki qiymat (masalan `--port 99999`) |
| `130` | Ctrl+C bilan to'xtatildi |

### GitHub Actions misoli

`.github/workflows/deploy.yml`:

```yaml
name: Deploy
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: abzctl o'rnatish
        run: pip install git+https://github.com/<siz>/abzctl.git
      - name: SSH kalitni yozish
        run: |
          mkdir -p ~/.ssh
          echo "${{ secrets.SSH_PRIVATE_KEY }}" > ~/.ssh/deploy_key
          chmod 600 ~/.ssh/deploy_key
      - name: Deploy
        run: |
          abzctl deploy \
            -h ${{ secrets.SERVER_IP }} \
            -k ~/.ssh/deploy_key \
            -r https://github.com/${{ github.repository }}.git \
            --app-port 3000 -d app.example.com \
            --skip-provision -y
```

---

## Serverda nima o'zgaradi

abzctl serverda quyidagilarni yaratadi yoki o'zgartiradi:

| Joy | Nima |
|---|---|
| `/var/www/<nom>/` | Loyiha kodi (papka egasi — SSH foydalanuvchisi) |
| Docker | Docker Engine, `docker-compose-plugin`, loyiha konteynerlari va image'lari |
| `/etc/apt/sources.list.d/caddy-stable.list` | Caddy reposi (agar u ishlasa) |
| `/usr/share/keyrings/caddy-stable-archive-keyring.gpg` | Caddy repo kaliti |
| `/etc/caddy/Caddyfile` | abzctl bloklari |
| `/etc/caddy/Caddyfile.abzctl.bak` | Oxirgi o'zgarishdan oldingi nusxa |
| UFW | 80/tcp va 443/tcp uchun ruxsat (UFW faol bo'lsa) |
| Paketlar | `curl`, `git`, `gnupg`, `ca-certificates` (yetishmasa) |

Foydali buyruqlar (serverda):

```bash
cd /var/www/my-app
docker compose ps                 # konteynerlar holati
docker compose logs -f            # ilova loglari
docker compose restart            # qayta ishga tushirish
journalctl -u caddy -n 50         # Caddy va SSL loglari
cat /etc/caddy/Caddyfile          # Caddy konfiguratsiyasi
```

---

## Xatoliklar va ularni hal qilish

Har bir xatolik qizil panelda ko'rsatiladi: unda nima bo'lgani va 💡 belgisi bilan
maslahat bo'ladi. Masofaviy buyruq xatosida bajarilgan buyruq, uning exit code'i va
chiqishining oxirgi qatorlari ham ko'rsatiladi. To'liq log uchun buyruqni `-v` bilan
qayta ishga tushiring.

| Xabar | Sabab | Yechim |
|---|---|---|
| **ulanib bo'lmadi (ulanish rad etildi)** | SSH porti yopiq yoki noto'g'ri | `-p` ni tekshiring; serverda `systemctl status ssh` |
| **ulanish vaqti tugadi** | IP noto'g'ri yoki firewall bloklayapti | IP'ni va provayder firewall'ini tekshiring |
| **manzilini aniqlab bo'lmadi** | Host nomi DNS'da topilmadi | IP manzilni to'g'ridan-to'g'ri bering |
| **SSH protokol xatosi: Error reading SSH protocol banner** | Port SSH emas yoki sshd hali ishga tushmagan | Portni tekshiring, biroz kutib qayta urining |
| **parol orqali kirib bo'lmadi** | Parol/foydalanuvchi noto'g'ri yoki serverda parol bilan kirish o'chirilgan | Rekvizitlarni tekshiring yoki SSH kalit ishlating |
| **SSH kalit orqali kirib bo'lmadi** | Ochiq kalit serverdagi `authorized_keys` da yo'q | [SSH kalit bo'limi](#ssh-kalit-bilan-tavsiya-etiladi) |
| **SSH kalit fayli topilmadi** | `--key-file` yo'li noto'g'ri | Yo'lni tekshiring |
| **kalit shifrlangan, lekin passphrase kiritilmagan** | Kalit paroli kerak | `--key-passphrase` bering |
| **sudo huquqi yo'q yoki parol noto'g'ri** | Foydalanuvchi sudo guruhida emas, yoki kalit bilan kirilgan va NOPASSWD sozlanmagan | [sudo bo'limi](#root-bolmagan-foydalanuvchi-sudo) |
| **Qo'llab-quvvatlanmaydigan OS** | Server Ubuntu/Debian emas | Ubuntu yoki Debian server ishlating |
| **Docker xizmatini ishga tushirib bo'lmadi** | systemd yo'q (masalan konteyner ichida) yoki Docker buzilgan | `journalctl -u docker -n 50` |
| **Repo'ni klonlab bo'lmadi** | Havola noto'g'ri yoki repo yopiq | [Private repo bo'limi](#yopiq-private-repozitoriylar) |
| **Noto'g'ri repo havolasi** | Havola `https://`, `git@` yoki `ssh://` bilan boshlanmagan, yoki maxsus belgilar (`;`, `$`, `|` va h.k.) bor | Havolani tekshiring |
| **papkasida boshqa repo joylashgan** | `/var/www/<nom>` da boshqa loyiha bor | `--name` bilan boshqa nom bering |
| **papkasi bo'sh emas va git repozitoriy emas** | Papkada eski fayllar bor | Papkani tozalang yoki `--name` ishlating |
| **git pull muvaffaqiyatsiz** | Serverdagi fayllar qo'lda o'zgartirilgan | `git -C /var/www/<nom> status` |
| **compose fayli topilmadi** | Repo ildizida compose fayli yo'q | [Loyihani tayyorlash](#loyihani-deployga-tayyorlash) |
| **Docker image build ... muvaffaqiyatsiz** | Dockerfile'da xato yoki RAM yetmadi | `-v` bilan logni ko'ring; RAM kam bo'lsa swap qo'shing |
| **⚠ Ilova localhost:PORT da javob bermadi** | Port publish qilinmagan, noto'g'ri, yoki ilova yiqilgan | Compose'dagi `ports` ni va `docker compose logs` ni tekshiring |
| **Noto'g'ri domen nomi** | Domen formati xato | `app.example.com` ko'rinishida bering |
| **qo'lda yozilgan blok allaqachon mavjud** | Caddyfile'da shu domen markerisiz yozilgan | O'sha blokni o'chiring |
| **Yangi Caddyfile noto'g'ri** | Caddyfile'dagi boshqa (qo'lda yozilgan) blokda sintaksis xatosi | `caddy validate --config /etc/caddy/Caddyfile` |
| **Caddy'ni reload qilib bo'lmadi, eski Caddyfile tiklandi** | Port 80/443 boshqa dastur (nginx, apache) tomonidan band | `ss -tlnp \| grep -E ':80\|:443'`; nginx/apache'ni to'xtating |
| **APT repozitoriylariga ulanib bo'lmadi** | Serverda internet yoki DNS ishlamayapti | `ping 8.8.8.8`, `cat /etc/resolv.conf` |

### Sayt ochilmayapti / SSL yo'q

1. DNS: `dig +short app.example.com` server IP'sini qaytaradimi?
2. Portlar: provayder panelida 80 va 443 ochiqmi?
3. Ilova: serverda `curl -I http://localhost:3000` javob beradimi?
4. Caddy logi: `journalctl -u caddy -n 100` — sertifikat xatolari shu yerda ko'rinadi.
5. Let's Encrypt bitta domen uchun qisqa vaqtda juda ko'p urinishni cheklaydi
   (rate limit). DNS'ni avval to'g'rilab, keyin deploy qiling.

---

## Loyihani serverdan o'chirish

abzctl'da o'chirish buyrug'i yo'q. Loyihani serverda qo'lda o'chiring:

```bash
ssh root@203.0.113.10

cd /var/www/my-app
docker compose down --rmi local -v     # konteynerlar, image'lar va volume'lar (ma'lumotlar ham!)
cd .. && rm -rf /var/www/my-app

nano /etc/caddy/Caddyfile              # "# abzctl:begin app.example.com" ... "# abzctl:end" blokini o'chiring
systemctl reload caddy
```

> `-v` flagi volume'lardagi ma'lumotlarni (masalan ma'lumotlar bazasini) o'chiradi.
> Ma'lumotlarni saqlab qolmoqchi bo'lsangiz, uni olib tashlang.

---

## abzctl'ni yangilash va o'chirish

`pip install -e .` bilan o'rnatilgan bo'lsa, koddagi o'zgarishlar darhol kuchga kiradi.
Yangi versiyani olish uchun:

```bash
cd ~/abzctl && git pull
pip install -e .        # faqat yangi bog'liqliklar qo'shilgan bo'lsa kerak
```

O'chirish:

```bash
pip uninstall abzctl    # yoki: pipx uninstall abzctl
```

---

## Loyiha tuzilishi

```
abzctl/
├── pyproject.toml          # paket ma'lumotlari va `abzctl` buyrug'i
├── requirements.txt        # typer, rich, paramiko
├── README.md
└── abzctl/
    ├── __init__.py         # versiya
    ├── __main__.py         # `python -m abzctl` uchun
    ├── main.py             # CLI: deploy, check-server, wizard
    ├── ui.py               # banner, spinner, panellar, jadvallar
    ├── exceptions.py       # xatolik turlari (sarlavha + maslahat)
    └── core/
        ├── ssh_client.py   # SSH ulanish, buyruq bajarish, sudo, fayl yozish
        ├── provisioner.py  # curl/git, Docker, Compose, Caddy, UFW, server ma'lumotlari
        ├── deployer.py     # /var/www/<nom>, git clone/pull, docker compose
        └── caddy.py        # Caddyfile boshqaruvi, validate, reload, DNS tekshiruvi
```

`pip install -e .` o'rniga to'g'ridan-to'g'ri ishga tushirish ham mumkin:

```bash
pip install -r requirements.txt
python -m abzctl --help
```
