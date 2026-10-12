"""Oxirgi deploy sozlamalarini eslab qolish.

Keyingi safar `abzctl` ishga tushirilganda foydalanuvchi hamma narsani qaytadan
kiritmasligi uchun server va loyiha ma'lumotlari `~/.abzctl/last_deploy.json`
fayliga saqlanadi. Parollar hech qachon saqlanmaydi.
"""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional
from abzctl.security import public_repo_url

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
        if not isinstance(data, dict):
            return None
        known = {f.name for f in fields(DeployProfile)}
        profile = DeployProfile(**{k: v for k, v in data.items() if k in known})
        if not isinstance(profile.host, str) or not isinstance(profile.repo, str):
            return None
        if type(profile.port) is not int or not 1 <= profile.port <= 65535:
            return None
        if not isinstance(profile.user, str) or not profile.user:
            return None
        if any(value is not None and not isinstance(value, str)
               for value in (profile.key_file, profile.branch, profile.name, profile.domain)):
            return None
        if profile.app_port is not None and (type(profile.app_port) is not int or not 1 <= profile.app_port <= 65535):
            return None
        clean_repo = public_repo_url(profile.repo)
        if clean_repo != profile.repo:
            profile.repo = clean_repo
            save_last(profile)
        return profile if profile.host and profile.repo else None
    except (OSError, ValueError, TypeError):
        return None


def save_last(profile: DeployProfile) -> None:
    """Muvaffaqiyatli deploy profilini saqlaydi. Yozib bo'lmasa, jimgina o'tkazib yuboradi."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = asdict(profile)
        data["repo"] = public_repo_url(profile.repo)
        fd, temporary = tempfile.mkstemp(prefix=".profile-", dir=CONFIG_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2, ensure_ascii=False)
            os.replace(temporary, LAST_DEPLOY_FILE)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except OSError:
        pass
