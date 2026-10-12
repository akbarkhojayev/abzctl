"""Serverni sozlash moduli.

Vazifalari:
- operatsion tizim Debian/Ubuntu ekanini va sudo huquqi borligini tekshirish;
- `curl`, `git` va yordamchi paketlarni o'rnatish;
- Docker (rasmiy get.docker.com skripti) va Docker Compose plaginini o'rnatish;
- Caddy'ni rasmiy Cloudsmith APT reposi orqali o'rnatish;
- Railpack va docker buildx'ni o'rnatish (Dockerfile'siz loyihalar uchun);
- RAM kam bo'lsa swap yaratish (build xotira yetmay yiqilmasligi uchun);
- `check-server` buyrug'i uchun server haqida ma'lumot yig'ish.

Barcha metodlar idempotent: dastur allaqachon o'rnatilgan bo'lsa, qayta o'rnatilmaydi.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional

from abzctl.core.ssh_client import LineCallback, SSHClient
from abzctl.exceptions import ProvisionError, RemoteCommandError

# dpkg qulfi boshqa jarayon (masalan unattended-upgrades) tomonidan band bo'lsa, 5 daqiqagacha kutamiz.
APT = "apt-get -o DPkg::Lock::Timeout=300 -y -q"

CADDY_GPG_URL = "https://dl.cloudsmith.io/public/caddy/stable/gpg.key"
CADDY_REPO_URL = "https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt"
CADDY_KEYRING = "/usr/share/keyrings/caddy-stable-archive-keyring.gpg"
CADDY_SOURCES = "/etc/apt/sources.list.d/caddy-stable.list"

RAILPACK_INSTALL_URL = "https://railpack.com/install.sh"


@dataclass
class OSInfo:
    """`/etc/os-release` dan olingan ma'lumot."""

    id: str
    id_like: str
    version: str
    pretty_name: str

    @property
    def is_debian_family(self) -> bool:
        return self.id in ("debian", "ubuntu") or "debian" in self.id_like or "ubuntu" in self.id_like


@dataclass
class ServerFacts:
    """`check-server` uchun yig'ilgan server holati."""

    hostname: str = "-"
    os_name: str = "-"
    kernel: str = "-"
    uptime: str = "-"
    cpu_cores: str = "-"
    memory: str = "-"
    disk: str = "-"
    is_root: bool = False
    sudo_ok: bool = False
    tools: Dict[str, Optional[str]] = field(default_factory=dict)


