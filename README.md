# abzctl

**Git’dan o‘z serveringizga sodda deploy.**

SSH orqali web ilovalar va qo‘llab-quvvatlanadigan botlarni VPS’ga chiqaradigan CLI.
Docker, Caddy va HTTPS sozlamalarini bir oqimga birlashtiradi.

**[Sayt](https://abzctl.abboskhoja.uz/) · [To‘liq qo‘llanma](https://abzctl.abboskhoja.uz/docs.html) · [Birinchi deploy](https://abzctl.abboskhoja.uz/docs.html#deploy)**

## Tez boshlash

Repo’ni yuklab oling:

```bash
git clone https://github.com/akbarkhojayev/abzctl.git
cd abzctl
```

**Linux / macOS:**

```bash
bash install.sh
```

**Windows — PowerShell:**

```powershell
.\install.bat
```

Terminalni qayta oching va ishga tushiring:

```bash
abzctl
```

Server ma’lumotlari va Git repo havolasini kiriting, kerakli sozlamalarni to‘ldiring.
Keyingi yangilash uchun kodni Git’ga push qilib, yana `abzctl` bajaring.

Kompyuterda Git va Python 3.9+, serverda **Ubuntu/Debian**, SSH va root yoki sudo
huquqi kerak. Birinchi ulanishdan oldin server fingerprint’ini tekshirib,
SSH kalitini `known_hosts` fayliga kiriting.

## Imkoniyatlar

- Docker Compose, Dockerfile yoki Railpack orqali build va deploy.
- Caddy orqali domen va avtomatik HTTPS sozlash.
- `.env` qiymatlari va baza ehtiyojlarini aniqlashga yordam berish.
- Qo‘llab-quvvatlanadigan botlar va fon jarayonlarini ishga tushirish.
- Interaktiv ishlatish yoki CI uchun flaglar orqali deploy.

## Qo‘llanma

Batafsil ko‘rsatmalar alohida saytda:

| Mavzu | Havola |
|---|---|
| O‘rnatish | [Linux](https://abzctl.abboskhoja.uz/docs.html#linux) · [macOS](https://abzctl.abboskhoja.uz/docs.html#macos) · [Windows](https://abzctl.abboskhoja.uz/docs.html#windows) |
| Serverga ulanish | [SSH va server talablari](https://abzctl.abboskhoja.uz/docs.html#ssh) |
| Sozlamalar | [.env va maxfiy qiymatlar](https://abzctl.abboskhoja.uz/docs.html#environment) · [Domen va HTTPS](https://abzctl.abboskhoja.uz/docs.html#domain) |
| Loyihalar | [Botlar](https://abzctl.abboskhoja.uz/docs.html#workers) · [Ma’lumotlar bazalari](https://abzctl.abboskhoja.uz/docs.html#databases) |
| Yangilash va avtomatlashtirish | [Yangilash / private repo](https://abzctl.abboskhoja.uz/docs.html#updates) · [CLI / CI](https://abzctl.abboskhoja.uz/docs.html#cli) |
| Yordam | [Muammolarni hal qilish](https://abzctl.abboskhoja.uz/docs.html#troubleshooting) · [GitHub Issues](https://github.com/akbarkhojayev/abzctl/issues) |

Avtomatik rollback va backup boshqaruvi hozir mavjud emas.

## Hissa qo‘shish

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
bash -n install.sh
```

Xatolar va takliflar uchun [issue oching](https://github.com/akbarkhojayev/abzctl/issues)
yoki pull request yuboring.

[MIT litsenziyasi](LICENSE).
