"""Varredura do disco.

Para aguentar centenas de milhares de arquivos sem estourar a memória, cada informação
fica num array compacto (módulo `array`), e não num objeto Python por arquivo.
Pastas e arquivos são identificados pelo índice nesses arrays.
"""
from __future__ import annotations

import heapq
import os
import threading
import time
from array import array

from . import config
from .categories import (CATEGORIES, DOWNLOAD_DIRS, INSTALLER_EXT, JUNK_INFO, NCAT, category_of,
                         dev_junk_kind, extension, is_temp)

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
DAY = 86400


def _is_link_dir(entry: os.DirEntry) -> bool:
    """Symlinks e junctions do Windows não são seguidos (evita contar a mesma coisa duas vezes e loops)."""
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)
    if is_junction is not None:
        try:
            return is_junction()
        except OSError:
            return False
    if os.name == "nt":
        try:
            return bool(entry.stat(follow_symlinks=False).st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT)
        except (OSError, AttributeError):
            return False
    return False


class ScanResult:
    def __init__(self, root: str):
        self.root = root
        # pastas
        self.d_parent = array("l")
        self.d_name: list[str] = []
        self.d_size = array("q")      # tamanho total da subárvore
        self.d_count = array("q")     # número de arquivos na subárvore
        self.d_latest = array("d")    # mtime mais recente (ignorando pastas de dev regeneráveis)
        self.d_cat = array("q")       # NCAT valores por pasta: bytes por categoria na subárvore
        self.d_children: list[list[int]] = []
        self.d_removed = bytearray()
        self.d_junk: dict[int, str] = {}
        self.d_in_junk = bytearray()  # 1 se a pasta é (ou está dentro de) uma pasta regenerável
        # arquivos
        self.f_dir = array("l")
        self.f_name: list[str] = []
        self.f_size = array("q")
        self.f_mtime = array("d")
        self.f_cat = array("b")
        self.f_removed = bytearray()
        self.files_by_dir: dict[int, list[int]] = {}
        self.cat_count = [0] * NCAT
        self.errors = 0
        self.links_skipped = 0
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.version = 0  # muda a cada remoção; invalida o cache das sugestões
        self._cleanup_cache: tuple[int, int, dict] | None = None
        self._protected_dir: dict[int, bool] = {}

    # ---- construção ----
    def add_dir(self, parent: int, name: str) -> int:
        i = len(self.d_name)
        self.d_parent.append(parent)
        self.d_name.append(name)
        self.d_size.append(0)
        self.d_count.append(0)
        self.d_latest.append(0.0)
        self.d_cat.extend([0] * NCAT)
        self.d_children.append([])
        self.d_removed.append(0)
        if parent >= 0:
            self.d_children[parent].append(i)
        return i

    def add_file(self, d: int, name: str, size: int, mtime: float, cat: int) -> None:
        self.f_dir.append(d)
        self.f_name.append(name)
        self.f_size.append(size)
        self.f_mtime.append(mtime)
        self.f_cat.append(cat)
        self.f_removed.append(0)
        self.d_size[d] += size
        self.d_count[d] += 1
        self.d_cat[d * NCAT + cat] += size
        if mtime > self.d_latest[d]:
            self.d_latest[d] = mtime

    def finalize(self) -> None:
        """Soma os tamanhos de baixo para cima. Como toda pasta é criada depois do pai,
        percorrer os índices em ordem decrescente garante que os filhos são somados antes."""
        d_parent, d_size, d_count, d_cat, d_latest = self.d_parent, self.d_size, self.d_count, self.d_cat, self.d_latest
        for i in range(len(self.d_name) - 1, 0, -1):
            p = d_parent[i]
            d_size[p] += d_size[i]
            d_count[p] += d_count[i]
            base_i, base_p = i * NCAT, p * NCAT
            for c in range(NCAT):
                d_cat[base_p + c] += d_cat[base_i + c]
            if i not in self.d_junk and d_latest[i] > d_latest[p]:
                d_latest[p] = d_latest[i]
        self.d_in_junk = bytearray(len(self.d_name))
        for i in range(1, len(self.d_name)):
            if i in self.d_junk or self.d_in_junk[d_parent[i]]:
                self.d_in_junk[i] = 1
        for f, d in enumerate(self.f_dir):
            self.files_by_dir.setdefault(d, []).append(f)
            self.cat_count[self.f_cat[f]] += 1
        self.finished_at = time.time()

    # ---- consultas ----
    @property
    def file_count(self) -> int:
        return len(self.f_name)

    @property
    def dir_count(self) -> int:
        return len(self.d_name)

    def dir_path(self, d: int) -> str:
        parts = []
        while d > 0:
            parts.append(self.d_name[d])
            d = self.d_parent[d]
        return os.path.join(self.root, *reversed(parts)) if parts else self.root

    def file_path(self, f: int) -> str:
        return os.path.join(self.dir_path(self.f_dir[f]), self.f_name[f])

    def ancestors(self, d: int):
        d = self.d_parent[d]
        while d >= 0:
            yield d
            d = self.d_parent[d]

    def root_label(self) -> str:
        return os.path.basename(self.root.rstrip("\\/")) or self.root

    def dominant_cat(self, d: int) -> int:
        base = d * NCAT
        values = self.d_cat[base:base + NCAT]
        best = max(range(NCAT), key=lambda c: values[c])
        return best if values[best] > 0 else NCAT - 1

    def is_protected_dir(self, d: int) -> bool:
        if d not in self._protected_dir:
            self._protected_dir[d] = config.is_protected(self.dir_path(d)) if d > 0 else False
        return self._protected_dir[d]

    def children_view(self, d: int, max_items: int = 70, min_fraction: float = 0.002) -> dict:
        """Itens de uma pasta para o mapa: subpastas e arquivos, os pequenos agrupados em "outros"."""
        items = []
        for c in self.d_children[d]:
            if self.d_removed[c] or self.d_size[c] <= 0:
                continue
            items.append({"kind": "dir", "id": c, "name": self.d_name[c], "size": self.d_size[c],
                          "files": self.d_count[c], "cat": self.dominant_cat(c), "junk": self.d_junk.get(c),
                          "hasChildren": bool(self.d_children[c] or self.files_by_dir.get(c))})
        for f in self.files_by_dir.get(d, []):
            if self.f_removed[f] or self.f_size[f] <= 0:
                continue
            items.append({"kind": "file", "id": f, "name": self.f_name[f], "size": self.f_size[f], "cat": self.f_cat[f]})
        items.sort(key=lambda it: it["size"], reverse=True)
        total = self.d_size[d]
        shown, rest_size, rest_count = [], 0, 0
        for it in items:
            if len(shown) < max_items and (total == 0 or it["size"] / total >= min_fraction):
                shown.append(it)
            else:
                rest_size += it["size"]
                rest_count += 1
        if rest_size > 0:
            shown.append({"kind": "rest", "id": -1, "name": f"{rest_count} itens menores", "size": rest_size, "cat": NCAT - 1})
        chain = [d, *self.ancestors(d)]
        crumbs = [{"id": x, "name": self.d_name[x] if x else self.root_label()} for x in reversed(chain)]
        return {"id": d, "path": self.dir_path(d), "size": total, "files": self.d_count[d],
                "items": shown, "crumbs": crumbs}

    def file_info(self, f: int) -> dict:
        return {"id": f, "name": self.f_name[f], "path": self.file_path(f), "size": self.f_size[f],
                "mtime": self.f_mtime[f], "cat": self.f_cat[f]}

    def largest_files(self, limit: int = 200, cat: int | None = None, older_than_days: int = 0, min_size: int = 0) -> list[dict]:
        cutoff = time.time() - older_than_days * DAY if older_than_days else None
        f_size, f_cat, f_mtime, f_removed = self.f_size, self.f_cat, self.f_mtime, self.f_removed

        def ok(f: int) -> bool:
            if f_removed[f] or f_size[f] < min_size:
                return False
            if cat is not None and f_cat[f] != cat:
                return False
            return cutoff is None or f_mtime[f] < cutoff

        best = heapq.nlargest(limit, (f for f in range(len(f_size)) if ok(f)), key=f_size.__getitem__)
        return [self.file_info(f) for f in best]

    def categories(self) -> list[dict]:
        return [{"index": c, "id": cid, "label": label, "color": color, "size": self.d_cat[c], "count": self.cat_count[c]}
                for c, (cid, label, color) in enumerate(CATEGORIES)]

    def top_dirs(self, limit: int = 8) -> list[dict]:
        kids = [c for c in self.d_children[0] if not self.d_removed[c] and self.d_size[c] > 0]
        kids.sort(key=lambda c: self.d_size[c], reverse=True)
        return [{"id": c, "name": self.d_name[c], "path": self.dir_path(c), "size": self.d_size[c],
                 "files": self.d_count[c], "cat": self.dominant_cat(c)} for c in kids[:limit]]

    def cleanup(self, limit: int = 300) -> dict:
        """Sugestões de limpeza. Nada é removido aqui: só listado com tamanho e motivo.
        O resultado fica em cache até alguma coisa ser removida."""
        if self._cleanup_cache and self._cleanup_cache[0] == self.version and self._cleanup_cache[1] >= limit:
            full = self._cleanup_cache[2]
        else:
            keep = max(limit, 300)
            full = self._compute_cleanup(keep)
            self._cleanup_cache = (self.version, keep, full)
        return {k: {**v, "items": v["items"][:limit]} for k, v in full.items()}

    def _compute_cleanup(self, limit: int) -> dict:
        now = time.time()
        dev = []
        for d, kind in self.d_junk.items():
            if self.d_removed[d] or self.d_size[d] <= 0 or self.is_protected_dir(d):
                continue
            if self.d_in_junk[self.d_parent[d]]:
                continue  # node_modules dentro de node_modules: conta só o de cima
            project = self.d_parent[d]
            last = self.d_latest[project] or self.d_latest[d]
            label, regen = JUNK_INFO.get(kind, (kind, ""))
            dev.append({"kind": "dir", "id": d, "type": kind, "label": label, "regen": regen,
                        "path": self.dir_path(d), "project": self.dir_path(project), "size": self.d_size[d],
                        "files": self.d_count[d], "idleDays": int((now - last) / DAY) if last else None})
        dev.sort(key=lambda x: x["size"], reverse=True)

        installers, temp, old_big = [], [], []
        in_downloads: dict[int, bool] = {}

        def is_download_dir(d: int) -> bool:
            if d not in in_downloads:
                in_downloads[d] = any(self.d_name[x].lower() in DOWNLOAD_DIRS for x in [d, *self.ancestors(d)] if x > 0)
            return in_downloads[d]

        for f in range(len(self.f_size)):
            d = self.f_dir[f]
            if self.f_removed[f] or self.d_in_junk[d]:
                continue
            name, size, age = self.f_name[f], self.f_size[f], (now - self.f_mtime[f]) / DAY
            ext = extension(name)
            if is_temp(name):
                bucket = temp
            elif age > 30 and (ext in INSTALLER_EXT or (ext == ".exe" and is_download_dir(d))):
                bucket = installers
            elif size >= 100 * 1024 * 1024 and age > 365:
                bucket = old_big
            else:
                continue
            if not self.is_protected_dir(d) and not config.is_protected(self.file_path(f)):
                bucket.append(f)

        def pack(ids: list[int], reason: str) -> dict:
            ids.sort(key=self.f_size.__getitem__, reverse=True)
            items = []
            for f in ids[:limit]:
                info = self.file_info(f)
                info["kind"] = "file"
                info["reason"] = reason
                items.append(info)
            return {"total": sum(self.f_size[f] for f in ids), "count": len(ids), "items": items}

        return {
            "dev": {"total": sum(x["size"] for x in dev), "count": len(dev), "items": dev[:limit]},
            "installers": pack(installers, "Instalador ou imagem de disco parada há mais de 30 dias"),
            "temp": pack(temp, "Arquivo temporário ou download incompleto"),
            "oldBig": pack(old_big, "Maior que 100 MB e sem alteração há mais de 1 ano"),
        }

    # ---- remoção (depois de mover para a quarentena) ----
    def _subtract(self, d: int, size: int, count: int, cats: list[int]) -> None:
        for a in [d, *self.ancestors(d)]:
            self.d_size[a] -= size
            self.d_count[a] -= count
            base = a * NCAT
            for c in range(NCAT):
                self.d_cat[base + c] -= cats[c]

    def remove_file(self, f: int) -> None:
        if self.f_removed[f]:
            return
        self.f_removed[f] = 1
        cats = [0] * NCAT
        cats[self.f_cat[f]] = self.f_size[f]
        self._subtract(self.f_dir[f], self.f_size[f], 1, cats)
        self.cat_count[self.f_cat[f]] -= 1
        self.version += 1

    def remove_dir(self, d: int) -> None:
        if d == 0 or self.d_removed[d]:
            return
        base = d * NCAT
        cats = list(self.d_cat[base:base + NCAT])
        size, count = self.d_size[d], self.d_count[d]
        stack = [d]
        while stack:
            x = stack.pop()
            self.d_removed[x] = 1
            for f in self.files_by_dir.get(x, []):
                if not self.f_removed[f]:
                    self.f_removed[f] = 1
                    self.cat_count[self.f_cat[f]] -= 1
            stack.extend(self.d_children[x])
        self._subtract(self.d_parent[d], size, count, cats)
        self.d_size[d], self.d_count[d] = 0, 0
        for c in range(NCAT):
            self.d_cat[base + c] = 0
        self.version += 1


