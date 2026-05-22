from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path


APP_NAME = "petergao"


def config_dir() -> Path:
    override = os.environ.get("PETERGAO_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = os.environ.get("APPDATA")
        if root:
            return Path(root) / APP_NAME
    if sys_platform() == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    root = os.environ.get("XDG_CONFIG_HOME")
    if root:
        return Path(root) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def sys_platform() -> str:
    return os.sys.platform


def session_path() -> Path:
    return config_dir() / "session.json"


@dataclass(slots=True)
class StoredSession:
    email: str | None
    domain: str | None
    cookies: dict[str, str]
    php_session_id: str | None = None
    course_download_counts: dict[str, int] = field(default_factory=dict)
    last_known_quota_used: int | None = None


class SessionStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or session_path()

    def load(self) -> StoredSession | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        cookies = payload.get("cookies") or {}
        return StoredSession(
            email=payload.get("email"),
            domain=payload.get("domain"),
            cookies={str(key): str(value) for key, value in cookies.items()},
            php_session_id=payload.get("php_session_id"),
            course_download_counts={
                str(key).upper(): int(value)
                for key, value in (payload.get("course_download_counts") or {}).items()
            },
            last_known_quota_used=payload.get("last_known_quota_used"),
        )

    def save(self, session: StoredSession) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(session), indent=2), encoding="utf-8")

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
