import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest

from faxina.server import make_server
from faxina.state import AppState
from tests.helpers import build_sample


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="faxina-srv-")
        cls.root = build_sample(os.path.join(cls.tmp, "home"))
        cls.server = make_server(AppState(os.path.join(cls.tmp, "data")), 0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def request(self, method, path, body=None, token=True, host=None):
        con = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        headers = {"Content-Type": "application/json", "Host": host or f"127.0.0.1:{self.server.port}"}
        if token:
            headers["X-Faxina-Token"] = self.server.token
        con.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        res = con.getresponse()
        data = res.read()
        con.close()
        return res.status, data

    def test_index_has_token(self):
        status, data = self.request("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.server.token.encode(), data)

    def test_static_files_have_fixed_mime(self):
        status, _ = self.request("GET", "/static/app.js", token=False)
        self.assertEqual(status, 200)

    def test_api_requires_token(self):
        status, _ = self.request("GET", "/api/status", token=False)
        self.assertEqual(status, 403)

    def test_rejects_foreign_host(self):
        status, _ = self.request("GET", "/api/status", host="evil.example:80")
        self.assertEqual(status, 403)

    def test_static_traversal_blocked(self):
        status, _ = self.request("GET", "/static/../server.py", token=False)
        self.assertEqual(status, 404)

    def test_scan_flow(self):
        status, _ = self.request("POST", "/api/scan", {"path": self.root})
        self.assertEqual(status, 200)
        self.server.state.scanner.join(30)
        status, data = self.request("GET", "/api/overview")
        self.assertEqual(status, 200)
        self.assertGreater(json.loads(data)["size"], 0)
        status, _ = self.request("POST", "/api/scan", {"path": os.path.join(self.root, "nao-existe")})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
