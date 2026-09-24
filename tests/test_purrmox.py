import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from purrmox.alerts import Alerts  # noqa: E402
from purrmox.discovery import Discovery, guess_scheme  # noqa: E402
from purrmox.links import safe_scheme, safe_url  # noqa: E402
from purrmox.proxmox import summarize  # noqa: E402


class LinkTests(unittest.TestCase):
    def test_safe_url(self):
        self.assertEqual(safe_url("https://a.nl/x"), "https://a.nl/x")
        self.assertIsNone(safe_url("javascript:alert(1)"))
        self.assertIsNone(safe_url("ftp://a.nl"))
        self.assertIsNone(safe_url(None))

    def test_safe_scheme(self):
        self.assertEqual(safe_scheme("http"), "http")
        self.assertIsNone(safe_scheme("file"))


class SummarizeTests(unittest.TestCase):
    def test_nodes_and_guests(self):
        data = summarize([
            {"type": "node", "node": "pve", "status": "online", "cpu": 0.1, "mem": 4, "maxmem": 8, "uptime": 10},
            {"type": "lxc", "vmid": 121, "name": "webdav", "node": "pve", "status": "running", "cpu": 0.01},
            {"type": "qemu", "vmid": 100, "name": "vm", "node": "pve", "status": "stopped"},
            {"type": "storage"},
        ])
        self.assertEqual(len(data["nodes"]), 1)
        self.assertEqual([g["id"] for g in data["guests"]], [100, 121])
        self.assertTrue(data["nodes"][0]["online"])
        self.assertFalse(data["guests"][0]["running"])


class DiscoveryTests(unittest.TestCase):
    def make(self, cfg):
        return Discovery(client=None, cfg=cfg)

    def test_scheme_guess(self):
        self.assertEqual(guess_scheme(443), "https")
        self.assertEqual(guess_scheme(8080), "http")
        self.assertIsNone(guess_scheme(22))

    def test_ports_merge_and_sanitize(self):
        d = self.make({"scan_ports": [22, 80], "guests": {
            5: {"ports": [{"port": 80, "label": "Web", "url": "javascript:x", "scheme": "file"}, 9000]}}})
        ports = d._build_ports(5, "10.0.0.5", [22, 80])
        by = {p["port"]: p for p in ports}
        self.assertEqual(by[22]["label"], "SSH")
        self.assertEqual(by[80]["label"], "Web")
        self.assertIsNone(by[80]["url"])
        self.assertEqual(by[80]["scheme"], "http")
        self.assertFalse(by[9000]["open"] is True)

    def test_apply_overrides(self):
        d = self.make({"guests": {7: {"ip": "10.0.0.7", "note": "test",
                                      "urls": [{"label": "x", "url": "https://x.nl"}, {"url": "javascript:1"}]}}})
        data = {"nodes": [], "guests": [{"id": 7}]}
        out = d.apply(data)["guests"][0]
        self.assertEqual(out["ip"], "10.0.0.7")
        self.assertEqual(len(out["urls"]), 1)


class FakeNotifier:
    def __init__(self):
        self.msgs = []

    def send(self, title, text="", level="info", fields=None, ping=False):
        self.msgs.append((title, level))


def snap(running=True, online=True, mem=1e9):
    return {"nodes": [{"name": "pve", "online": online, "cpu": 1, "mem_used": mem, "mem_total": 1e9,
                       "disk_used": 1, "disk_total": 100}],
            "guests": [{"id": 1, "name": "bot", "type": "CT", "node": "pve", "running": running}]}


