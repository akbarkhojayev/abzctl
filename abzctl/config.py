"""Oxirgi deploy sozlamalarini eslab qolish.

Keyingi safar `abzctl` ishga tushirilganda foydalanuvchi hamma narsani qaytadan
kiritmasligi uchun server va loyiha ma'lumotlari `~/.abzctl/last_deploy.json`
fayliga saqlanadi. Parollar hech qachon saqlanmaydi.
"""

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path.home() / ".abzctl"
LAST_DEPLOY_FILE = CONFIG_DIR / "last_deploy.json"


@dataclass
class DeployProfile:
    """Bitta deploy uchun saqlanadigan ma'lumotlar (parolsiz)."""

    host: str
    user: str = "root"
    port: int = 22
    key_file: Optional[str] = None
    repo: str = ""
    branch: Optional[str] = None
    name: Optional[str] = None
    app_port: Optional[int] = None
    domain: Optional[str] = None


def load_last() -> Optional[DeployProfile]:
    """Oxirgi deploy profilini o'qiydi. Fayl yo'q yoki buzilgan bo'lsa None qaytaradi."""
    try:
        data = json.loads(LAST_DEPLOY_FILE.read_text(encoding="utf-8"))
        known = {f.name for f in fields(DeployProfile)}
        profile = DeployProfile(**{k: v for k, v in data.items() if k in known})
        return profile if profile.host and profile.repo else None
    except (OSError, ValueError, TypeError):
        return None


def save_last(profile: DeployProfile) -> None:
    """Muvaffaqiyatli deploy profilini saqlaydi. Yozib bo'lmasa, jimgina o'tkazib yuboradi."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        LAST_DEPLOY_FILE.write_text(json.dumps(asdict(profile), indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
