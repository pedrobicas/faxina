"""Busca de arquivos duplicados em funil.

1. Agrupa por tamanho (arquivos de tamanhos diferentes nunca são iguais; não lê nada do disco).
2. Descarta hard links (dois nomes para o mesmo arquivo físico não ocupam espaço duas vezes).
3. Hash do começo e do fim de cada candidato (64 KB de cada ponta).
4. Só quem empatou até aqui tem o arquivo inteiro lido.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import defaultdict

from .scanner import ScanResult

EDGE = 64 * 1024
CHUNK = 1024 * 1024


class DuplicateFinder:
    def __init__(self, result: ScanResult, min_size: int = 1024 * 1024):
        self.result = result
        self.min_size = max(1, int(min_size))
        self.state = "idle"
        self.phase = ""
        self.error: str | None = None
        self.done_bytes = 0
        self.total_bytes = 0
        self.done_files = 0
        self.total_files = 0
        self.read_bytes = 0  # quanto foi lido do disco de verdade (mostra a economia do funil)
        self.candidate_bytes = 0
        self.current = ""
        self.groups: list[dict] = []
        self.started_at = 0.0
        self.elapsed = 0.0
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.state = "running"
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._run, name="faxina-dups", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def progress(self) -> dict:
        elapsed = (time.time() - self.started_at) if self.state == "running" else self.elapsed
        return {"state": self.state, "phase": self.phase, "doneBytes": self.done_bytes, "totalBytes": self.total_bytes,
                "doneFiles": self.done_files, "totalFiles": self.total_files, "current": self.current,
                "elapsed": elapsed, "minSize": self.min_size, "error": self.error,
                "readBytes": self.read_bytes, "candidateBytes": self.candidate_bytes,
                "groups": len(self.groups), "wasted": sum(g["wasted"] for g in self.groups)}

    def view(self, limit: int = 300) -> dict:
        """Grupos com os arquivos ainda existentes (alguns podem ter ido para a quarentena)."""
        r = self.result
        groups = []
        for g in self.groups:
            alive = [f for f in g["files"] if not r.f_removed[f]]
            if len(alive) < 2:
                continue
            groups.append({"hash": g["hash"], "size": g["size"], "wasted": g["size"] * (len(alive) - 1),
                           "files": [r.file_info(f) for f in alive]})
        groups.sort(key=lambda g: g["wasted"], reverse=True)
        return {"progress": self.progress(), "total": sum(g["wasted"] for g in groups),
                "count": len(groups), "groups": groups[:limit]}

    # ---- trabalho ----
    def _run(self) -> None:
        try:
            self._find()
            self.state = "cancelled" if self._cancel.is_set() else "done"
        except Exception as exc:
            self.state = "error"
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.elapsed = time.time() - self.started_at
            self.current = ""

    def _hash_edges(self, path: str, size: int) -> str | None:
        h = hashlib.blake2b(digest_size=16)
        try:
            with open(path, "rb") as fh:
                if size <= 2 * EDGE:
                    block = fh.read()  # arquivo pequeno: lê inteiro, então este hash já é o definitivo
                    h.update(block)
                    self.read_bytes += len(block)
                else:
                    h.update(fh.read(EDGE))
                    fh.seek(-EDGE, os.SEEK_END)
                    h.update(fh.read(EDGE))
                    self.read_bytes += 2 * EDGE
        except OSError:
            return None
        return h.hexdigest()

    def _hash_full(self, path: str) -> str | None:
        h = hashlib.blake2b(digest_size=20)
        try:
            with open(path, "rb") as fh:
                while True:
                    if self._cancel.is_set():
                        return None
                    block = fh.read(CHUNK)
                    if not block:
                        break
                    h.update(block)
                    self.done_bytes += len(block)
                    self.read_bytes += len(block)
        except OSError:
            return None
        return h.hexdigest()

    def _find(self) -> None:
        r = self.result
        self.phase = "grouping"
        by_size: dict[int, list[int]] = defaultdict(list)
        for f in range(r.file_count):
            size = r.f_size[f]
            # Arquivos dentro de node_modules, .venv etc. ficam de fora: são recriados pelo
            # gerenciador de pacotes, então apagar uma "cópia" lá dentro só quebraria o projeto.
            if size >= self.min_size and not r.f_removed[f] and not r.d_in_junk[r.f_dir[f]]:
                by_size[size].append(f)
        candidates = [ids for ids in by_size.values() if len(ids) > 1]

        # hard links: mesmo (dispositivo, inode) = mesmo arquivo físico
        self.phase = "links"
        unique_groups = []
        for ids in candidates:
            seen, keep = set(), []
            for f in ids:
                try:
                    st = os.stat(r.file_path(f))
                    key = (st.st_dev, st.st_ino) if st.st_ino else ("path", f)
                except OSError:
                    continue
                if key not in seen:
                    seen.add(key)
                    keep.append(f)
            if len(keep) > 1:
                unique_groups.append(keep)
            if self._cancel.is_set():
                return
        self.candidate_bytes = sum(r.f_size[f] for g in unique_groups for f in g)

        self.phase = "edges"
        self.total_files = sum(len(g) for g in unique_groups)
        edge_groups: list[list[int]] = []
        for ids in unique_groups:
            buckets: dict[str, list[int]] = defaultdict(list)
            for f in ids:
                if self._cancel.is_set():
                    return
                path = r.file_path(f)
                self.current = path
                digest = self._hash_edges(path, r.f_size[f])
                self.done_files += 1
                if digest:
                    buckets[digest].append(f)
            edge_groups.extend(b for b in buckets.values() if len(b) > 1)

        self.phase = "full"
        self.total_bytes = sum(r.f_size[f] for g in edge_groups for f in g if r.f_size[f] > 2 * EDGE)
        for ids in edge_groups:
            size = r.f_size[ids[0]]
            if size <= 2 * EDGE:
                # o hash das pontas já cobriu o arquivo inteiro
                self.groups.append({"hash": "edge", "size": size, "files": ids, "wasted": size * (len(ids) - 1)})
                continue
            buckets = defaultdict(list)
            for f in ids:
                path = r.file_path(f)
                self.current = path
                digest = self._hash_full(path)
                if self._cancel.is_set():
                    return
                if digest:
                    buckets[digest].append(f)
            for digest, same in buckets.items():
                if len(same) > 1:
                    self.groups.append({"hash": digest, "size": size, "files": same, "wasted": size * (len(same) - 1)})
        self.phase = "done"
