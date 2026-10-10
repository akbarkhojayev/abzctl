"""Manba kodini tahlil qilish (sof funksiyalar, SSH'siz).

Bitta faylli loyihalar (masalan faqat `main.py` dan iborat Telegram bot) uchun:
- `import` qatorlaridan `requirements.txt` yaratish;
- koddan muhit o'zgaruvchilarini (`os.getenv("BOT_TOKEN")`, `process.env.API_KEY`) topish;
- loyiha web-server emas, balki fon jarayoni (bot/worker) ekanini aniqlash.
"""

import re
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set

GENERATED_MARKER = "# abzctl tomonidan avtomatik yaratilgan"

# import nomi -> pip paket nomi (nomi farq qiladiganlar)
PIP_NAMES = {
    "telegram": "python-telegram-bot",
    "telebot": "pyTelegramBotAPI",
    "dotenv": "python-dotenv",
    "decouple": "python-decouple",
    "yaml": "PyYAML",
    "PIL": "Pillow",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python-headless",
    "sklearn": "scikit-learn",
    "dateutil": "python-dateutil",
    "jwt": "PyJWT",
    "discord": "discord.py",
    "psycopg2": "psycopg2-binary",
    "MySQLdb": "mysqlclient",
    "Crypto": "pycryptodome",
    "magic": "python-magic",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "googletrans": "googletrans==4.0.0rc1",
    "youtube_dl": "youtube-dl",
    "yt_dlp": "yt-dlp",
    "apscheduler": "APScheduler",
    "fitz": "PyMuPDF",
    "serial": "pyserial",
    "attr": "attrs",
    "socks": "PySocks",
    "OpenSSL": "pyOpenSSL",
}

# Bot / fon jarayoni kutubxonalari
PY_WORKER_LIBS = {"telegram", "aiogram", "telebot", "pyrogram", "telethon", "discord", "vk_api", "vkbottle"}
JS_WORKER_LIBS = {"telegraf", "node-telegram-bot-api", "grammy", "discord.js", "@grammyjs/runner", "gramio"}
# Web-server kutubxonalari: bular bo'lsa, loyiha HTTP port ochadi deb hisoblanadi.
PY_WEB_LIBS = {"flask", "fastapi", "django", "starlette", "sanic", "tornado", "bottle", "uvicorn", "gunicorn", "quart", "litestar"}
JS_WEB_LIBS = {"express", "fastify", "koa", "@hapi/hapi", "hono", "next", "nuxt", "@nestjs/core", "@remix-run/serve", "astro", "vite", "elysia"}

_FALLBACK_STDLIB = {
    "abc", "argparse", "array", "ast", "asyncio", "base64", "bisect", "calendar", "collections",
    "concurrent", "configparser", "contextlib", "copy", "csv", "ctypes", "dataclasses", "datetime",
    "decimal", "difflib", "email", "enum", "functools", "gc", "getpass", "glob", "gzip", "hashlib",
    "heapq", "hmac", "html", "http", "importlib", "inspect", "io", "ipaddress", "itertools", "json",
    "logging", "math", "mimetypes", "multiprocessing", "operator", "os", "pathlib", "pickle",
    "platform", "pprint", "queue", "random", "re", "secrets", "select", "shlex", "shutil", "signal",
    "socket", "sqlite3", "ssl", "statistics", "string", "struct", "subprocess", "sys", "tempfile",
    "textwrap", "threading", "time", "timeit", "traceback", "types", "typing", "unicodedata",
    "unittest", "urllib", "uuid", "warnings", "weakref", "xml", "zipfile", "zlib", "zoneinfo",
    "__future__", "typing_extensions",
}
STDLIB = set(getattr(sys, "stdlib_module_names", ())) or _FALLBACK_STDLIB

_PY_IMPORT_RE = re.compile(r"^\s*(?:from\s+([A-Za-z_]\w*)[\w.]*\s+import\b|import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*))", re.MULTILINE)
_JS_IMPORT_RE = re.compile(r"""(?:require\(\s*|from\s+|import\s+)['"]([^'"./][^'"]*)['"]""")

