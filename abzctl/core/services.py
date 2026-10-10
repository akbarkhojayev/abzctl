"""Yordamchi servislar: PostgreSQL, MySQL, Redis, MongoDB.

Loyiha kutubxonalariga qarab qaysi ma'lumotlar bazasi kerakligini aniqlaydi va
generatsiya qilinadigan compose fayliga servis bloklarini hamda ilova uchun ulanish
o'zgaruvchilarini (DATABASE_URL, DB_HOST, REDIS_URL, ...) tayyorlaydi.

Bu moduldagi funksiyalar sof (SSH'siz) — fayl matnlarini qabul qilib natija qaytaradi.
"""

import json
import re
import secrets
from dataclasses import dataclass
from typing import Dict, List, Optional

# Servislar ko'rinadigan tartibda: birinchi SQL baza DATABASE_URL va DB_* ni oladi.
SERVICE_ORDER = ("postgres", "mysql", "mongo", "redis")

SERVICE_LABELS = {
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "mongo": "MongoDB",
    "redis": "Redis",
}

# Kutubxona nomi -> kerakli servis. Nomlar kichik harfda.
LIBRARY_SIGNALS = {
    "postgres": {
        # Node.js
        "pg", "pg-promise", "postgres", "@neondatabase/serverless", "@vercel/postgres",
        # Python
        "psycopg", "psycopg2", "psycopg2-binary", "asyncpg",
        # Go
        "github.com/lib/pq", "github.com/jackc/pgx", "github.com/jackc/pgx/v4", "github.com/jackc/pgx/v5",
    },
    "mysql": {
        "mysql", "mysql2",
        "mysqlclient", "pymysql", "aiomysql", "mysql-connector-python",
        "github.com/go-sql-driver/mysql",
    },
    "mongo": {
        "mongoose", "mongodb",
        "pymongo", "motor", "mongoengine",
        "go.mongodb.org/mongo-driver", "mongoid",
    },
    "redis": {
        "redis", "ioredis", "bullmq", "bull",
        "django-redis", "redis-py",
        "github.com/redis/go-redis/v9", "github.com/go-redis/redis/v8",
        "predis/predis",
    },
}

# Dependency fayllari (loyiha ildiziga nisbatan).
DEPENDENCY_FILES = (
    "package.json", "requirements.txt", "requirements/base.txt", "requirements/prod.txt",
    "pyproject.toml", "Pipfile", "go.mod", "Gemfile", "composer.json",
)
PRISMA_SCHEMAS = ("prisma/schema.prisma", "schema.prisma")

_TOKEN_RE = re.compile(r"[A-Za-z0-9_.@/\-]+")


@dataclass
class DetectedService:
    kind: str     # "postgres" | "mysql" | "mongo" | "redis"
    reason: str   # masalan "pg (package.json)"

    @property
    def label(self) -> str:
        return SERVICE_LABELS[self.kind]


def _library_names(filename: str, content: str) -> List[str]:
    """Fayldan kutubxona nomlarini ajratib oladi."""
    if filename.endswith(".json"):
        try:
            data = json.loads(content)
        except ValueError:
            return []
        names: List[str] = []
        # package.json: dependencies; composer.json: require
        for key in ("dependencies", "require"):
            section = data.get(key)
            if isinstance(section, dict):
                names += list(section)
        return [n.lower() for n in names]
    # requirements.txt, pyproject.toml, Pipfile, go.mod, Gemfile: oddiy tokenlarga bo'lamiz.
    # "psycopg[binary]>=3" -> "psycopg", "gem 'pg'" -> "pg".
    return [t.lower().strip(".-") for t in _TOKEN_RE.findall(content)]


