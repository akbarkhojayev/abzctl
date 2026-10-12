"""Deploy moduli: loyihani klonlash/yangilash va Docker Compose orqali ishga tushirish.

Ish tartibi:
1. Repo havolasidan loyiha nomini aniqlash (masalan `my-app`).
2. Serverda `/var/www/<project_name>` papkasini tayyorlash.
3. Papka bo'sh bo'lsa `git clone`, git repo bo'lsa `git pull` qilish.
4. `docker compose up -d --build` ni bajarish (compose faylini `analyzer.py` tayyorlaydi).
5. Ilova `localhost:<app_port>` da javob berayotganini tekshirish.
"""

import json
import re
import shlex
import time
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

from abzctl.core.ssh_client import LineCallback, SSHClient
from abzctl.exceptions import DeployError
from abzctl.security import public_repo_url, redact_urls

BASE_DIR = "/var/www"
COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")


def project_name_from_repo(repo: str) -> str:
    """Repo havolasidan xavfsiz papka nomini chiqaradi.

    >>> project_name_from_repo("https://github.com/user/My-App.git")
    'my-app'
    >>> project_name_from_repo("git@github.com:user/api.git")
    'api'
    """
    name = repo.rstrip("/").split("/")[-1].split(":")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    # Faqat xavfsiz belgilarni qoldiramiz — bu nom shell buyruqlarida va yo'llarda ishlatiladi.
    name = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")[:63]
    if not name:
        raise DeployError(
            f"Repo havolasidan loyiha nomini aniqlab bo'lmadi: {repo}",
            hint="--name parametri orqali loyiha nomini qo'lda bering.",
        )
    return name


def validate_repo_url(repo: str) -> None:
    """Repo havolasi qo'llab-quvvatlanadigan formatda ekanini tekshiradi."""
    if not re.match(r"^(https?://|git@|ssh://)[^\s'\"`;|&$]+$", repo):
        raise DeployError(
            f"Noto'g'ri repo havolasi: {redact_urls(repo)}",
            hint="Masalan: https://github.com/user/project.git yoki git@github.com:user/project.git",
        )
    try:
        parts = urlsplit(repo) if "://" in repo else None
        credential_url = public_repo_url(repo) != repo
    except ValueError as exc:
        raise DeployError("Noto'g'ri repo havolasi.") from exc
    if parts and (not parts.hostname or not parts.path or parts.query or parts.fragment):
        raise DeployError("Repo havolasi host va loyiha yo'lini ko'rsatsin; query yoki fragment ishlatmang.")
    if credential_url:
        raise DeployError("Repo havolasida token yoki parol ishlatmang.",
                          hint="Private repo uchun serverda SSH deploy key sozlang va git@... havolasini bering.")


