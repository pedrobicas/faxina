"""Caminhos do app e regras de segurança (o que nunca pode ser movido)."""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

APP_NAME = "Faxina"
DEFAULT_PORT = 8737
# Nome da pasta de quarentena criada na raiz de outros discos (D:\, pendrive, HD externo).
LOCAL_QUARANTINE = ".faxina-quarentena"
# Arquivos do sistema que ficam na raiz do disco no Windows e nunca devem ser movidos.
SYSTEM_FILES = {"pagefile.sys", "hiberfil.sys", "swapfile.sys", "dumpstack.log", "dumpstack.log.tmp", "bootmgr"}


def data_dir() -> Path:
    """Pasta onde ficam o banco (histórico, quarentena) e os arquivos em quarentena."""
    override = os.environ.get("FAXINA_DATA")
    if override:
        base = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME.lower()
    base.mkdir(parents=True, exist_ok=True)
    return base


def web_dir() -> Path:
    """Pasta da interface. Funciona tanto rodando o .py quanto empacotado com PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "faxina" / "web"
    return Path(__file__).resolve().parent / "web"


def norm(path: str) -> str:
    p = os.path.normcase(os.path.abspath(path))
    return p.rstrip("\\/") or p


@lru_cache(maxsize=1)
def protected_roots() -> tuple[str, ...]:
    """Pastas do sistema. O Faxina pode analisar, mas nunca move nada daqui."""
    roots: list[str] = []
    if os.name == "nt":
        for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "ProgramW6432"):
            value = os.environ.get(var)
            if value:
                roots.append(value)
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        roots += [os.path.join(drive, "Windows"), os.path.join(drive, "$Recycle.Bin"),
                  os.path.join(drive, "System Volume Information"), os.path.join(drive, "Recovery")]
    else:
        roots += ["/bin", "/sbin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/proc", "/sys", "/usr",
                  "/var/lib", "/snap", "/System", "/Library", "/Applications"]
    return tuple(sorted({norm(r) for r in roots}))


def is_inside(path: str, parent: str) -> bool:
    p, root = norm(path), norm(parent)
    return p == root or p.startswith(root + os.sep)


def is_protected(path: str) -> bool:
    p = norm(path)
    drive_root = norm(os.path.splitdrive(p)[0] + os.sep) if os.name == "nt" else "/"
    if p == drive_root:
        return True
    parent, name = os.path.split(p)
    if norm(parent) == drive_root and name.lower() in SYSTEM_FILES:
        return True
    for root in protected_roots():
        if p == root or p.startswith(root + os.sep):
            return True
    return False


def is_faxina_path(path: str, data: Path) -> bool:
    """Arquivos do próprio Faxina: o banco, a quarentena principal e as quarentenas em outros discos."""
    if is_inside(path, str(data)):
        return True
    parts = [part.lower() for part in Path(os.path.abspath(path)).parts]
    return LOCAL_QUARANTINE in parts
