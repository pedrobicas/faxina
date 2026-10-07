import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from faxina import config, storage
from faxina.duplicates import DuplicateFinder
from faxina.scanner import Scanner
from faxina.state import AppState
from tests.helpers import build_sample


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="faxina-test-")
        self.root = build_sample(os.path.join(self.tmp, "home"))
        self.data = os.path.join(self.tmp, "data")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def scan(self):
        s = Scanner(self.root)
        s.start()
        s.join(30)
        self.assertEqual(s.state, "done")
        return s.result

    def state(self):
        st = AppState(self.data)
        st.start_scan(self.root)
        st.scanner.join(30)
        return st


def disk_total(root):
    total = 0
    for dp, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d != config.LOCAL_QUARANTINE]
        total += sum(os.path.getsize(os.path.join(dp, f)) for f in fs)
    return total


class ScannerTests(Base):
    def test_total_size_matches_disk(self):
        r = self.scan()
        self.assertEqual(r.d_size[0], disk_total(self.root))
        self.assertEqual(sum(c["size"] for c in r.categories()), r.d_size[0])

    def test_largest_and_tree(self):
        r = self.scan()
        self.assertEqual(r.largest_files(limit=3)[0]["size"], 300_000)
        view = r.children_view(0)
        self.assertEqual(sum(i["size"] for i in view["items"]), r.d_size[0])

    def test_cleanup_rules(self):
        r = self.scan()
        c = r.cleanup()
        kinds = sorted(i["type"] for i in c["dev"]["items"])
        self.assertEqual(kinds, ["node_modules", "target", "venv"])  # node_modules aninhado conta uma vez
        nm = next(i for i in c["dev"]["items"] if i["type"] == "node_modules")
        self.assertEqual(nm["size"], 100_000)
        self.assertGreaterEqual(nm["idleDays"], 199)
        self.assertEqual(sorted(i["name"] for i in c["installers"]["items"]), ["setup-antigo.exe", "ubuntu.iso"])
        self.assertEqual([i["name"] for i in c["temp"]["items"]], ["filme.mkv.part"])

    def test_remove_updates_totals_and_cache(self):
        r = self.scan()
        before = r.d_size[0]
        dev_before = r.cleanup()["dev"]["total"]
        r.remove_file(r.largest_files(limit=1)[0]["id"])
        self.assertEqual(r.d_size[0], before - 300_000)
        nm = next(d for d, k in r.d_junk.items() if k == "node_modules")
        r.remove_dir(nm)
        self.assertEqual(r.d_size[0], before - 400_000)
        self.assertEqual(sum(c["size"] for c in r.categories()), r.d_size[0])
        self.assertEqual(r.cleanup()["dev"]["total"], dev_before - 100_000)


class DuplicateTests(Base):
    def test_funnel_finds_only_true_duplicates(self):
        d = DuplicateFinder(self.scan(), min_size=1)
        d.start()
        d.join(30)
        self.assertEqual(d.state, "done")
        view = d.view()
        big = [g for g in view["groups"] if g["size"] == 300_000]
        self.assertEqual(len(big), 1)
        self.assertEqual(len(big[0]["files"]), 3)
        self.assertEqual(big[0]["wasted"], 600_000)
        names = {f["name"] for g in view["groups"] for f in g["files"]}
        self.assertNotIn("a.bin", names)  # mesmo começo, fim diferente: não é duplicado

    def test_ignores_copies_inside_regenerable_folders(self):
        src = os.path.join(self.root, "Videos", "ferias.mp4")
        shutil.copy(src, os.path.join(self.root, "Projetos", "site", "node_modules", "ferias.mp4"))
        d = DuplicateFinder(self.scan(), min_size=200_000)
        d.start()
        d.join(30)
        paths = [f["path"] for f in d.view()["groups"][0]["files"]]
        self.assertEqual(len(paths), 3)
        self.assertFalse(any("node_modules" in p for p in paths))

    def test_hardlinks_are_not_duplicates(self):
        src = os.path.join(self.root, "Videos", "ferias.mp4")
        try:
            os.link(src, os.path.join(self.root, "Videos", "link.mp4"))
        except (OSError, AttributeError):
            self.skipTest("sistema sem hard links")
        d = DuplicateFinder(self.scan(), min_size=200_000)
        d.start()
        d.join(30)
        self.assertEqual(len(d.view()["groups"][0]["files"]), 3)  # 4 nomes, mas 2 são o mesmo arquivo físico


