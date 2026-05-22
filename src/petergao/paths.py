from __future__ import annotations

from pathlib import Path


def default_output_dir() -> Path:
    downloads = Path.home() / "Downloads"
    if downloads.exists() and downloads.is_dir():
        return downloads
    return Path.cwd()
