import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from purrmox.alerts import Alerts  # noqa: E402
from test_purrmox import AppTests, FakeNotifier  # noqa: E402


class HostTests(unittest.TestCase):
    def test_validate(self):
        from purrmox.hosts import validate
        self.assertEqual(validate([{"name": "pc", "url": "http://10.0.0.5:9100", "token": "t"}]), [])
        self.assertTrue(validate([{"name": "pc", "url": "ftp://x", "token": "t"}]))
        self.assertTrue(validate([{"name": "pc", "url": "http://x"}]))
        self.assertTrue(validate([{"name": "a b", "url": "http://x", "token": "t"},
                                  {"name": "a-b", "url": "http://y", "token": "t"}]))

    def test_token_from_environment(self):
        from unittest import mock

        from purrmox.hosts import resolve_token, validate
        host = {"name": "pc", "url": "http://x", "token_env": "PURRMOX_TEST_TOKEN"}
        self.assertTrue(validate([host]))
        with mock.patch.dict(os.environ, {"PURRMOX_TEST_TOKEN": "secret"}):
            self.assertEqual(validate([host]), [])
            self.assertEqual(resolve_token(host), "secret")

    def test_normalize_drops_bad_types(self):
        from purrmox.hosts import normalize
        h = normalize({"cpu": "high", "mem_used": 5, "mem_total": 10,
                       "disks": [1, {"mount": "C:\\", "used": 1, "total": 2}],
                       "battery": {"percent": 50, "plugged": 1}, "temp": "hot", "evil": "<script>"})
        self.assertEqual(h["cpu"], 0)
        self.assertEqual(h["disks"], [{"mount": "C:\\", "used": 1, "total": 2}])
        self.assertEqual(h["battery"], {"percent": 50, "plugged": True})
        self.assertIsNone(h["temp"])
        self.assertNotIn("evil", h)

    def test_poller_online_then_keeps_last_values_offline(self):
        from unittest import mock

        import requests

        from purrmox.hosts import HostPoller
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {"cpu": 12.5, "mem_used": 1, "mem_total": 2, "os": "Windows 11"}
        poller = HostPoller([{"name": "Milan PC", "url": "http://10.0.0.5:9100/", "token": "t"}])
        with mock.patch("purrmox.hosts.requests.get", return_value=ok) as get:
            poller.refresh()
            self.assertEqual(get.call_args[0][0], "http://10.0.0.5:9100/metrics")
            self.assertEqual(get.call_args[1]["headers"]["Authorization"], "Bearer t")
        h = poller.get()[0]
        self.assertTrue(h["online"])
        self.assertEqual((h["id"], h["cpu"], h["os"]), ("Milan-PC", 12.5, "Windows 11"))
        with mock.patch("purrmox.hosts.requests.get", side_effect=requests.ConnectionError("down")):
            poller.refresh()
        h = poller.get()[0]
        self.assertFalse(h["online"])
        self.assertEqual(h["cpu"], 12.5)
        self.assertIn("unreachable", h["error"])

    def test_poller_reports_rejected_token(self):
        from unittest import mock

        from purrmox.hosts import HostPoller
        poller = HostPoller([{"name": "pc", "url": "http://x", "token": "t"}])
        with mock.patch("purrmox.hosts.requests.get", return_value=mock.Mock(status_code=401)):
            poller.refresh()
        self.assertIn("token", poller.get()[0]["error"])

    def test_host_alerts(self):
        n = FakeNotifier()
        a = Alerts(n, {})

        def host(online, mem=1):
            return [{"id": "pc", "name": "pc", "online": online, "error": None, "cpu": 1, "mem_used": mem,
                     "mem_total": 10, "disk_used": 1, "disk_total": 10}]
        a.evaluate_hosts(host(True))
        a.evaluate_hosts(host(False))
        self.assertEqual(n.msgs, [])
        a.evaluate_hosts(host(False))
        self.assertIn(("Host offline: pc", "bad"), n.msgs)
        a.evaluate_hosts(host(True))
        self.assertEqual(n.msgs[-1], ("Host back online: pc", "ok"))
        for _ in range(3):
            a.evaluate_hosts(host(True, mem=9.5))
        self.assertTrue(any("RAM almost full: pc" in m[0] for m in n.msgs))

    def test_per_host_threshold_overrides_global(self):
        from unittest import mock

        from purrmox.hosts import HostPoller
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {"mem_used": 8, "mem_total": 10}
        cfg = [{"name": "big", "url": "http://x", "token": "t", "alerts": {"ram_percent": 95, "disk_percent": "x"}}]
        poller = HostPoller(cfg)
        with mock.patch("purrmox.hosts.requests.get", return_value=ok):
            poller.refresh()
        host = poller.get()[0]
        self.assertEqual((host["ram_limit"], host["disk_limit"]), (95, None))
        n = FakeNotifier()
        a = Alerts(n, {"alerts": {"ram_percent": 70}})
        for _ in range(4):
            a.evaluate_hosts([host])
        self.assertFalse(any("RAM" in m[0] for m in n.msgs))
        for _ in range(4):
            a.evaluate_hosts([dict(host, ram_limit=None)])
        self.assertTrue(any("RAM almost full" in m[0] for m in n.msgs))

    def test_history_records_hosts(self):
        import tempfile

        from purrmox.history import History
        hist = History(path=os.path.join(tempfile.mkdtemp(), "h.db"))
        hist.record_hosts([{"id": "pc", "online": True, "cpu": 10, "mem_used": 5, "mem_total": 10},
                           {"id": "gone", "online": False, "cpu": 0, "mem_used": 0, "mem_total": 0}])
        series = hist.series("host", "pc", 1)
        self.assertEqual([(p["cpu"], p["mem"]) for p in series], [(10.0, 50.0)])
        self.assertEqual(hist.series("host", "gone", 1), [])


    def test_young_series_is_not_collapsed(self):
        import tempfile
        import time

        from purrmox.history import History
        hist = History(path=os.path.join(tempfile.mkdtemp(), "h.db"))
        now = int(time.time())
        hist.conn.executemany("INSERT INTO samples VALUES (?,?,?,?,?)",
                              [(now - 60 * i, "host", "pc", 10.0, 20.0) for i in range(10)])
        self.assertGreaterEqual(len(hist.series("host", "pc", 24)), 8)