# Muhit o'zgaruvchilari. Satr qo'shtirnoq yoki apostrofda bo'lishi mumkin va ichida
# boshqa turdagi qo'shtirnoq bo'lishi mumkin: os.getenv("ADMIN", "yo'q").
_STR = r"""(?:"([^"]*)"|'([^']*)')"""
_NAME = r"""['"]([A-Z][A-Z0-9_]*)['"]"""
_ENV_PATTERNS = [
    re.compile(rf"os\.getenv\(\s*{_NAME}\s*(?:,\s*{_STR})?"),
    re.compile(rf"os\.environ\.get\(\s*{_NAME}\s*(?:,\s*{_STR})?"),
    re.compile(rf"os\.environ\[\s*{_NAME}\s*\]"),
    re.compile(rf"\bconfig\(\s*{_NAME}\s*(?:,\s*default\s*=\s*{_STR})?"),
    re.compile(rf"process\.env\.([A-Z][A-Z0-9_]*)(?:\s*(?:\|\||\?\?)\s*{_STR})?"),
    re.compile(rf"process\.env\[\s*{_NAME}\s*\]"),
]
# Tizim o'zgaruvchilari — ularni so'rash shart emas.
_IGNORED_ENV = {"PORT", "HOST", "HOME", "PATH", "USER", "PWD", "SHELL", "TERM", "LANG", "TZ", "NODE_ENV", "PYTHONPATH", "HOSTNAME"}


def python_imports(sources: Dict[str, str]) -> Set[str]:
    """Python fayllaridagi yuqori darajadagi import nomlari."""
    names: Set[str] = set()
    for path, content in sources.items():
        if not path.endswith(".py"):
            continue
        for match in _PY_IMPORT_RE.finditer(content):
            if match.group(1):
                names.add(match.group(1))
            else:
                for part in match.group(2).split(","):
                    names.add(part.strip().split(".")[0])
    return names


def js_imports(sources: Dict[str, str]) -> Set[str]:
    """JS/TS fayllaridagi paket nomlari (`@scope/name` saqlanadi)."""
    names: Set[str] = set()
    for path, content in sources.items():
        if not re.search(r"\.(m?js|cjs|ts|tsx|jsx)$", path):
            continue
        for match in _JS_IMPORT_RE.finditer(content):
            spec = match.group(1)
            parts = spec.split("/")
            names.add("/".join(parts[:2]) if spec.startswith("@") else parts[0])
    return {n for n in names if not n.startswith("node:")}


def local_modules(paths: Iterable[str]) -> Set[str]:
    """Loyihaning o'z modullari (`utils.py` -> `utils`, `handlers/x.py` -> `handlers`)."""
    result = set()
    for path in paths:
        path = path.removeprefix("./")
        first = path.split("/")[0]
        result.add(first[:-3] if first.endswith(".py") else first)
    return result


def infer_requirements(sources: Dict[str, str]) -> List[str]:
    """Python importlaridan pip paketlar ro'yxatini yasaydi."""
    imports = python_imports(sources) - STDLIB - local_modules(sources)
    return sorted({PIP_NAMES.get(name, name) for name in imports}, key=str.lower)


def render_requirements(packages: List[str]) -> str:
    return GENERATED_MARKER + " (kod importlaridan). O'zingiz requirements.txt qo'shsangiz, o'shanisi ishlatiladi.\n" + "\n".join(packages) + "\n"


def is_worker(sources: Dict[str, str], node_deps: Optional[Set[str]] = None) -> Optional[str]:
    """Loyiha fon jarayoni (bot) bo'lsa, uning kutubxonasini qaytaradi; web-server bo'lsa None.

    Masalan aiogram bilan yozilgan bot HTTP port ochmaydi, shuning uchun unga port
    berish va HTTP orqali tekshirish ma'nosiz.
    """
    py = python_imports(sources)
    js = js_imports(sources) | (node_deps or set())
    py_web = py & PY_WEB_LIBS or re.search(r"from\s+aiohttp\s+import\s+web|aiohttp\.web", "\n".join(sources.values()))
    if py & PY_WORKER_LIBS and not py_web:
        return sorted(py & PY_WORKER_LIBS)[0]
    if js & JS_WORKER_LIBS and not js & JS_WEB_LIBS:
        return sorted(js & JS_WORKER_LIBS)[0]
    return None


@dataclass
class CodeEnvVar:
    name: str
    default: Optional[str]  # None — koddagi default yo'q


def env_vars_from_code(sources: Dict[str, str]) -> List[CodeEnvVar]:
    """Koddan o'qiladigan muhit o'zgaruvchilarini topadi (paydo bo'lish tartibida)."""
    found: Dict[str, Optional[str]] = {}
    for content in sources.values():
        for pattern in _ENV_PATTERNS:
            for match in pattern.finditer(content):
                name = match.group(1)
                default = next((g for g in match.groups()[1:] if g is not None), None)
                if name in _IGNORED_ENV:
                    continue
                if name not in found or (found[name] is None and default):
                    found[name] = default or None
    return [CodeEnvVar(name, default) for name, default in found.items()]
