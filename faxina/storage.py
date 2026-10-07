"""Persistência em SQLite: quarentena (com restauração) e histórico de varreduras."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch TEXT NOT NULL,
    original TEXT NOT NULL,
    stored TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL,
    reason TEXT,
    moved_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root TEXT NOT NULL,
    root_key TEXT NOT NULL,
    finished_at REAL NOT NULL,
    total_size INTEGER NOT NULL,
    files INTEGER NOT NULL,
    dirs INTEGER NOT NULL,
    top_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS scans_root ON scans(root_key, finished_at);
"""


class Database:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "faxina.db"
        self.lock = threading.Lock()
        with self.connect() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        """Abre, faz commit e fecha. (O `with` nativo do sqlite3 faz commit, mas não fecha a conexão.)"""
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()


class QuarantineError(Exception):
    pass


def _dev(path) -> int | None:
    try:
        return os.stat(path).st_dev
    except OSError:
        return None


def _hide(path: Path) -> None:
    """Esconde a pasta de quarentena no Windows (atributo "oculto")."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)
    except Exception:
        pass


class Quarantine:
    """Move arquivos e pastas para uma área do app, guardando de onde vieram.

    A quarentena fica sempre no mesmo disco do item: mover vira só uma renomeação,
    instantânea, em vez de copiar 50 GB de um HD externo para o disco do sistema.
    """

    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.data_dir = Path(data_dir)
        self.dir = self.data_dir / "quarentena"
        self.dir.mkdir(parents=True, exist_ok=True)

    def root_for(self, path: str) -> Path:
        item_dev = _dev(path)
        if item_dev is None or item_dev == _dev(self.dir):
            return self.dir
        # Outro disco: sobe até a raiz desse volume e usa uma pasta oculta lá.
        top = os.path.dirname(os.path.abspath(path))
        while True:
            parent = os.path.dirname(top)
            if parent == top or _dev(parent) != item_dev:
                break
            top = parent
        candidate = Path(top) / config.LOCAL_QUARANTINE
        try:
            created = not candidate.exists()
            candidate.mkdir(exist_ok=True)
            if created:
                _hide(candidate)
            return candidate
        except OSError:
            return self.dir  # sem permissão na raiz do disco: copia para a quarentena principal

    def move(self, items: list[dict], allowed_root: str) -> dict:
        """items: [{"path", "size", "kind"}]. Os tamanhos vêm do scan, não do navegador."""
        batch = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        moved, failed = [], []
        for index, item in enumerate(items):
            path = os.path.abspath(item["path"])
            try:
                self._check_movable(path, allowed_root)
                target_dir = self.root_for(path) / batch
                target_dir.mkdir(parents=True, exist_ok=True)
                dest = target_dir / f"{index:05d}_{os.path.basename(path)}"
                shutil.move(path, dest)
                with self.db.lock, self.db.connect() as con:
                    cur = con.execute(
                        "INSERT INTO quarantine(batch, original, stored, kind, size, reason, moved_at) VALUES (?,?,?,?,?,?,?)",
                        (batch, path, str(dest), item.get("kind", "file"), int(item.get("size", 0)), item.get("reason"), time.time()))
                    row_id = cur.lastrowid
                moved.append({"path": path, "id": row_id, "size": int(item.get("size", 0))})
            except (OSError, QuarantineError) as exc:
                failed.append({"path": path, "error": self._friendly(exc)})
        return {"batch": batch, "moved": moved, "failed": failed, "bytes": sum(m["size"] for m in moved)}

    @staticmethod
    def _friendly(exc: Exception) -> str:
        if isinstance(exc, PermissionError):
            return "Sem permissão, ou o arquivo está aberto em outro programa."
        return str(exc)

    def _check_movable(self, path: str, allowed_root: str) -> None:
        if not os.path.lexists(path):
            raise QuarantineError("O arquivo não existe mais.")
        if not config.is_inside(path, allowed_root) or config.norm(path) == config.norm(allowed_root):
            raise QuarantineError("Fora da pasta analisada.")
        if config.is_protected(path):
            raise QuarantineError("Arquivo ou pasta do sistema: o Faxina não mexe aqui.")
        if config.is_faxina_path(path, self.data_dir):
            raise QuarantineError("Isso é do próprio Faxina.")

    def list(self) -> dict:
        with self.db.connect() as con:
            rows = [dict(r) for r in con.execute("SELECT * FROM quarantine ORDER BY moved_at DESC, id")]
        for r in rows:
            r["exists"] = os.path.lexists(r["stored"])
        return {"items": rows, "total": sum(r["size"] for r in rows), "count": len(rows), "dir": str(self.dir)}

    def restore(self, ids: list[int]) -> dict:
        restored, failed = [], []
        for row in self._rows(ids):
            target = row["original"]
            try:
                if not os.path.lexists(row["stored"]):
                    raise QuarantineError("O item sumiu da quarentena.")
                if os.path.lexists(target):
                    target = self._free_name(target)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(row["stored"], target)
                self._delete_rows([row["id"]])
                self._prune(row["stored"])
                restored.append({"id": row["id"], "path": target})
            except (OSError, QuarantineError) as exc:
                failed.append({"id": row["id"], "error": self._friendly(exc)})
        return {"restored": restored, "failed": failed}

    def purge(self, ids: list[int] | None) -> dict:
        """Apaga de verdade. É a única operação irreversível do app."""
        freed, failed = 0, []
        for row in self._rows(ids):
            try:
                stored = row["stored"]
                if os.path.isdir(stored) and not os.path.islink(stored):
                    shutil.rmtree(stored)
                elif os.path.lexists(stored):
                    os.remove(stored)
                self._delete_rows([row["id"]])
                self._prune(stored)
                freed += row["size"]
            except OSError as exc:
                failed.append({"id": row["id"], "error": self._friendly(exc)})
        return {"freed": freed, "failed": failed}

    def _rows(self, ids: list[int] | None) -> list[dict]:
        with self.db.connect() as con:
            if ids is None:
                return [dict(r) for r in con.execute("SELECT * FROM quarantine")]
            if not ids:
                return []
            marks = ",".join("?" * len(ids))
            return [dict(r) for r in con.execute(f"SELECT * FROM quarantine WHERE id IN ({marks})", [int(i) for i in ids])]

    def _delete_rows(self, ids: list[int]) -> None:
        with self.db.lock, self.db.connect() as con:
            con.executemany("DELETE FROM quarantine WHERE id = ?", [(i,) for i in ids])

    def _prune(self, stored: str) -> None:
        """Remove a pasta do lote quando ficou vazia (e a quarentena do outro disco, se esvaziou)."""
        batch = os.path.dirname(stored)
        for folder in (batch, os.path.dirname(batch)):
            if config.norm(folder) == config.norm(str(self.dir)):
                return
            try:
                os.rmdir(folder)  # só funciona se estiver vazia
            except OSError:
                return

    @staticmethod
    def _free_name(path: str) -> str:
        base, ext = os.path.splitext(path)
        n = 1
        while True:
            candidate = f"{base} (restaurado{'' if n == 1 else ' ' + str(n)}){ext}"
            if not os.path.lexists(candidate):
                return candidate
            n += 1


class History:
    """Resumo de cada varredura, para comparar o crescimento das pastas ao longo do tempo."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def key(root: str) -> str:
        return config.norm(root)

    def record(self, root: str, total: int, files: int, dirs: int, top: dict[str, int]) -> None:
        with self.db.lock, self.db.connect() as con:
            con.execute("INSERT INTO scans(root, root_key, finished_at, total_size, files, dirs, top_json) VALUES (?,?,?,?,?,?,?)",
                        (root, self.key(root), time.time(), total, files, dirs, json.dumps(top)))

    def list(self, root: str | None = None, limit: int = 50) -> list[dict]:
        with self.db.connect() as con:
            if root:
                rows = con.execute("SELECT * FROM scans WHERE root_key = ? ORDER BY finished_at DESC LIMIT ?", (self.key(root), limit))
            else:
                rows = con.execute("SELECT * FROM scans ORDER BY finished_at DESC LIMIT ?", (limit,))
            out = []
            for r in rows:
                item = dict(r)
                item["top"] = json.loads(item.pop("top_json"))
                out.append(item)
            return out

    def roots(self, limit: int = 6) -> list[dict]:
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT root, MAX(finished_at) AS last, COUNT(*) AS scans FROM scans GROUP BY root_key ORDER BY last DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows]
