"""Deploy moduli: loyihani klonlash/yangilash va Docker Compose orqali ishga tushirish.

Ish tartibi:
1. Repo havolasidan loyiha nomini aniqlash (masalan `my-app`).
2. Serverda `/var/www/<project_name>` papkasini tayyorlash.
3. Papka bo'sh bo'lsa `git clone`, git repo bo'lsa `git pull` qilish.
4. Compose faylini topish va `docker compose up -d --build` ni bajarish.
5. Ilova `localhost:<app_port>` da javob berayotganini tekshirish.
"""

import re
import shlex
import time
from dataclasses import dataclass
from typing import Optional

from abzctl.core.ssh_client import LineCallback, SSHClient
from abzctl.exceptions import DeployError

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
    name = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-.")
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
            f"Noto'g'ri repo havolasi: {repo}",
            hint="Masalan: https://github.com/user/project.git yoki git@github.com:user/project.git",
        )


@dataclass
class DeployResult:
    project_name: str
    project_dir: str
    action: str  # "cloned" yoki "updated"
    commit: str
    compose_file: str
    app_healthy: Optional[bool]


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
        self.project_name = project_name or project_name_from_repo(repo)
        self.project_dir = f"{BASE_DIR}/{self.project_name}"

    @property
    def _dir(self) -> str:
        return shlex.quote(self.project_dir)

    def prepare_directory(self) -> None:
        """`/var/www/<project_name>` papkasini yaratadi va egasini ulangan foydalanuvchiga beradi."""
        user = shlex.quote(self.ssh.creds.user)
        self.ssh.run(
            f"mkdir -p {self._dir} && chown {user}: {self._dir}",
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

    def find_compose_file(self) -> str:
        """Loyiha ildizidagi compose faylini topadi."""
        for name in COMPOSE_FILES:
            if self.ssh.run(f"test -f {self._dir}/{name}", check=False).ok:
                return name
        raise DeployError(
            f"{self.project_dir} ichida compose fayli topilmadi.",
            hint=f"Loyiha ildizida quyidagilardan biri bo'lishi kerak: {', '.join(COMPOSE_FILES)}",
        )

    def compose_up(self, compose_file: str, on_line: Optional[LineCallback] = None) -> None:
        """`docker compose up -d --build` ni bajaradi."""
        self.ssh.run(
            f"cd {self._dir} && docker compose -f {shlex.quote(compose_file)} up -d --build --remove-orphans",
            sudo=True,
            on_line=on_line,
            hint="Docker image build yoki konteynerni ishga tushirish muvaffaqiyatsiz. Yuqoridagi logni ko'ring.",
        )

    def compose_ps(self, compose_file: str) -> str:
        res = self.ssh.run(
            f"cd {self._dir} && docker compose -f {shlex.quote(compose_file)} ps "
            "--format 'table {{.Service}}\t{{.State}}\t{{.Ports}}'",
            sudo=True,
            check=False,
        )
        return res.stdout.strip()

    def wait_for_app(self, app_port: int, attempts: int = 10, delay: float = 3.0) -> bool:
        """Ilova `localhost:<app_port>` da HTTP javob qaytarishini kutadi.

        Har qanday HTTP status (hatto 404) ilova ishlayotganini bildiradi.
        """
        for _ in range(attempts):
            res = self.ssh.run(
                f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 http://127.0.0.1:{app_port}/",
                check=False,
            )
            code = res.stdout.strip()
            if code and code != "000":
                return True
            time.sleep(delay)
        return False
