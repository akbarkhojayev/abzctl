"""Loyiha tahlilchisi: loyihani qanday build qilib ishga tushirishni o'zi aniqlaydi.

Aniqlash tartibi (birinchi mos kelgani tanlanadi):

1. `compose.yaml` / `docker-compose.yml` bor  → loyihaning o'z compose fayli ishlatiladi.
2. `Dockerfile` bor                            → compose faylini abzctl o'zi yaratadi.
3. Ikkalasi ham yo'q                           → Railpack loyiha turini (Node, Python, Go,
   PHP, Java, Ruby, Rust, .NET, statik sayt, ...) aniqlab, image'ni koddan o'zi yasaydi.

Kerak bo'lsa PostgreSQL / MySQL / MongoDB / Redis ham qo'shiladi (`services.py`).

abzctl yaratgan fayllar loyiha ichidagi `.abzctl/` papkasida turadi va git'ga
qo'shilmaydi (`.git/info/exclude`), shuning uchun `git pull` ga xalaqit bermaydi.
"""

import json
import re
import secrets
import shlex
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from abzctl.core import codescan
from abzctl.core import services as svc
from abzctl.core.deployer import COMPOSE_FILES, Deployer
from abzctl.core.ssh_client import LineCallback, SSHClient
from abzctl.exceptions import DeployError

WORK_DIR = ".abzctl"
GENERATED_COMPOSE = f"{WORK_DIR}/compose.yaml"
STATE_FILE = f"{WORK_DIR}/state.json"
SERVICES_FILE = f"{WORK_DIR}/services.json"  # baza parollari (faqat egasi o'qiy oladi)
RAILPACK_PLAN = f"{WORK_DIR}/railpack-plan.json"
RAILPACK_INFO = f"{WORK_DIR}/railpack-info.json"
RAILPACK_FRONTEND = "ghcr.io/railwayapp/railpack-frontend"

DOCKERFILES = ("Dockerfile", "dockerfile")
ENV_EXAMPLES = (".env.example", ".env.sample", ".env.template", ".env.dist")

# Railpack provayder nomlari -> foydalanuvchiga ko'rinadigan nom
PROVIDER_NAMES = {
    "node": "Node.js", "python": "Python", "golang": "Go", "php": "PHP", "java": "Java",
    "ruby": "Ruby", "rust": "Rust", "dotnet": ".NET", "deno": "Deno", "elixir": "Elixir",
    "gleam": "Gleam", "cpp": "C/C++", "staticfile": "Statik sayt", "shell": "Shell skript",
}
RUNTIME_NAMES = {
    "next": "Next.js", "nuxt": "Nuxt", "remix": "Remix", "astro": "Astro", "vite": "Vite",
    "react-router": "React Router", "tanstack-start": "TanStack Start", "angular": "Angular",
    "django": "Django", "flask": "Flask", "fastapi": "FastAPI", "laravel": "Laravel",
    "rails": "Rails", "spring-boot": "Spring Boot",
}

_SECRET_RE = re.compile(r"SECRET|PASSWORD|PASSWD|TOKEN|SALT|PRIVATE|_KEY$|^KEY$", re.IGNORECASE)
# Tasodifiy qiymat yaratsa bo'ladigan maxfiy kalitlar. BOT_TOKEN, API_KEY kabilar tashqi
# servisdan olinadi — ularni yaratib bo'lmaydi, foydalanuvchi kiritishi shart.
_GENERATABLE_RE = re.compile(r"SECRET|SALT|SESSION|SIGNING|ENCRYPT|JWT|APP_KEY", re.IGNORECASE)
_REQUIRED_RE = re.compile(r"TOKEN|API_KEY|_KEY$|^KEY$|API_ID|API_HASH", re.IGNORECASE)
PYTHON_DEP_FILES = ("requirements.txt", "pyproject.toml", "Pipfile", "setup.py")
_PLACEHOLDER_RE = re.compile(r"^$|^(change|your|xxx|replace|todo|secret|example)", re.IGNORECASE)


@dataclass
class ProjectPlan:
    """Loyihani qanday build qilish va ishga tushirish rejasi."""

    kind: str                      # "compose" | "dockerfile" | "railpack"
    label: str                     # foydalanuvchiga ko'rsatiladigan tur, masalan "Python (Flask)"
    compose_file: str = ""         # loyiha papkasiga nisbatan compose fayl yo'li
    container_port: Optional[int] = None
    details: List[str] = field(default_factory=list)

    @property
    def generated(self) -> bool:
        """Compose faylini abzctl yaratadimi (loyihada o'zining compose fayli yo'q)."""
        return self.kind != "compose"


