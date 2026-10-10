"""Domen va SSL moduli: Caddyfile'ga reverse proxy blokini xavfsiz qo'shish/yangilash.

Xavfsizlik choralari:
- abzctl o'zi yozgan bloklarni `# abzctl:begin <domain>` / `# abzctl:end <domain>`
  markerlari bilan belgilaydi — qayta deploy qilinganda faqat o'sha blok yangilanadi,
  qo'lda yozilgan konfiguratsiyaga tegilmaydi;
- yangi konfiguratsiya avval vaqtinchalik faylga yoziladi va `caddy validate` bilan tekshiriladi;
- eski Caddyfile zaxira nusxasi saqlanadi va reload muvaffaqiyatsiz bo'lsa tiklanadi.
"""

import re
import socket
import uuid
from typing import List, Optional

from abzctl.core.ssh_client import SSHClient
from abzctl.exceptions import CaddyError, RemoteCommandError

CADDYFILE = "/etc/caddy/Caddyfile"
BACKUP = "/etc/caddy/Caddyfile.abzctl.bak"

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)([a-z0-9-]{1,63}(?<!-)\.)+[a-z]{2,63}$")


def validate_domain(domain: str) -> str:
    """Domen nomini normallashtiradi va formatini tekshiradi."""
    domain = domain.strip().lower().rstrip(".")
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    domain = domain.split("/")[0]
    if not _DOMAIN_RE.match(domain):
        raise CaddyError(
            f"Noto'g'ri domen nomi: {domain}",
            hint="Masalan: api.example.com (http:// va yo'llarsiz).",
        )
    return domain


def render_block(domain: str, app_port: int) -> str:
    """Bitta domen uchun markerlangan Caddy blokini yaratadi."""
    return (
        f"# abzctl:begin {domain}\n"
        f"{domain} {{\n"
        f"    reverse_proxy localhost:{app_port}\n"
        f"}}\n"
        f"# abzctl:end {domain}\n"
    )


def upsert_block(content: str, domain: str, app_port: int) -> str:
    """Caddyfile matnida domen blokini qo'shadi yoki mavjudini almashtiradi (sof funksiya)."""
    pattern = re.compile(
        rf"^# abzctl:begin {re.escape(domain)}\n.*?^# abzctl:end {re.escape(domain)}\n?",
        re.MULTILINE | re.DOTALL,
    )
    block = render_block(domain, app_port)
    if pattern.search(content):
        return pattern.sub(lambda _: block, content, count=1)
    if content and not content.endswith("\n"):
        content += "\n"
    return content + ("\n" if content.strip() else "") + block


def find_unmanaged_conflict(content: str, domain: str) -> bool:
    """Domen uchun abzctl markerisiz (qo'lda yozilgan) blok borligini aniqlaydi."""
    managed = re.sub(
        r"^# abzctl:begin .*?^# abzctl:end .*?$", "", content, flags=re.MULTILINE | re.DOTALL
    )
    site_re = re.compile(rf"^\s*(https?://)?{re.escape(domain)}(:\d+)?(\s*,[^{{]*)?\s*\{{", re.MULTILINE)
    return bool(site_re.search(managed))


def check_dns(domain: str) -> Optional[List[str]]:
    """Domen A-rekordlarini lokal kompyuterdan tekshiradi.

    Returns:
        Domen IP manzillari ro'yxati yoki None (domen umuman aniqlanmasa).
    """
    try:
        _, _, ips = socket.gethostbyname_ex(domain)
        return ips
    except OSError:
        return None


def resolve_host(host: str) -> str:
    """Server host nomini IP manzilga aylantiradi (DNS solishtirish uchun)."""
    try:
        return socket.gethostbyname(host)
    except OSError:
        return host


class CaddyManager:
    """Serverdagi Caddy konfiguratsiyasini boshqaradi."""

    def __init__(self, ssh: SSHClient):
        self.ssh = ssh

    def configure_site(self, domain: str, app_port: int) -> str:
        """Domen blokini Caddyfile'ga yozadi, tekshiradi va Caddy'ni reload qiladi.

        Returns:
            "added" — yangi blok qo'shildi, "updated" — mavjud blok yangilandi,
            "unchanged" — konfiguratsiya o'zgarmadi.
        """
        current = self.ssh.read_file(CADDYFILE, sudo=True) or ""

        if find_unmanaged_conflict(current, domain):
            raise CaddyError(
                f"{CADDYFILE} da '{domain}' uchun qo'lda yozilgan blok allaqachon mavjud.",
                hint="Takrorlanishning oldini olish uchun o'sha blokni o'chiring yoki tahrirlang, so'ng qayta urining.",
            )

        new = upsert_block(current, domain, app_port)
        if new == current:
            return "unchanged"
        action = "updated" if f"# abzctl:begin {domain}\n" in current else "added"

        # 1) Yangi konfiguratsiyani vaqtinchalik faylga yozib, tekshiramiz.
        tmp = f"/etc/caddy/.Caddyfile.abzctl-{uuid.uuid4().hex[:8]}"
        self.ssh.write_file(tmp, new, sudo=True)
        try:
            self.ssh.run(
                f"caddy validate --config {tmp} --adapter caddyfile",
                sudo=True,
                hint="Yangi Caddyfile noto'g'ri. Asl fayl o'zgartirilmadi.",
            )
        finally:
            self.ssh.run(f"rm -f {tmp}", sudo=True, check=False)

        # 2) Zaxira nusxa olib, asosiy faylni almashtiramiz.
        if current:
            self.ssh.run(f"cp -a {CADDYFILE} {BACKUP}", sudo=True)
        self.ssh.write_file(CADDYFILE, new, sudo=True)

        # 3) Reload; muvaffaqiyatsiz bo'lsa, zaxiradan tiklaymiz.
        try:
            self.reload()
        except RemoteCommandError as exc:
            if current:
                self.ssh.run(f"cp -a {BACKUP} {CADDYFILE}", sudo=True, check=False)
                self.ssh.run("systemctl reload caddy", sudo=True, check=False)
            raise CaddyError(
                "Caddy'ni reload qilib bo'lmadi, eski Caddyfile tiklandi.",
                hint="Serverda `journalctl -u caddy -n 50` ni tekshiring.",
            ) from exc
        return action

    def reload(self) -> None:
        """Caddy'ni uzilishsiz qayta yuklaydi (ishlamayotgan bo'lsa — ishga tushiradi)."""
        active = self.ssh.run("systemctl is-active --quiet caddy", sudo=True, check=False).ok
        cmd = "systemctl reload caddy" if active else "systemctl restart caddy"
        self.ssh.run(cmd, sudo=True)