class Scanner:
    """Roda a varredura numa thread e expõe o progresso para a interface."""

    def __init__(self, root: str, on_done=None, exclude: list[str] | None = None):
        self.root = os.path.abspath(root)
        self.result = ScanResult(self.root)
        self.exclude = {config.norm(e) for e in (exclude or [])}
        self.state = "idle"
        self.error: str | None = None
        self.files = 0
        self.dirs = 0
        self.bytes = 0
        self.current = ""
        self.started_at = 0.0
        self.elapsed = 0.0
        self._cancel = threading.Event()
        self._on_done = on_done
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.state = "running"
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._run, name="faxina-scan", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def progress(self) -> dict:
        elapsed = (time.time() - self.started_at) if self.state == "running" else self.elapsed
        return {"state": self.state, "root": self.root, "files": self.files, "dirs": self.dirs, "bytes": self.bytes,
                "current": self.current, "elapsed": elapsed, "errors": self.result.errors, "error": self.error}

    def _run(self) -> None:
        try:
            self._scan()
            if self._cancel.is_set():
                self.state = "cancelled"
            else:
                self.result.finalize()
                self.state = "done"
        except Exception as exc:  # erro inesperado não pode derrubar o servidor
            self.state = "error"
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.elapsed = time.time() - self.started_at
            self.current = ""
        if self.state == "done" and self._on_done:
            try:
                self._on_done(self)
            except Exception:
                pass

    def _scan(self) -> None:
        r = self.result
        r.add_dir(-1, self.root)
        stack = [(self.root, 0)]
        while stack:
            if self._cancel.is_set():
                return
            path, d = stack.pop()
            self.current = path
            try:
                it = os.scandir(path)
            except OSError:
                r.errors += 1
                continue
            with it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if _is_link_dir(entry):
                                r.links_skipped += 1
                                continue
                            if entry.name == config.LOCAL_QUARANTINE or (self.exclude and config.norm(entry.path) in self.exclude):
                                continue  # a própria quarentena do Faxina não entra na conta
                            child = r.add_dir(d, entry.name)
                            kind = dev_junk_kind(entry.name, path, entry.path)
                            if kind:
                                r.d_junk[child] = kind
                            stack.append((entry.path, child))
                            self.dirs += 1
                        elif entry.is_file(follow_symlinks=False):
                            st = entry.stat(follow_symlinks=False)
                            r.add_file(d, entry.name, st.st_size, st.st_mtime, category_of(entry.name))
                            self.files += 1
                            self.bytes += st.st_size
                        elif entry.is_symlink():
                            r.links_skipped += 1
                    except OSError:
                        r.errors += 1