@dataclass
class EnvVar:
    name: str
    default: str
    secret: bool           # kiritilganda ekranda ko'rsatilmaydi
    required: bool = False  # bo'sh qoldirib bo'lmaydi (masalan BOT_TOKEN)
    from_code: bool = False  # .env.example'dan emas, koddan topilgan (default koddagi qiymat)


class ProjectAnalyzer:
    """Serverdagi loyiha papkasini tahlil qiladi va ishga tushirishga tayyorlaydi."""

    def __init__(self, ssh: SSHClient, deployer: Deployer):
        self.ssh = ssh
        self.deployer = deployer
        self.dir = deployer.project_dir
        self._q = shlex.quote(self.dir)
        self._sources: Optional[Dict[str, str]] = None

    # ------------------------------------------------------------------ yordamchilar

    def _exists(self, rel_path: str) -> bool:
        return self.ssh.run(f"test -f {self._q}/{shlex.quote(rel_path)}", check=False).ok

    def _read(self, rel_path: str) -> Optional[str]:
        return self.ssh.read_file(f"{self.dir}/{rel_path}")

    def _prepare_work_dir(self) -> None:
        """`.abzctl/` papkasini yaratadi va uni git'dan yashiradi."""
        self.ssh.run(
            f"mkdir -p {self._q}/{WORK_DIR} && "
            f"(grep -qxF '{WORK_DIR}/' {self._q}/.git/info/exclude 2>/dev/null || "
            f"echo '{WORK_DIR}/' >> {self._q}/.git/info/exclude)"
        )

    # ------------------------------------------------------------------ 1. turini aniqlash

    def detect(self) -> ProjectPlan:
        """Loyiha turini aniqlaydi (Railpack tahlilisiz — u alohida `railpack_prepare` da)."""
        for name in COMPOSE_FILES:
            if self._exists(name):
                return ProjectPlan(kind="compose", label=f"Docker Compose ({name})", compose_file=name)

        for name in DOCKERFILES:
            if self._exists(name):
                return ProjectPlan(
                    kind="dockerfile",
                    label="Dockerfile",
                    compose_file=GENERATED_COMPOSE,
                    container_port=self._exposed_port(name),
                    details=[name],
                )

        return ProjectPlan(kind="railpack", label="", compose_file=GENERATED_COMPOSE)

    def _exposed_port(self, dockerfile: str) -> Optional[int]:
        """Dockerfile'dagi birinchi `EXPOSE <port>` qatoridan portni oladi."""
        content = self._read(dockerfile) or ""
        # EXPOSE in a build stage does not describe the final runtime image.
        stages = list(re.finditer(r"^\s*FROM\s+", content, re.MULTILINE | re.IGNORECASE))
        if stages:
            content = content[stages[-1].start():]
        match = re.search(r"^\s*EXPOSE\s+(\d+)", content, re.MULTILINE | re.IGNORECASE)
        port = int(match.group(1)) if match else None
        return port if port and 1 <= port <= 65535 else None

    def railpack_prepare(self, on_line: Optional[LineCallback] = None) -> ProjectPlan:
        """Railpack bilan loyihani tahlil qiladi va build rejasini tayyorlaydi."""
        self._prepare_work_dir()
        # Railpack o'z keshini /tmp/railpack da saqlaydi. Uni har doim root nomidan ishga
        # tushiramiz — aks holda turli foydalanuvchilar deploy qilganda kesh papkasiga
        # yozish huquqi bo'lmay qoladi ("failed to create mutex: permission denied").
        user = shlex.quote(self.ssh.creds.user)
        res = self.ssh.run(
            f"cd {self._q} && railpack prepare . --plan-out {RAILPACK_PLAN} --info-out {RAILPACK_INFO};"
            f" code=$?; chown -R {user}: {WORK_DIR}; exit $code",
            sudo=True,
            check=False,
            on_line=on_line,
        )
        info: Dict = {}
        try:
            info = json.loads(self._read(RAILPACK_INFO) or "{}")
        except ValueError:
            pass

        if not res.ok or not info.get("success"):
            errors = [log.get("Msg", "") for log in info.get("logs", []) if log.get("Level") == "error"]
            reason = errors[0].split("\n")[0] if errors else "Railpack loyiha turini aniqlay olmadi."
            raise DeployError(
                f"Loyihani qanday ishga tushirishni aniqlab bo'lmadi.\n{reason}",
                hint=(
                    "Repo ildizida loyiha fayllari (package.json, requirements.txt, go.mod, index.html va h.k.) "
                    "borligini tekshiring, yoki repo'ga Dockerfile qo'shing."
                ),
            )

        providers = info.get("detectedProviders") or []
        metadata = info.get("metadata") or {}
        provider = providers[0] if providers else "app"
        label = PROVIDER_NAMES.get(provider, provider.capitalize())
        runtime = metadata.get(f"{provider}Runtime")
        if runtime and runtime != provider:
            label += f" ({RUNTIME_NAMES.get(runtime, runtime)})"

        details = []
        for pkg, data in (info.get("resolvedPackages") or {}).items():
            if data.get("resolvedVersion"):
                details.append(f"{pkg} {data['resolvedVersion']}")
        return ProjectPlan(kind="railpack", label=label, compose_file=GENERATED_COMPOSE, details=details)

    # ------------------------------------------------------------------ manba kodi

    def sources(self) -> Dict[str, str]:
        """Loyihadagi Python/JS/TS fayllari matni (bir marta o'qiladi, katta loyihalarda cheklangan)."""
        if self._sources is not None:
            return self._sources
        marker = "=====abzctl-file:"
        res = self.ssh.run(
            f"cd {self._q} && find . -maxdepth 3 -type f "
            r"\( -name '*.py' -o -name '*.js' -o -name '*.mjs' -o -name '*.cjs' -o -name '*.ts' \) "
            "-not -path '*/node_modules/*' -not -path '*/.venv/*' -not -path '*/venv/*' "
            "-not -path './.git/*' -not -path './.abzctl/*' -not -name '*.min.js' -not -name '*.d.ts' "
            f"| head -n 150 | while read -r f; do echo \"{marker}$f\"; head -c 200000 \"$f\"; echo; done",
            check=False,
        )
        sources: Dict[str, str] = {}
        current = None
        for line in res.stdout.splitlines():
            if line.startswith(marker):
                current = line[len(marker):].removeprefix("./")
                sources[current] = ""
            elif current is not None:
                sources[current] += line + "\n"
        self._sources = sources
        return sources

    def ensure_python_requirements(self) -> Optional[List[str]]:
        """Python loyihada dependency fayli yo'q bo'lsa, importlardan `requirements.txt` yaratadi.

        Masalan faqat `main.py` dan iborat Telegram bot uchun `aiogram`, `python-dotenv` va h.k.
        abzctl yaratgan fayl git'dan yashiriladi; repo'ga o'zingiz requirements.txt qo'shsangiz,
        o'shanisi ishlatiladi.

        Returns:
            Yozilgan paketlar ro'yxati yoki None (fayl kerak emas / allaqachon bor).
        """
        existing = self._read("requirements.txt")
        own_file = existing is not None and not existing.startswith(codescan.GENERATED_MARKER)
        if own_file or any(self._exists(name) for name in PYTHON_DEP_FILES[1:]):
            return None
        sources = self.sources()
        if not any(path.endswith(".py") for path in sources):
            return None
        packages = codescan.infer_requirements(sources)
        self.ssh.write_file(f"{self.dir}/requirements.txt", codescan.render_requirements(packages))
        self.ssh.run(
            f"grep -qxF 'requirements.txt' {self._q}/.git/info/exclude 2>/dev/null || "
            f"echo 'requirements.txt' >> {self._q}/.git/info/exclude",
            check=False,
        )
        return packages

    def worker_library(self) -> Optional[str]:
        """Loyiha bot / fon jarayoni bo'lsa (HTTP port ochmaydi), uning kutubxonasi nomi."""
        node_deps = set()
        try:
            data = json.loads(self._read("package.json") or "{}")
            node_deps = set((data.get("dependencies") or {}))
        except ValueError:
            pass
        return codescan.is_worker(self.sources(), node_deps)

    # ------------------------------------------------------------------ 2. ma'lumotlar bazasi

    def detect_services(self) -> List[svc.DetectedService]:
        """Loyiha kutubxonalariga qarab kerakli bazalarni aniqlaydi."""
        files: Dict[str, str] = {}
        for name in svc.DEPENDENCY_FILES + svc.PRISMA_SCHEMAS + ENV_EXAMPLES + (".env",):
            content = self._read(name)
            if content is not None:
                files[name] = content
        return svc.detect_services(files)

    def saved_services(self) -> Dict[str, Dict[str, str]]:
        """Oldingi deploy'da yaratilgan bazalar va ularning parollari.

        Parol qayta yaratilmasligi shart: baza birinchi ishga tushganda parol volume'ga
        yozib qo'yiladi va keyin o'zgartirilsa, ilova bazaga ulana olmay qoladi.
        """
        return svc.parse_services(self._read(SERVICES_FILE))

    def save_services(self, services: Dict[str, Dict[str, str]]) -> None:
        self._prepare_work_dir()
        self.ssh.write_file(f"{self.dir}/{SERVICES_FILE}", json.dumps(services, indent=2) + "\n", mode="600")

    def resolve_services(self, enabled: List[str], declined: List[str]) -> Dict[str, Dict[str, str]]:
        """Tanlangan servislar uchun hisob ma'lumotlarini qaytaradi (saqlanganlarini qayta ishlatadi).

        Rad etilgan servislar ham eslab qolinadi, shunda keyingi deploy'da qayta so'ralmaydi.
        """
        saved = self.saved_services()
        db_name = svc.safe_db_name(self.deployer.project_name)
        result: Dict[str, Dict[str, str]] = {}
        for kind in enabled:
            previous = saved.get(kind)
            result[kind] = previous if previous and not previous.get("disabled") else svc.new_credentials(kind, db_name)
        stored = {**result, **{kind: {"disabled": True} for kind in declined}}
        if stored:
            self.save_services(stored)
        return result

    # ------------------------------------------------------------------ 3. .env

    def missing_env(self) -> Tuple[Optional[str], List[EnvVar]]:
        """`.env.example` bor, lekin `.env` yo'q bo'lsa, to'ldirilishi kerak bo'lgan o'zgaruvchilar.

        Returns:
            (namuna fayl nomi, o'zgaruvchilar ro'yxati). `.env` allaqachon bo'lsa — (None, []).
        """
        if self._exists(".env"):
            return None, []
        for name in ENV_EXAMPLES:
            content = self._read(name)
            if content is None:
                continue
            variables = []
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.removeprefix("export ").partition("=")
                key = key.strip()
                value = value.split(" #")[0].strip().strip("'\"")
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                    required = not value and bool(_REQUIRED_RE.search(key))
                    variables.append(EnvVar(key, value, bool(_SECRET_RE.search(key)), required))
            return name, variables

        # Namuna fayl yo'q — o'zgaruvchilarni koddan qidiramiz (os.getenv("BOT_TOKEN") va h.k.).
        code_vars = codescan.env_vars_from_code(self.sources())
        if not code_vars:
            return None, []
        variables = [
            EnvVar(
                v.name,
                v.default or "",
                secret=bool(_SECRET_RE.search(v.name)),
                required=v.default is None and bool(_REQUIRED_RE.search(v.name)),
                from_code=True,
            )
            for v in code_vars
        ]
        return "kod", variables

    @staticmethod
    def suggest_value(var: EnvVar) -> str:
        """`SECRET_KEY` kabi kalit namunasi bo'sh yoki "change-me" bo'lsa, tasodifiy qiymat taklif qiladi."""
        if _GENERATABLE_RE.search(var.name) and _PLACEHOLDER_RE.match(var.default):
            return secrets.token_urlsafe(32)
        return var.default

    def write_env(self, values: Dict[str, str], preserve: bool = False) -> None:
        """Serverda `.env` faylini yozadi (faqat egasi o'qiy oladi)."""
        lines = ["# abzctl tomonidan yaratilgan. Qiymatlarni serverda tahrirlashingiz mumkin."]
        if preserve:
            lines.insert(0, (self._read(".env") or "").rstrip())
        for key, value in values.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or "\x00" in value:
                raise DeployError("Noto'g'ri muhit o'zgaruvchisi.")
            # Escape Compose interpolation as well as dotenv string syntax.
            value = (value.replace("\\", "\\\\").replace('"', '\\"')
                     .replace("$", "$$").replace("\n", "\\n").replace("\r", "\\r"))
            value = '"' + value + '"'
            lines.append(f"{key}={value}")
        self.ssh.write_file(f"{self.dir}/.env", "\n".join(lines) + "\n", mode="600")
        # Serverda yaratilgan .env `git status` da ko'rinmasligi uchun.
        self.ssh.run(
            f"grep -qxF '.env' {self._q}/.git/info/exclude 2>/dev/null || echo '.env' >> {self._q}/.git/info/exclude",
            check=False,
        )

    # ------------------------------------------------------------------ 4. port

    def previous_port(self) -> Optional[int]:
        """Oldingi deploy'da tanlangan portni o'qiydi (port har safar o'zgarib ketmasligi uchun)."""
        try:
            port = json.loads(self._read(STATE_FILE) or "{}").get("port")
            return int(port) if port and 1 <= int(port) <= 65535 else None
        except (ValueError, TypeError, AttributeError):
            return None

    def free_port(self, start: int = 3000) -> int:
        """Serverda band bo'lmagan birinchi TCP portni topadi (`start` dan boshlab)."""
        used = set()
        for path in ("/proc/net/tcp", "/proc/net/tcp6"):
            for line in (self.ssh.read_file(path) or "").splitlines()[1:]:
                parts = line.split()
                if len(parts) > 3 and parts[3] == "0A":  # 0A = LISTEN
                    used.add(int(parts[1].rsplit(":", 1)[1], 16))
        port = start
        while port in used:
            port += 1
        if not 1 <= port <= 65535:
            raise DeployError("Bo'sh TCP port topilmadi.")
        return port

    # ------------------------------------------------------------------ 5. build va compose

    def railpack_build(self, on_line: Optional[LineCallback] = None) -> str:
        """Railpack rejasi asosida Docker image yasaydi. Image nomini qaytaradi."""
        image = f"abzctl/{self.deployer.project_name}:latest"
        self.ssh.run(
            f"cd {self._q} && docker buildx build --progress plain "
            f"--build-arg BUILDKIT_SYNTAX={RAILPACK_FRONTEND} "
            f"-f {RAILPACK_PLAN} -t {shlex.quote(image)} --load .",
            sudo=True,
            on_line=on_line,
            hint="Build muvaffaqiyatsiz. Yuqoridagi logda xatoni ko'ring (to'liq log: -v).",
        )
        return image

    def write_compose(
        self,
        plan: ProjectPlan,
        host_port: int,
        public: bool,
        image: Optional[str] = None,
        services: Optional[Dict[str, Dict[str, str]]] = None,
        worker: bool = False,
    ) -> None:
        """Dockerfile yoki Railpack loyihasi uchun compose faylini yaratadi.

        `worker=True` — bot / fon jarayoni: port ochilmaydi.
        `services` berilsa, bazalar alohida konteynerlarda qo'shiladi va ilova ular
        tayyor bo'lgandan keyingina (healthcheck) ishga tushadi.
        """
        self._prepare_work_dir()
        services = services or {}
        container_port = plan.container_port or host_port
        bind = "" if public else "127.0.0.1:"

        lines = [
            "# abzctl tomonidan avtomatik yaratilgan. Har deploy'da qayta yoziladi.",
            "services:",
            "  app:",
        ]
        if image:
            lines.append(f"    image: {image}")
        else:
            lines += ["    build:", "      context: ..", f"      dockerfile: {plan.details[0]}"]

        port_env = {} if worker else {"PORT": str(container_port)}
        environment = {**port_env, **svc.app_environment(services)}
        if environment:
            lines.append("    environment:")
            lines += [f"      {key}: {json.dumps(value)}" for key, value in environment.items()]

        if self._exists(".env"):
            lines += ["    env_file:", "      - ../.env"]
        if services:
            lines.append("    depends_on:")
            for kind in svc.SERVICE_ORDER:
                if kind in services:
                    lines += [f"      {kind}:", "        condition: service_healthy"]
        if not worker:  # bot / fon jarayoni port ochmaydi
            lines += ["    ports:", f'      - "{bind}{host_port}:{container_port}"']
        lines.append("    restart: unless-stopped")

        lines += svc.compose_blocks(services)
        volumes = svc.volume_names(services)
        if volumes:
            lines.append("volumes:")
            lines += [f"  {name}:" for name in volumes]

        self.ssh.write_file(f"{self.dir}/{GENERATED_COMPOSE}", "\n".join(lines) + "\n", mode="600")
        self.ssh.write_file(f"{self.dir}/{STATE_FILE}", json.dumps({"port": host_port, "kind": plan.kind}) + "\n")