class AgentTests(unittest.TestCase):
    """The agent is served over a real local socket and polled by the real HostPoller."""

    @classmethod
    def setUpClass(cls):
        try:
            import psutil
        except ImportError:
            raise unittest.SkipTest("psutil is not installed (pip install purrmox[agent])")
        import threading
        from http.server import ThreadingHTTPServer

        from purrmox import agent
        cls.agent = agent
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), agent.make_handler(agent.Collector(psutil), "x" * 20))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_token_required(self):
        import requests
        self.assertEqual(requests.get(self.url + "/metrics", timeout=5).status_code, 401)
        bad = {"Authorization": "Bearer nope"}
        self.assertEqual(requests.get(self.url + "/metrics", timeout=5, headers=bad).status_code, 401)
        self.assertEqual(requests.get(self.url + "/healthz", timeout=5).status_code, 200)
        self.assertEqual(requests.get(self.url + "/other", timeout=5).status_code, 404)

    def test_poller_reads_real_agent(self):
        from purrmox.hosts import HostPoller
        poller = HostPoller([{"name": "this-pc", "url": self.url, "token": "x" * 20}])
        poller.refresh()
        h = poller.get()[0]
        self.assertTrue(h["online"], h["error"])
        self.assertGreater(h["mem_total"], 0)
        self.assertGreater(h["disk_total"], 0)
        self.assertGreater(h["cores"], 0)
        self.assertTrue(h["os"])

    def test_agent_refuses_short_token(self):
        with self.assertRaises(SystemExit):
            self.agent.main(["--token-file", os.devnull])


class MountFilterTests(unittest.TestCase):
    def test_macos_hides_repeated_apfs_volumes(self):
        from purrmox.agent import skip_mount
        data = "/System/Volumes/Data"
        self.assertFalse(skip_mount(data, data, "darwin"))
        self.assertTrue(skip_mount("/", data, "darwin"))
        self.assertTrue(skip_mount("/Volumes/Recovery", data, "darwin"))
        self.assertTrue(skip_mount("/System/Volumes/VM", data, "darwin"))
        self.assertFalse(skip_mount("/Volumes/Backup Drive", data, "darwin"))

    def test_other_platforms_keep_mounts(self):
        from purrmox.agent import skip_mount
        self.assertFalse(skip_mount("/mnt/data", "/", "linux"))
        self.assertTrue(skip_mount("/boot/efi", "/", "linux"))
        self.assertFalse(skip_mount("D:\\", "C:\\", "win32"))


class HostAppTests(unittest.TestCase):
    def test_hosts_in_status_history_and_export(self):
        import base64
        from unittest import mock
        app = AppTests().make_app({"hosts": [{"name": "Mac", "url": "http://10.0.0.9:9100", "token": "t"}]})
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {"cpu": 3, "mem_used": 1, "mem_total": 2, "os": "macOS 14"}
        with mock.patch("purrmox.hosts.requests.get", return_value=ok):
            app.purrmox.hosts.refresh()
        client = app.test_client()
        hdr = {"Authorization": "Basic " + base64.b64encode(b"admin:pw").decode()}
        data = client.get("/api/status", headers=hdr).get_json()
        self.assertEqual([(h["id"], h["online"], h["os"]) for h in data["hosts"]], [("Mac", True, "macOS 14")])
        self.assertEqual(client.get("/api/history?kind=host&name=Mac", headers=hdr).status_code, 200)
        self.assertIn("Other machines", client.get("/api/export.md", headers=hdr).get_data(as_text=True))

    def test_load_config_rejects_bad_hosts(self):
        import tempfile

        import yaml

        from purrmox.config import load_config
        path = os.path.join(tempfile.mkdtemp(), "c.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"proxmox": {"host": "h", "token_id": "a", "token_secret": "b"},
                            "hosts": [{"name": "pc", "url": "http://x"}]}, f)
        with self.assertRaises(SystemExit):
            load_config(path)


if __name__ == "__main__":
    unittest.main()
