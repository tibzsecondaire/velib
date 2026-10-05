"""velib package."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from .env as early as possible so any module
# importing from this package sees them.
load_dotenv()

PACKAGE_DIR: Path = Path(__file__).resolve().parent
PROJECT_ROOT: Path = PACKAGE_DIR.parent.parent

DATA_DIR: Path = PROJECT_ROOT / "data"
MODELS_DIR: Path = PROJECT_ROOT / "models"
CONF_DIR: Path = PROJECT_ROOT / "conf"

__all__ = [
    "CONF_DIR",
    "DATA_DIR",
    "MODELS_DIR",
    "PACKAGE_DIR",
    "PROJECT_ROOT",
]
