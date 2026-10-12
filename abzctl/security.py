"""Shared validation and credential redaction."""

import re
from urllib.parse import urlsplit, urlunsplit


def redact_urls(text: str) -> str:
    return re.sub(r"((?:https?|ssh)://)[^\s/@]+@", r"\1***@", text)


def public_repo_url(repo: str) -> str:
    if repo.startswith(("http://", "https://", "ssh://")):
        parts = urlsplit(repo)
        if parts.password is not None or (parts.scheme in ("http", "https") and parts.username):
            return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))
    return repo