def detect_services(files: Dict[str, str]) -> List[DetectedService]:
    """Loyiha fayllari matnidan kerakli servislarni aniqlaydi.

    Args:
        files: {fayl_nomi: matn} — DEPENDENCY_FILES, PRISMA_SCHEMAS va .env.example.
    """
    found: Dict[str, str] = {}

    for filename, content in files.items():
        if filename in DEPENDENCY_FILES:
            names = set(_library_names(filename, content))
            for kind, signals in LIBRARY_SIGNALS.items():
                match = next((n for n in sorted(signals) if n in names), None)
                if match and kind not in found:
                    found[kind] = f"{match} ({filename})"

    for filename in PRISMA_SCHEMAS:
        content = files.get(filename, "")
        match = re.search(r'provider\s*=\s*"(postgresql|mysql|mongodb)"', content)
        if match:
            kind = {"postgresql": "postgres", "mysql": "mysql", "mongodb": "mongo"}[match.group(1)]
            found.setdefault(kind, f"Prisma ({filename})")

    for filename, content in files.items():
        if not filename.startswith(".env"):
            continue
        # Laravel: DB_CONNECTION=pgsql / mysql
        conn = re.search(r"^\s*DB_CONNECTION\s*=\s*['\"]?(pgsql|mysql|mariadb)", content, re.MULTILINE)
        if conn:
            found.setdefault("postgres" if conn.group(1) == "pgsql" else "mysql", f"DB_CONNECTION ({filename})")
        url = re.search(r"^\s*DATABASE_URL\s*=\s*['\"]?(postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://", content, re.MULTILINE)
        if url:
            scheme = url.group(1)
            kind = "postgres" if scheme.startswith("postgres") else ("mysql" if scheme == "mysql" else "mongo")
            found.setdefault(kind, f"DATABASE_URL ({filename})")
        if re.search(r"^\s*REDIS_(URL|HOST)\s*=", content, re.MULTILINE):
            found.setdefault("redis", f"REDIS_URL ({filename})")

    return [DetectedService(kind, found[kind]) for kind in SERVICE_ORDER if kind in found]


# ============================================================================ hisob ma'lumotlari


def new_credentials(kind: str, db_name: str) -> Dict[str, str]:
    """Servis uchun yangi login/parol yaratadi."""
    if kind == "redis":
        return {}
    creds = {"user": "app", "password": secrets.token_hex(16), "database": db_name}
    if kind == "mysql":
        creds["root_password"] = secrets.token_hex(16)
    return creds


def safe_db_name(project_name: str) -> str:
    """Loyiha nomidan baza nomi yasaydi (faqat harf, raqam va _)."""
    name = re.sub(r"[^a-z0-9_]", "_", project_name.lower()).strip("_") or "app"
    return name[:60]


