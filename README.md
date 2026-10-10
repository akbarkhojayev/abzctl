# abzctl

SSH orqali Docker Compose loyihalarini Ubuntu/Debian serverga deploy qiluvchi CLI.
Docker va Caddy'ni o'zi o'rnatadi, loyihani `/var/www/<nom>` papkasiga klonlaydi,
`docker compose up -d --build` ni ishga tushiradi va domen berilsa, Caddy orqali
avtomatik Let's Encrypt SSL ulaydi.

## Fayllar tuzilishi

```
abzctl/
├── pyproject.toml          # paket va `abzctl` global buyrug'i
├── requirements.txt
├── README.md
└── abzctl/
    ├── __init__.py         # versiya
    ├── __main__.py         # python -m abzctl
    ├── main.py             # typer CLI: deploy, check-server
    ├── ui.py               # rich: banner, spinner, panellar, jadvallar
    ├── exceptions.py       # tushunarli xatolik turlari (title + hint)
    └── core/
        ├── ssh_client.py   # paramiko: ulanish, stream, exit code, sudo, SFTP
        ├── provisioner.py  # curl/git, Docker, Compose, Caddy, UFW, server ma'lumotlari
        ├── deployer.py     # /var/www/<nom>, git clone/pull, docker compose up
        └── caddy.py        # Caddyfile'ni xavfsiz yangilash, validate, reload, DNS tekshiruvi
```

## O'rnatish

Python 3.9+ kerak.

```bash
cd ~/Desktop/abzctl

# 1-usul: virtual muhit (tavsiya etiladi)
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 2-usul: virtual muhitni faollashtirmasdan, har qanday joydan ishlatish uchun
pipx install -e .

abzctl --version
abzctl --help
```

> Ubuntu 23.04+ / Debian 12+ da tizim `pip` i `externally-managed-environment` xatosini beradi:
> virtual muhit yoki `pipx` dan foydalaning.

## Foydalanish

### Server holatini tekshirish

```bash
abzctl check-server -h 203.0.113.10                       # parol yashirin so'raladi
abzctl check-server -h 203.0.113.10 -k ~/.ssh/id_ed25519
```

### Deploy

```bash
# Interaktiv wizard: hamma narsa so'raladi
abzctl deploy

# Flaglar bilan
abzctl deploy \
  -h 203.0.113.10 -u root -p 22 \
  -k ~/.ssh/id_ed25519 \
  --repo https://github.com/user/my-app.git \
  --app-port 3000 \
  --domain api.example.com

# Parolni muhit o'zgaruvchisi orqali berish (shell tarixida qolmaydi)
ABZCTL_PASSWORD='...' abzctl deploy -h 203.0.113.10 --repo https://github.com/user/my-app.git -y
```

| Flag | Tavsif | Default |
|---|---|---|
| `--host`, `-h` | Server IP manzili | — |
| `--user`, `-u` | SSH foydalanuvchi | `root` |
| `--port`, `-p` | SSH port | `22` |
| `--password` | SSH/sudo paroli (`ABZCTL_PASSWORD`) | yashirin so'raladi |
| `--key-file`, `-k` | SSH private key | — |
| `--key-passphrase` | Shifrlangan kalit passphrase'i | kerak bo'lsa so'raladi |
| `--repo`, `-r` | Git repo havolasi | — |
| `--branch`, `-b` | Git branch | repo'ning asosiy branchi |
| `--name` | Loyiha nomi (`/var/www/<nom>`) | repo nomi |
| `--app-port` | Ilova porti | `3000` |
| `--domain`, `-d` | Domen (SSL bilan) | — |
| `--skip-provision` | Docker/Caddy o'rnatish bosqichini o'tkazib yuborish | — |
| `--yes`, `-y` | Tasdiqlash so'ralmaydi | — |
| `--verbose`, `-v` | Masofaviy buyruqlarning to'liq chiqishi | — |

### Loyihaga talablar

- Repo ildizida `compose.yaml` / `compose.yml` / `docker-compose.yml` bo'lishi kerak.
- Ilova `--app-port` ga publish qilingan bo'lishi kerak. Domen ishlatilsa, portni faqat
  localhost'ga ochgan ma'qul, shunda unga faqat Caddy orqali kiriladi:

  ```yaml
  services:
    web:
      build: .
      ports:
        - "127.0.0.1:3000:3000"
      restart: unless-stopped
  ```

- Private repo: serverga deploy key qo'shing (`git@github.com:...` havola bilan) yoki
  HTTPS havolaga token qo'shing.

### Domen va SSL

1. Domen provayderida A-rekord yarating: `api.example.com → <server IP>`.
2. Server firewall'i (va bulut provayderining security group'i) 80 va 443 portlarini
   ochiq qoldirishi kerak. UFW faol bo'lsa, abzctl bu portlarni o'zi ochadi.
3. Deploy'dan keyin Caddy sertifikatni avtomatik oladi. abzctl DNS'ni tekshiradi va
   rekord hali server IP'siga yo'naltirilmagan bo'lsa ogohlantiradi.

abzctl Caddyfile'dagi o'z bloklarini `# abzctl:begin <domain>` / `# abzctl:end <domain>`
markerlari bilan belgilaydi. Qayta deploy qilinganda faqat shu blok yangilanadi.
Har bir o'zgarish `caddy validate` dan o'tadi, eski fayl
`/etc/caddy/Caddyfile.abzctl.bak` ga saqlanadi va reload muvaffaqiyatsiz bo'lsa tiklanadi.

## Root bo'lmagan foydalanuvchi

Foydalanuvchi `sudo` guruhida bo'lishi kerak. Parol berilsa, u sudo uchun ham ishlatiladi
(`sudo -S`). Faqat kalit bilan kirilsa, parolsiz sudo (`NOPASSWD`) kerak.

## Sinab ko'rish

Haqiqiy serverga ulanmasdan oldin sinash uchun, systemd'li virtual mashinada
(masalan `multipass launch 24.04`) quyidagilarni bajaring:

```bash
abzctl check-server -h <VM_IP> -u ubuntu -k ~/.ssh/id_ed25519
abzctl deploy -h <VM_IP> -u ubuntu -k ~/.ssh/id_ed25519 \
  --repo https://github.com/<user>/<compose-loyiha>.git --app-port 3000 -v
```

Oddiy Docker konteyneri Docker va Caddy xizmatlarini to'liq sinash uchun yaramaydi,
chunki uning ichida systemd yo'q.