class QuarantineTests(Base):
    def test_move_restore_purge(self):
        st = self.state()
        r = st.result
        f = r.largest_files(limit=1)[0]
        nm = next(d for d, k in r.d_junk.items() if k == "node_modules")
        nm_path = r.dir_path(nm)
        out = st.quarantine_move([f["id"]], [nm])
        self.assertEqual(len(out["moved"]), 2)
        self.assertFalse(os.path.exists(f["path"]))
        self.assertFalse(os.path.exists(nm_path))
        items = st.quarantine_list()["items"]
        self.assertEqual(len(items), 2)
        st.quarantine_restore([next(i for i in items if i["kind"] == "file")["id"]])
        self.assertTrue(os.path.exists(f["path"]))
        self.assertEqual(st.quarantine_purge(None)["freed"], 100_000)
        self.assertEqual(st.quarantine_list()["count"], 0)
        self.assertFalse(os.path.exists(nm_path))
        self.assertEqual(os.listdir(st.quarantine.dir), [])  # lotes vazios são removidos

    def test_refuses_outside_root_and_root_itself(self):
        st = AppState(self.data)
        outside = os.path.join(self.tmp, "fora.txt")
        with open(outside, "wb") as fh:
            fh.write(b"x")
        res = st.quarantine.move([{"path": outside, "size": 1}, {"path": self.root, "size": 1}], self.root)
        self.assertEqual((len(res["moved"]), len(res["failed"])), (0, 2))
        self.assertTrue(os.path.exists(outside))

    def test_restore_does_not_overwrite(self):
        st = self.state()
        f = next(x for x in st.result.largest_files(limit=50) if x["name"] == "relatorio.pdf")
        st.quarantine_move([f["id"]], [])
        with open(f["path"], "wb") as fh:
            fh.write(b"novo")  # alguém criou outro arquivo com o mesmo nome
        out = st.quarantine_restore([st.quarantine_list()["items"][0]["id"]])
        self.assertTrue(out["restored"][0]["path"].endswith("relatorio (restaurado).pdf"))
        with open(f["path"], "rb") as fh:
            self.assertEqual(fh.read(), b"novo")

    def test_other_disk_uses_quarantine_on_that_disk(self):
        """Simula um HD externo: o item fica num "dispositivo" diferente da pasta de dados."""
        st = AppState(self.data)
        external = os.path.join(self.root, "Videos")
        real_dev = storage._dev

        def fake_dev(path):
            p = os.path.abspath(str(path))
            if config.is_inside(p, self.root):
                return 999
            return real_dev(path)

        with mock.patch.object(storage, "_dev", fake_dev):
            target = st.quarantine.root_for(os.path.join(external, "ferias.mp4"))
        self.assertEqual(Path(target), Path(self.root) / config.LOCAL_QUARANTINE)

    def test_system_files_are_protected(self):
        root = "C:\\" if os.name == "nt" else "/"
        self.assertTrue(config.is_protected(root))
        self.assertTrue(config.is_protected(os.path.join(root, "pagefile.sys")))
        self.assertFalse(config.is_protected(self.root))


class HistoryTests(Base):
    def test_second_scan_reports_growth(self):
        st = self.state()
        with open(os.path.join(self.root, "Downloads", "grande.bin"), "wb") as fh:
            fh.write(os.urandom(123_456))
        st.start_scan(self.root)
        st.scanner.join(30)
        delta = st.overview()["delta"]
        self.assertEqual(delta["diff"], 123_456)
        self.assertEqual(delta["changes"][0]["name"], "Downloads")


if __name__ == "__main__":
    unittest.main()