def app_environment(services: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    """Ilova konteyneriga beriladigan ulanish o'zgaruvchilari.

    Turli framework'lar turli nomlarni kutadi, shuning uchun eng ko'p tarqalganlari beriladi:
    DATABASE_URL (Prisma, Rails, dj-database-url, ...), DB_* (Laravel, ko'p Node loyihalar),
    PG*/POSTGRES_*/MYSQL_* va REDIS_URL / MONGODB_URI.
    """
    env: Dict[str, str] = {}
    sql_done = False

    for kind in SERVICE_ORDER:
        if kind not in services:
            continue
        c = services[kind]

        if kind == "postgres":
            url = f"postgresql://{c['user']}:{c['password']}@postgres:5432/{c['database']}"
            env.update({
                "PGHOST": "postgres", "PGPORT": "5432", "PGUSER": c["user"],
                "PGPASSWORD": c["password"], "PGDATABASE": c["database"],
                "POSTGRES_HOST": "postgres", "POSTGRES_PORT": "5432", "POSTGRES_USER": c["user"],
                "POSTGRES_PASSWORD": c["password"], "POSTGRES_DB": c["database"],
                "POSTGRES_URL": url,
            })
            if not sql_done:
                env.update(_db_vars("pgsql", "postgres", "5432", c, url))
                sql_done = True

        elif kind == "mysql":
            url = f"mysql://{c['user']}:{c['password']}@mysql:3306/{c['database']}"
            env.update({
                "MYSQL_HOST": "mysql", "MYSQL_PORT": "3306", "MYSQL_USER": c["user"],
                "MYSQL_PASSWORD": c["password"], "MYSQL_DATABASE": c["database"], "MYSQL_URL": url,
            })
            if not sql_done:
                env.update(_db_vars("mysql", "mysql", "3306", c, url))
                sql_done = True

        elif kind == "mongo":
            url = f"mongodb://{c['user']}:{c['password']}@mongo:27017/{c['database']}?authSource=admin"
            env.update({"MONGODB_URI": url, "MONGODB_URL": url, "MONGO_URL": url, "MONGO_URI": url})
            if not sql_done:
                env["DATABASE_URL"] = url

        elif kind == "redis":
            env.update({"REDIS_URL": "redis://redis:6379", "REDIS_HOST": "redis", "REDIS_PORT": "6379"})

    return env


def _db_vars(connection: str, host: str, port: str, c: Dict[str, str], url: str) -> Dict[str, str]:
    return {
        "DATABASE_URL": url,
        "DB_CONNECTION": connection,
        "DB_HOST": host, "DB_PORT": port,
        "DB_DATABASE": c["database"], "DB_NAME": c["database"],
        "DB_USERNAME": c["user"], "DB_USER": c["user"],
        "DB_PASSWORD": c["password"],
    }


# ============================================================================ compose bloklari


def compose_blocks(services: Dict[str, Dict[str, str]]) -> List[str]:
    """Compose faylining `services:` bo'limiga qo'shiladigan YAML qatorlari."""
    lines: List[str] = []
    for kind in SERVICE_ORDER:
        if kind not in services:
            continue
        c = services[kind]
        if kind == "postgres":
            lines += [
                "  postgres:",
                "    image: postgres:17-alpine",
                "    environment:",
                f"      POSTGRES_USER: {_q(c['user'])}",
                f"      POSTGRES_PASSWORD: {_q(c['password'])}",
                f"      POSTGRES_DB: {_q(c['database'])}",
                "    volumes:",
                "      - postgres-data:/var/lib/postgresql/data",
                "    healthcheck:",
                f"      test: [\"CMD-SHELL\", \"pg_isready -U {c['user']} -d {c['database']}\"]",
            ]
        elif kind == "mysql":
            lines += [
                "  mysql:",
                "    image: mysql:8.4",
                "    environment:",
                f"      MYSQL_DATABASE: {_q(c['database'])}",
                f"      MYSQL_USER: {_q(c['user'])}",
                f"      MYSQL_PASSWORD: {_q(c['password'])}",
                f"      MYSQL_ROOT_PASSWORD: {_q(c['root_password'])}",
                "    volumes:",
                "      - mysql-data:/var/lib/mysql",
                "    healthcheck:",
                f"      test: [\"CMD\", \"mysqladmin\", \"ping\", \"-h\", \"127.0.0.1\", \"-uroot\", \"-p{c['root_password']}\"]",
            ]
        elif kind == "mongo":
            lines += [
                "  mongo:",
                "    image: mongo:7",
                "    environment:",
                f"      MONGO_INITDB_ROOT_USERNAME: {_q(c['user'])}",
                f"      MONGO_INITDB_ROOT_PASSWORD: {_q(c['password'])}",
                f"      MONGO_INITDB_DATABASE: {_q(c['database'])}",
                "    volumes:",
                "      - mongo-data:/data/db",
                "    healthcheck:",
                "      test: [\"CMD\", \"mongosh\", \"--quiet\", \"--eval\", \"db.adminCommand('ping')\"]",
            ]
        elif kind == "redis":
            lines += [
                "  redis:",
                "    image: redis:7-alpine",
                "    volumes:",
                "      - redis-data:/data",
                "    healthcheck:",
                "      test: [\"CMD\", \"redis-cli\", \"ping\"]",
            ]
        lines += [
            "      interval: 5s",
            "      timeout: 5s",
            "      retries: 20",
            "      start_period: 10s",
            "    restart: unless-stopped",
        ]
    return lines


def volume_names(services: Dict[str, Dict[str, str]]) -> List[str]:
    return [f"{kind}-data" for kind in SERVICE_ORDER if kind in services]


def _q(value: str) -> str:
    """YAML uchun xavfsiz qo'shtirnoqli qiymat (JSON satri YAML'da ham to'g'ri)."""
    return json.dumps(value)


def parse_services(raw: Optional[str]) -> Dict[str, Dict[str, str]]:
    """Saqlangan servislar faylini o'qiydi."""
    try:
        data = json.loads(raw or "{}")
        return {k: v for k, v in data.items() if k in SERVICE_LABELS and isinstance(v, dict)}
    except ValueError:
        return {}