class Provisioner:
    """Serverga kerakli dasturlarni o'rnatuvchi klass."""

    def __init__(self, ssh: SSHClient):
        self.ssh = ssh
        self._apt_updated = False

    # ------------------------------------------------------------------ tekshiruvlar

    def detect_os(self) -> OSInfo:
        """OS turini aniqlaydi va Debian/Ubuntu oilasiga tegishli ekanini tekshiradi."""
        content = self.ssh.read_file("/etc/os-release") or ""
        data: Dict[str, str] = {}
        for line in content.splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                data[key.strip()] = value.strip().strip('"')
        info = OSInfo(
            id=data.get("ID", "unknown").lower(),
            id_like=data.get("ID_LIKE", "").lower(),
            version=data.get("VERSION_ID", ""),
            pretty_name=data.get("PRETTY_NAME", "Noma'lum OS"),
        )
        if not info.is_debian_family:
            raise ProvisionError(
                f"Qo'llab-quvvatlanmaydigan OS: {info.pretty_name}",
                hint="abzctl hozircha faqat Ubuntu va Debian serverlarini qo'llab-quvvatlaydi.",
            )
        return info

    def check_privileges(self) -> None:
        """Root yoki ishlaydigan sudo huquqi borligini tekshiradi."""
        if self.ssh.is_root:
            return
        result = self.ssh.run("true", sudo=True, check=False)
        if not result.ok:
            raise ProvisionError(
                f"'{self.ssh.creds.user}' foydalanuvchisida sudo huquqi yo'q yoki parol noto'g'ri.",
                hint=(
                    "root bilan ulaning, --password orqali sudo parolini bering yoki "
                    "foydalanuvchini sudo guruhiga qo'shing."
                ),
            )

    # ------------------------------------------------------------------ o'rnatish

    def _apt_update(self, on_line: Optional[LineCallback] = None) -> None:
        """`apt-get update` ni bir sessiyada faqat bir marta bajaradi."""
        if self._apt_updated:
            return
        self.ssh.run(
            f"{APT} update",
            sudo=True,
            on_line=on_line,
            hint="APT repozitoriylariga ulanib bo'lmadi. Serverning internetini tekshiring.",
        )
        self._apt_updated = True

    def _apt_install(self, packages: list, on_line: Optional[LineCallback] = None) -> None:
        self._apt_update(on_line)
        self.ssh.run(
            f"{APT} install --no-install-recommends {' '.join(packages)}",
            sudo=True,
            on_line=on_line,
            hint=f"Paketlarni o'rnatib bo'lmadi: {', '.join(packages)}",
        )

    def ensure_base_packages(self, on_line: Optional[LineCallback] = None) -> list:
        """curl, git, ca-certificates va gnupg paketlarini tekshiradi/o'rnatadi.

        Returns:
            Yangi o'rnatilgan paketlar ro'yxati (bo'sh bo'lsa — hammasi bor edi).
        """
        # paket nomi -> tekshiriladigan buyruq
        required = {"curl": "curl", "git": "git", "gnupg": "gpg"}
        missing = [pkg for pkg, binary in required.items() if not self.ssh.command_exists(binary)]
        if missing:
            self._apt_install(missing + ["ca-certificates"], on_line)
        return missing

    def ensure_docker(self, on_line: Optional[LineCallback] = None) -> bool:
        """Docker Engine o'rnatilganini tekshiradi, bo'lmasa get.docker.com orqali o'rnatadi.

        Returns:
            True — yangi o'rnatildi, False — avvaldan mavjud edi.
        """
        installed = False
        if not self.ssh.command_exists("docker"):
            self.ssh.run(
                "curl -fsSL https://get.docker.com -o /tmp/get-docker.sh && sh /tmp/get-docker.sh",
                sudo=True,
                on_line=on_line,
                hint="Docker o'rnatish skripti muvaffaqiyatsiz tugadi. Yuqoridagi chiqishni tekshiring.",
            )
            self.ssh.run("rm -f /tmp/get-docker.sh", sudo=True, check=False)
            installed = True

        # Docker xizmati yoqilgan va ishlayotgan bo'lishi kerak.
        self.ssh.run(
            "systemctl enable --now docker",
            sudo=True,
            on_line=on_line,
            hint="Docker xizmatini ishga tushirib bo'lmadi: `journalctl -u docker` ni tekshiring.",
        )
        return installed

    def ensure_compose(self, on_line: Optional[LineCallback] = None) -> bool:
        """`docker compose` (v2 plagin) mavjudligini tekshiradi, bo'lmasa o'rnatadi."""
        if self.ssh.run("docker compose version", sudo=True, check=False).ok:
            return False
        try:
            self._apt_install(["docker-compose-plugin"], on_line)
        except RemoteCommandError as exc:
            raise ProvisionError(
                "Docker Compose plaginini o'rnatib bo'lmadi.",
                hint="Docker rasmiy repodan o'rnatilganini tekshiring (get.docker.com).",
            ) from exc
        if not self.ssh.run("docker compose version", sudo=True, check=False).ok:
            raise ProvisionError("Docker Compose o'rnatildi, lekin `docker compose version` ishlamayapti.")
        return True

    def _add_caddy_repo(self, on_line: Optional[LineCallback] = None) -> bool:
        """Caddy'ning rasmiy (Cloudsmith) APT reposini qo'shadi.

        Returns:
            True — repo qo'shildi va `apt-get update` muvaffaqiyatli o'tdi.
            False — repo ishlamadi; buzilgan repo fayli o'chiriladi, aks holda
            keyingi barcha `apt-get update` buyruqlari ham yiqiladi.
        """
        try:
            self.ssh.run(
                f"curl -1sLf '{CADDY_GPG_URL}' | gpg --dearmor --yes -o {CADDY_KEYRING}"
                f" && curl -1sLf '{CADDY_REPO_URL}' > {CADDY_SOURCES}"
                f" && chmod o+r {CADDY_KEYRING} {CADDY_SOURCES}",
                sudo=True,
                on_line=on_line,
            )
            self._apt_updated = False
            self._apt_update(on_line)
            return True
        except RemoteCommandError:
            self.ssh.run(f"rm -f {CADDY_SOURCES} {CADDY_KEYRING}", sudo=True, check=False)
            self._apt_updated = False
            return False

    def ensure_caddy(self, on_line: Optional[LineCallback] = None) -> Optional[str]:
        """Caddy'ni rasmiy Debian/Ubuntu repozitoriysi orqali o'rnatadi.

        Rasmiy repo mavjud bo'lmasa (tarmoq/Cloudsmith xatosi), OS'ning o'z
        reposidagi `caddy` paketi o'rnatiladi (Debian 12+, Ubuntu 22.04+ da bor).

        Returns:
            None — Caddy avvaldan o'rnatilgan edi; aks holda o'rnatish manbai
            ("rasmiy Caddy reposi" yoki "OS reposi").
        """
        source: Optional[str] = None
        if not self.ssh.command_exists("caddy"):
            self._apt_install(
                ["debian-keyring", "debian-archive-keyring", "apt-transport-https", "curl", "gnupg"],
                on_line,
            )
            source = "rasmiy Caddy reposi" if self._add_caddy_repo(on_line) else "OS reposi"
            try:
                self._apt_install(["caddy"], on_line)
            except RemoteCommandError as exc:
                raise ProvisionError(
                    "Caddy'ni na rasmiy repodan, na OS reposidan o'rnatib bo'lmadi.",
                    hint="Caddy'ni qo'lda o'rnating: https://caddyserver.com/docs/install",
                ) from exc

        self.ssh.run(
            "systemctl enable --now caddy",
            sudo=True,
            on_line=on_line,
            hint="Caddy xizmatini ishga tushirib bo'lmadi: `journalctl -u caddy` ni tekshiring.",
        )
        return source

    def ensure_buildx(self, on_line: Optional[LineCallback] = None) -> bool:
        """`docker buildx` plaginini tekshiradi (Railpack build uchun kerak).

        get.docker.com uni o'zi o'rnatadi; Docker OS reposidan (docker.io) o'rnatilgan
        bo'lsa, plagin alohida paket sifatida o'rnatiladi.
        """
        if self.ssh.run("docker buildx version", sudo=True, check=False).ok:
            return False
        for package in ("docker-buildx-plugin", "docker-buildx"):
            try:
                self._apt_install([package], on_line)
                break
            except RemoteCommandError:
                continue
        if not self.ssh.run("docker buildx version", sudo=True, check=False).ok:
            raise ProvisionError(
                "docker buildx o'rnatib bo'lmadi.",
                hint="Docker'ni rasmiy skript bilan qayta o'rnating: curl -fsSL https://get.docker.com | sh",
            )
        return True

    def ensure_railpack(self, on_line: Optional[LineCallback] = None) -> bool:
        """Railpack CLI'ni (loyiha turini aniqlab, image yasovchi vosita) o'rnatadi/yangilaydi."""
        if self.ssh.command_exists("railpack"):
            return False
        self.ssh.run(
            f"set -o pipefail; curl -fsSL {RAILPACK_INSTALL_URL} | sh -s -- --yes; code=$?;"
            " rm -f /tmp/tmp.*-railpack.*.tar.gz; exit $code",
            sudo=True,
            on_line=on_line,
            hint="Railpack'ni o'rnatib bo'lmadi. Serverdan github.com ga kirish borligini tekshiring.",
        )
        if not self.ssh.command_exists("railpack"):
            raise ProvisionError("Railpack o'rnatildi, lekin `railpack` buyrug'i topilmadi.")
        return True

    def ensure_swap(self, min_memory_mb: int = 2048, swap_size: str = "2G") -> Optional[str]:
        """RAM kam bo'lgan serverda swap fayl yaratadi — aks holda build xotira yetmay yiqiladi.

        Bu majburiy emas: swap yaratib bo'lmasa (masalan konteyner/VPS cheklovi), deploy davom etadi.

        Returns:
            Yaratilgan swap hajmi yoki None (kerak emas / yaratib bo'lmadi).
        """
        meminfo = self.ssh.read_file("/proc/meminfo") or ""
        values: Dict[str, int] = {}
        for line in meminfo.splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                values[key] = int(parts[0]) // 1024  # kB -> MB
        if values.get("MemTotal", min_memory_mb) >= min_memory_mb or values.get("SwapTotal", 0) > 0:
            return None
        result = self.ssh.run(
            f"[ -f /swapfile ] || (fallocate -l {swap_size} /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048)"
            " && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile"
            " && (grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab)",
            sudo=True,
            check=False,
        )
        if not result.ok:
            self.ssh.run("swapoff /swapfile 2>/dev/null; rm -f /swapfile", sudo=True, check=False)
            return None
        return swap_size

    def open_firewall_ports(self, on_line: Optional[LineCallback] = None) -> bool:
        """UFW faol bo'lsa, HTTP/HTTPS (80, 443) portlarini ochadi.

        Returns:
            True — qoidalar qo'shildi, False — UFW yo'q yoki faol emas.
        """
        if not self.ssh.command_exists("ufw"):
            return False
        status = self.ssh.run("ufw status", sudo=True, check=False)
        if "Status: active" not in status.stdout:
            return False
        self.ssh.run("ufw allow 80/tcp && ufw allow 443/tcp", sudo=True, on_line=on_line)
        return True

    # ------------------------------------------------------------------ check-server

    def gather_facts(self) -> ServerFacts:
        """Server haqida umumiy ma'lumot va o'rnatilgan dasturlar versiyalarini yig'adi."""
        facts = ServerFacts()

        def q(cmd: str, sudo: bool = False) -> str:
            res = self.ssh.run(cmd, sudo=sudo, check=False)
            return res.stdout.strip() if res.ok and res.stdout.strip() else "-"

        facts.hostname = q("hostname")
        facts.os_name = q(". /etc/os-release && echo \"$PRETTY_NAME\"")
        facts.kernel = q("uname -r")
        facts.uptime = q("uptime -p")
        facts.cpu_cores = q("nproc")
        facts.memory = q("free -h | awk '/^Mem:/ {print $3 \" / \" $2 \" ishlatilgan\"}'")
        facts.disk = q("df -h / | awk 'NR==2 {print $3 \" / \" $2 \" (\" $5 \")\"}'")
        facts.is_root = self.ssh.is_root
        facts.sudo_ok = facts.is_root or self.ssh.run("true", sudo=True, check=False).ok

        version_cmds = {
            "curl": "curl --version | head -n1 | awk '{print $2}'",
            "git": "git --version | awk '{print $3}'",
            "docker": "docker --version | sed 's/Docker version //; s/,.*//'",
            "docker compose": "docker compose version --short",
            "caddy": "caddy version | awk '{print $1}'",
        }
        for name, cmd in version_cmds.items():
            # docker compose root bo'lmagan foydalanuvchida docker guruhisiz ishlamasligi mumkin.
            sudo = name.startswith("docker") and facts.sudo_ok
            value = q(cmd, sudo=sudo)
            facts.tools[name] = None if value == "-" else value

        for service in ("docker", "caddy"):
            if facts.tools.get(service):
                # `is-active` faol bo'lmagan xizmat uchun exit code != 0 qaytaradi, shuning uchun `|| true`.
                state = q(f"systemctl is-active {service} || true")
                facts.tools[service] = f"{facts.tools[service]} ({state})"
        return facts