class AlertTests(unittest.TestCase):
    def test_no_spam_on_first_poll_and_alert_on_stop(self):
        n = FakeNotifier()
        a = Alerts(n, {})
        a.evaluate(snap(True), None, [], None, [])
        self.assertEqual(n.msgs, [])
        a.evaluate(snap(False), None, [], None, [])
        self.assertIn(("Stopped: bot (CT 1)", "bad"), n.msgs)
        a.evaluate(snap(True), None, [], None, [])
        self.assertEqual(n.msgs[-1][1], "ok")

    def test_ram_needs_three_polls_and_cooldown(self):
        n = FakeNotifier()
        a = Alerts(n, {})
        for _ in range(2):
            a.evaluate(snap(mem=0.95e9), None, [], None, [])
        self.assertFalse(any("RAM" in m[0] for m in n.msgs))
        for _ in range(4):
            a.evaluate(snap(mem=0.95e9), None, [], None, [])
        self.assertEqual(sum("RAM" in m[0] for m in n.msgs), 1)

    def test_service_needs_two_failures(self):
        n = FakeNotifier()
        a = Alerts(n, {})
        a.evaluate(snap(), None, [{"name": "s", "ok": True}], None, [])
        a.evaluate(snap(), None, [{"name": "s", "ok": False}], None, [])
        self.assertFalse(any("offline" in m[0] for m in n.msgs))
        a.evaluate(snap(), None, [{"name": "s", "ok": False}], None, [])
        self.assertTrue(any("Service down" in m[0] for m in n.msgs))

    def test_ignored_guest(self):
        n = FakeNotifier()
        a = Alerts(n, {"alerts": {"ignore_guests": [1]}})
        a.evaluate(snap(True), None, [], None, [])
        a.evaluate(snap(False), None, [], None, [])
        self.assertEqual(n.msgs, [])


class NotifierTests(unittest.TestCase):
    def test_disabled_without_token(self):
        from purrmox.notifier import Discord
        os.environ.pop("DISCORD_BOT_TOKEN", None)
        self.assertFalse(Discord({"enabled": True, "channel_id": "1"}).enabled)


class AppTests(unittest.TestCase):
    """End-to-end checks of the Flask app with a stubbed Proxmox client."""

    def make_app(self, extra=None):
        import tempfile
        from unittest import mock

        from werkzeug.security import generate_password_hash

        from purrmox.app import create_app
        cfg = {"proxmox": {"host": "pve.test", "token_id": "u@pve!t", "token_secret": "s"},
               "auth": {"username": "admin", "password_hash": generate_password_hash("pw")},
               "links": [{"label": "ok", "url": "https://x.test"}, {"label": "bad", "url": "javascript:1"}]}
        cfg.update(extra or {})
        resources = [
            {"type": "node", "node": "pve", "status": "online", "cpu": 0.1, "mem": 1, "maxmem": 2},
            {"type": "lxc", "vmid": 101, "name": "web", "node": "pve", "status": "running"},
        ]
        tmp = tempfile.mkdtemp()
        with mock.patch("purrmox.proxmox.ProxmoxClient.resources", return_value=resources):
            app = create_app(cfg, history_path=os.path.join(tmp, "h.db"))
            app.purrmox.monitor.poll()
        return app

    def test_requires_authentication_except_healthz(self):
        client = self.make_app().test_client()
        self.assertEqual(client.get("/healthz").status_code, 200)
        self.assertEqual(client.get("/api/status").status_code, 401)

    def test_status_with_valid_login(self):
        import base64
        client = self.make_app().test_client()
        token = base64.b64encode(b"admin:pw").decode()
        r = client.get("/api/status", headers={"Authorization": "Basic " + token})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data["guests"][0]["name"], "web")
        self.assertEqual(data["links"], [{"label": "ok", "url": "https://x.test"}])
        self.assertEqual(data["actions"], [])

    def test_actions_disabled_by_default(self):
        import base64
        client = self.make_app().test_client()
        token = base64.b64encode(b"admin:pw").decode()
        r = client.post("/api/action", json={"id": 101, "action": "start"},
                        headers={"Authorization": "Basic " + token, "X-Purrmox": "1"})
        self.assertEqual(r.status_code, 403)


class SetupTests(unittest.TestCase):
    def test_written_config_is_private_and_loadable(self):
        import stat
        import tempfile

        import yaml

        from purrmox.setup import build_config, write_config
        path = os.path.join(tempfile.mkdtemp(), "config.yaml")
        write_config(build_config("pve.test", "u@pve!t", "secret", "admin", "hash"), path)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(yaml.safe_load(f)["proxmox"]["token_id"], "u@pve!t")


if __name__ == "__main__":
    unittest.main()
