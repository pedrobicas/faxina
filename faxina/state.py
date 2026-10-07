"""Estado do app: une scanner, duplicados, quarentena e histórico. A API só conversa com esta classe."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config
from .categories import CATEGORIES
from .duplicates import DuplicateFinder
from .scanner import Scanner, ScanResult
from .storage import Database, History, Quarantine


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class AppState:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else config.data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.data_dir)
        self.quarantine = Quarantine(self.db, self.data_dir)
        self.history = History(self.db)
        self.lock = threading.RLock()
        self.scanner: Scanner | None = None
        self.dups: DuplicateFinder | None = None
        self.previous: dict | None = None
        self.freed_this_session = 0

    # ---- varredura ----
    @property
    def result(self) -> ScanResult:
        if not self.scanner or self.scanner.state != "done":
            raise ApiError("Nenhuma varredura concluída ainda.", 409)
        return self.scanner.result

    def start_scan(self, path: str) -> dict:
        path = os.path.abspath(os.path.expanduser(str(path or "").strip().strip('"')))
        if not os.path.isdir(path):
            raise ApiError("Essa pasta não existe ou não pode ser aberta.")
        with self.lock:
            if self.scanner and self.scanner.state == "running":
                self.scanner.cancel()
            if self.dups and self.dups.state == "running":
                self.dups.cancel()
            self.dups = None
            previous = self.history.list(path, limit=1)
            self.previous = previous[0] if previous else None
            self.scanner = Scanner(path, on_done=self._scan_done, exclude=[str(self.data_dir)])
            self.scanner.start()
        return self.status()

    def _scan_done(self, scanner: Scanner) -> None:
        r = scanner.result
        top = {r.d_name[c]: r.d_size[c] for c in sorted(r.d_children[0], key=lambda c: r.d_size[c], reverse=True)[:40]}
        self.history.record(r.root, r.d_size[0], r.file_count, r.dir_count, top)

    def cancel_scan(self) -> dict:
        if self.scanner and self.scanner.state == "running":
            self.scanner.cancel()
            self.scanner.join(5)
        return self.status()

    def status(self) -> dict:
        return {
            "scan": self.scanner.progress() if self.scanner else None,
            "dups": self.dups.progress() if self.dups else None,
            "quarantine": {"count": self.quarantine.list()["count"]},
            "freed": self.freed_this_session,
        }

    # ---- leitura ----
    def overview(self) -> dict:
        r = self.result
        cleanup = r.cleanup(limit=0)
        reclaim = {k: {"total": v["total"], "count": v["count"]} for k, v in cleanup.items()}
        dup_total = None
        if self.dups and self.dups.state == "done":
            dup_total = self.dups.view(limit=0)["total"]
        disk = None
        try:
            usage = shutil.disk_usage(r.root)
            disk = {"total": usage.total, "used": usage.used, "free": usage.free}
        except OSError:
            pass
        delta = None
        if self.previous:
            prev_top = self.previous["top"]
            changes = []
            for d in r.d_children[0]:
                if r.d_removed[d]:
                    continue
                name = r.d_name[d]
                before = prev_top.get(name)
                if before is None:
                    if r.d_size[d] > 0:
                        changes.append({"name": name, "before": 0, "after": r.d_size[d], "diff": r.d_size[d]})
                elif r.d_size[d] != before:
                    changes.append({"name": name, "before": before, "after": r.d_size[d], "diff": r.d_size[d] - before})
            changes = sorted(changes, key=lambda x: abs(x["diff"]), reverse=True)[:6]
            delta = {"since": self.previous["finished_at"], "before": self.previous["total_size"],
                     "after": r.d_size[0], "diff": r.d_size[0] - self.previous["total_size"], "changes": changes}
        return {
            "root": r.root, "size": r.d_size[0], "files": r.d_count[0], "dirs": r.dir_count,
            "errors": r.errors, "linksSkipped": r.links_skipped,
            "scanSeconds": (r.finished_at or time.time()) - r.started_at,
            "categories": r.categories(), "topDirs": r.top_dirs(), "largest": r.largest_files(limit=6),
            "reclaim": reclaim, "duplicates": dup_total, "disk": disk, "delta": delta,
            "freed": self.freed_this_session,
        }

    def tree(self, dir_id: int) -> dict:
        r = self.result
        if dir_id < 0 or dir_id >= r.dir_count or r.d_removed[dir_id]:
            dir_id = 0
        return r.children_view(dir_id)

    def files(self, cat: str | None, older_than_days: int, limit: int) -> dict:
        r = self.result
        cat_index = next((i for i, c in enumerate(CATEGORIES) if c[0] == cat), None) if cat else None
        return {"items": r.largest_files(limit=max(1, min(limit, 1000)), cat=cat_index, older_than_days=older_than_days)}

    def cleanup(self) -> dict:
        return self.result.cleanup()

    # ---- duplicados ----
    def start_duplicates(self, min_size: int) -> dict:
        r = self.result
        with self.lock:
            if self.dups and self.dups.state == "running":
                self.dups.cancel()
            self.dups = DuplicateFinder(r, min_size)
            self.dups.start()
        return self.status()

    def duplicates(self) -> dict:
        if not self.dups:
            return {"progress": None, "groups": [], "total": 0, "count": 0}
        if self.dups.state == "running":
            return {"progress": self.dups.progress(), "groups": [], "total": 0, "count": 0}
        return self.dups.view()

    def cancel_duplicates(self) -> dict:
        if self.dups and self.dups.state == "running":
            self.dups.cancel()
            self.dups.join(5)
        return self.status()

    def reset_duplicates(self) -> dict:
        self.cancel_duplicates()
        self.dups = None
        return self.status()

    # ---- quarentena ----
    def quarantine_move(self, file_ids: list[int], dir_ids: list[int]) -> dict:
        r = self.result
        if self.dups and self.dups.state == "running":
            raise ApiError("Espere a busca de duplicados terminar antes de mover arquivos.", 409)
        items, refs = [], []
        for f in sorted({int(x) for x in file_ids}):
            if 0 <= f < r.file_count and not r.f_removed[f]:
                items.append({"path": r.file_path(f), "size": r.f_size[f], "kind": "file"})
                refs.append(("f", f))
        for d in sorted({int(x) for x in dir_ids}):
            if 0 < d < r.dir_count and not r.d_removed[d]:
                items.append({"path": r.dir_path(d), "size": r.d_size[d], "kind": "dir"})
                refs.append(("d", d))
        if not items:
            raise ApiError("Nenhum item válido selecionado.")
        with self.lock:
            outcome = self.quarantine.move(items, r.root)
            moved_paths = {m["path"] for m in outcome["moved"]}
            for (kind, index), item in zip(refs, items):
                if os.path.abspath(item["path"]) in moved_paths:
                    if kind == "f":
                        r.remove_file(index)
                    else:
                        r.remove_dir(index)
            self.freed_this_session += outcome["bytes"]
        return outcome

    def quarantine_list(self) -> dict:
        return self.quarantine.list()

    def quarantine_restore(self, ids: list[int]) -> dict:
        out = self.quarantine.restore(ids)
        if out["restored"]:
            out["note"] = "Os itens voltaram para o lugar. Analise a pasta de novo para eles aparecerem nos números."
        return out

    def quarantine_purge(self, ids: list[int] | None) -> dict:
        return self.quarantine.purge(ids)

    # ---- utilidades ----
    def places(self) -> dict:
        home = Path.home()
        names = [("Pasta pessoal", home), ("Downloads", home / "Downloads"),
                 ("Documentos", home / "Documents"), ("Documentos", home / "Documentos"),
                 ("Área de trabalho", home / "Desktop"), ("Área de trabalho", home / "Área de Trabalho"),
                 ("Vídeos", home / "Videos"), ("Imagens", home / "Pictures")]
        seen, places = set(), []
        for label, p in names:
            key = os.path.normcase(str(p))
            if p.is_dir() and key not in seen:
                seen.add(key)
                places.append({"label": label, "path": str(p), "kind": "folder"})
        drives = []
        if os.name == "nt":
            candidates = [f"{chr(c)}:\\" for c in range(ord("A"), ord("Z") + 1)]
        else:
            candidates = ["/"] + ([str(p) for p in Path("/Volumes").iterdir()] if Path("/Volumes").is_dir() else [])
        for root in candidates:
            if os.path.isdir(root):
                try:
                    u = shutil.disk_usage(root)
                    drives.append({"label": f"Disco {root[:2]}" if os.name == "nt" else root, "path": root,
                                   "kind": "drive", "total": u.total, "free": u.free, "used": u.used})
                except OSError:
                    pass
        return {"places": places, "drives": drives, "recent": self.history.roots(), "sep": os.sep}

    def browse(self, path: str) -> dict:
        path = os.path.abspath(os.path.expanduser(path or str(Path.home())))
        if not os.path.isdir(path):
            raise ApiError("Pasta não encontrada.")
        dirs = []
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False) and not e.name.startswith("$") and e.name != config.LOCAL_QUARANTINE:
                            dirs.append({"name": e.name, "path": e.path, "hidden": e.name.startswith(".")})
                    except OSError:
                        pass
        except OSError:
            raise ApiError("Sem permissão para abrir essa pasta.", 403)
        dirs.sort(key=lambda d: (d["hidden"], d["name"].lower()))
        parent = os.path.dirname(path.rstrip("\\/")) or None
        if parent == path:
            parent = None
        return {"path": path, "parent": parent, "dirs": dirs[:500]}

    def reveal(self, path: str) -> dict:
        path = os.path.abspath(path)
        allowed = [self.scanner.root] if self.scanner else []
        if not any(config.is_inside(path, a) for a in allowed) and not config.is_faxina_path(path, self.data_dir):
            raise ApiError("Só dá para abrir itens da pasta analisada.", 403)
        if not os.path.lexists(path):
            raise ApiError("Esse item não existe mais.", 404)
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
        except OSError as exc:
            raise ApiError(f"Não consegui abrir o explorador de arquivos: {exc}", 500)
        return {"ok": True}