def validate_project_name(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", name):
        raise DeployError("Loyiha nomi 1–63 belgi: kichik harf, raqam, _ yoki -; harf/raqam bilan boshlansin.")
    return name


class Deployer:
    """Bitta loyihani serverga deploy qiluvchi klass."""

    def __init__(
        self,
        ssh: SSHClient,
        repo: str,
        branch: Optional[str] = None,
        project_name: Optional[str] = None,
    ):
        validate_repo_url(repo)
        self.ssh = ssh
        self.repo = repo
        self.branch = branch
        self.project_name = validate_project_name(project_name or project_name_from_repo(repo))
        self.project_dir = f"{BASE_DIR}/{self.project_name}"

    @property
    def _dir(self) -> str:
        return shlex.quote(self.project_dir)

    def prepare_directory(self) -> None:
        """`/var/www/<project_name>` papkasini yaratadi va egasini ulangan foydalanuvchiga beradi."""
        user = shlex.quote(self.ssh.creds.user)
        self.ssh.run(
            f"test ! -L {self._dir} && mkdir -p {self._dir} && chown {user}: {self._dir}",
            sudo=True,
            hint=f"{self.project_dir} papkasini yaratib bo'lmadi.",
        )

    def sync_code(self, on_line: Optional[LineCallback] = None) -> str:
        """Loyihani klonlaydi yoki mavjud bo'lsa yangilaydi.

        Returns:
            "cloned" yoki "updated".
        """
        is_repo = self.ssh.run(f"test -d {self._dir}/.git", check=False).ok
        branch_flag = f"--branch {shlex.quote(self.branch)} " if self.branch else ""
        # GIT_TERMINAL_PROMPT=0 — yopiq repo uchun git login so'rab qotib qolmasligi uchun.
        git = "GIT_TERMINAL_PROMPT=0 git"

        if is_repo:
            remote = self.ssh.run(f"git -C {self._dir} remote get-url origin", check=False).stdout.strip()
            if remote and remote != self.repo:
                raise DeployError(
                    f"{self.project_dir} papkasida boshqa repo joylashgan: {remote}",
                    hint="--name orqali boshqa loyiha nomini bering yoki papkani serverda o'chiring.",
                )
            if self.branch:
                self.ssh.run(
                    f"{git} -C {self._dir} fetch --progress origin {shlex.quote(self.branch)}"
                    f" && git -C {self._dir} checkout {shlex.quote(self.branch)}",
                    on_line=on_line,
                )
            self.ssh.run(
                f"{git} -C {self._dir} pull --ff-only --progress",
                on_line=on_line,
                hint=(
                    "git pull muvaffaqiyatsiz. Serverdagi papkada lokal o'zgarishlar bo'lishi mumkin — "
                    f"`git -C {self.project_dir} status` ni tekshiring."
                ),
            )
            return "updated"

        # Papka mavjud, lekin git repo emas va bo'sh emas — ma'lumotni buzmaslik uchun to'xtaymiz.
        not_empty = self.ssh.run(f'[ -n "$(ls -A {self._dir} 2>/dev/null)" ]', check=False).ok
        if not_empty:
            raise DeployError(
                f"{self.project_dir} papkasi bo'sh emas va git repozitoriy emas.",
                hint="Papkani serverda tozalang yoki --name orqali boshqa nom tanlang.",
            )
        self.ssh.run(
            f"{git} clone --progress {branch_flag}{shlex.quote(self.repo)} {self._dir}",
            on_line=on_line,
            hint=(
                "Repo'ni klonlab bo'lmadi. Havola to'g'riligini tekshiring. Yopiq (private) repo uchun "
                "serverda deploy key sozlang yoki token bilan HTTPS havola ishlating."
            ),
        )
        return "cloned"

    def current_commit(self) -> str:
        res = self.ssh.run(f"git -C {self._dir} log -1 --format='%h — %s'", check=False)
        return res.stdout.strip() or "-"

    def _compose(self, compose_file: str, args: str) -> str:
        """`docker compose` buyrug'ini loyiha nomi (-p) va fayli (-f) bilan yasaydi.

        Loyiha nomi har doim aniq beriladi: generatsiya qilingan compose fayli
        `.abzctl/` papkasida turganda ham konteynerlar to'g'ri nomlanadi.
        """
        return (
            f"cd {self._dir} && docker compose -p {shlex.quote(self.project_name)} "
            f"-f {shlex.quote(compose_file)} {args}"
        )

    def find_compose_file(self) -> Optional[str]:
        """Loyiha ildizidagi compose faylini topadi (yo'q bo'lsa None)."""
        for name in COMPOSE_FILES:
            if self.ssh.run(f"test -f {self._dir}/{name}", check=False).ok:
                return name
        return None

    def published_ports(self, compose_file: str) -> List[Tuple[str, int]]:
        """Compose faylida tashqariga ochilgan (publish qilingan) portlarni aniqlaydi.

        `docker compose config` compose faylini to'liq tahlil qiladi (o'zgaruvchilar,
        qisqa/uzun port sintaksisi), shuning uchun YAML'ni o'zimiz o'qimaymiz.

        Returns:
            (servis_nomi, port) juftliklari ro'yxati. Aniqlab bo'lmasa — bo'sh ro'yxat.
        """
        res = self.ssh.run(self._compose(compose_file, "config --format json"), sudo=True, check=False)
        if not res.ok:
            return []
        try:
            services = json.loads(res.stdout).get("services", {})
        except ValueError:
            return []
        ports: List[Tuple[str, int]] = []
        for service, spec in services.items():
            for entry in spec.get("ports") or []:
                published = str(entry.get("published", "")).split("-")[0]
                if published.isdigit() and entry.get("protocol", "tcp") == "tcp":
                    ports.append((service, int(published)))
        return ports

    def compose_up(self, compose_file: str, on_line: Optional[LineCallback] = None) -> None:
        """`docker compose up -d --build` ni bajaradi."""
        self.ssh.run(self._compose(compose_file, "config --quiet"), sudo=True,
                     hint="Compose konfiguratsiyasi yoki .env qiymatlarini tekshiring.")
        self.ssh.run(
            self._compose(compose_file, "up -d --build --remove-orphans"),
            sudo=True,
            on_line=on_line,
            hint="Docker image build yoki konteynerni ishga tushirish muvaffaqiyatsiz. Yuqoridagi logni ko'ring.",
        )

    def compose_logs(self, compose_file: str, tail: int = 30) -> str:
        """Konteynerlarning oxirgi loglarini qaytaradi (ilova ishlamay qolganda ko'rsatish uchun)."""
        res = self.ssh.run(
            self._compose(compose_file, f"logs --no-color --tail {tail}"), sudo=True, check=False
        )
        return res.output

    def wait_for_app(self, app_port: int, attempts: int = 20, delay: float = 3.0) -> bool:
        """Ilova `localhost:<app_port>` da HTTP javob qaytarishini kutadi (~1 daqiqagacha).

        Har qanday HTTP status (hatto 404) ilova ishlayotganini bildiradi.
        """
        for _ in range(attempts):
            res = self.ssh.run(
                f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 http://127.0.0.1:{app_port}/",
                check=False,
            )
            code = res.stdout.strip()
            if res.ok and re.fullmatch(r"[1-5][0-9]{2}", code):
                return True
            time.sleep(delay)
        return False

    def wait_for_running(self, compose_file: str, service: str = "app", checks: int = 3, delay: float = 5.0) -> bool:
        """Fon jarayoni (bot) uchun tekshiruv: konteyner ishlab turibdi va qayta-qayta yiqilmayapti.

        HTTP port bo'lmagani uchun konteyner holati bir necha marta tekshiriladi.
        """
        for _ in range(checks):
            time.sleep(delay)
            res = self.ssh.run(
                f"docker inspect -f '{{{{.State.Running}}}} {{{{.RestartCount}}}}' "
                f"$({self._compose(compose_file, f'ps -q {shlex.quote(service)}')})",
                sudo=True,
                check=False,
            )
            parts = res.stdout.split()
            if not res.ok or len(parts) != 2 or parts[0] != "true" or parts[1] != "0":
                return False
        return True
