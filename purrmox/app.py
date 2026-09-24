"""Flask application factory and command-line entry point for Purrmox."""
import argparse
import os
import re
import time
from types import SimpleNamespace

from flask import Flask, Response, jsonify, render_template, request

from purrmox import __version__, auth
from purrmox.alerts import Alerts
from purrmox.certs import CertChecker
from purrmox.config import DEFAULT_PATH, load_config
from purrmox.discovery import Discovery
from purrmox.exporter import to_markdown
from purrmox.history import History
from purrmox.links import safe_url
from purrmox.monitor import Monitor
from purrmox.notifier import Discord
from purrmox.proxmox import ProxmoxClient, ProxmoxError
from purrmox.services import ServiceChecker

VALID_ACTIONS = ("start", "shutdown", "reboot")


def create_app(cfg, history_path="history.db"):
    """Build the Flask app and its background workers from a configuration dictionary."""
    px = cfg["proxmox"]
    client_args = dict(host=px["host"], port=px.get("port", 8006), verify_ssl=px.get("verify_ssl", False),
                       scheme=px.get("scheme", "https"))
    client = ProxmoxClient(token_id=px["token_id"], token_secret=px["token_secret"],
                           timeout=px.get("timeout", 5), **client_args)

    # Optional, separate write token for power actions. Disabled by default.
    act = cfg.get("actions") or {}
    actions_on = bool(act.get("enabled") and act.get("token_id") and act.get("token_secret") and auth.enabled(cfg))
    action_client = ProxmoxClient(token_id=act["token_id"], token_secret=act["token_secret"], timeout=15,
                                  **client_args) if actions_on else None
    allowed_actions = [a for a in (act.get("allowed") or list(VALID_ACTIONS)) if a in VALID_ACTIONS]
    protected = {int(x) for x in (act.get("protected_guests") or [])}
    action_log = []

    discovery = Discovery(client, cfg)
    checker = ServiceChecker(cfg.get("services"))
    certs = CertChecker(cfg.get("cert_checks"))
    history = History(path=history_path, retention_days=(cfg.get("history") or {}).get("retention_days", 7))
    notifier = Discord(cfg.get("discord"))
    alerts = Alerts(notifier, cfg)
    monitor = Monitor(client, discovery, checker, certs, history, alerts, poll_seconds=cfg.get("poll_seconds", 30))

    bookmarks = [{"label": b.get("label") or b.get("url"), "url": safe_url(b.get("url"))}
                 for b in (cfg.get("links") or []) if isinstance(b, dict) and safe_url(b.get("url"))]
    node_extra = cfg.get("nodes") or {}

    app = Flask(__name__)
    auth.setup(app, cfg)

    def build_status():
        snap = monitor.get()
        data = snap["summary"] or {"nodes": [], "guests": []}
        data["error"] = snap["error"]
        data["loading"] = snap["updated"] is None
        if snap["summary"]:
            discovery.apply(data)
            if data["nodes"] and not data["guests"]:
                data["error"] = ("Connected, but no VMs or containers are visible: grant the PVEAuditor role "
                                 "on path / to both the user and the token.")
        for n in data["nodes"]:
            extra = node_extra.get(n["name"]) or {}
            n["tailscale"] = extra.get("tailscale")
            n["note"] = extra.get("note")
        backups = snap["backups"]
        for g in data["guests"]:
            b = backups.get(g["id"]) if backups is not None else None
            g["backup"] = {"last": b["last"], "size": b["size"]} if b else None
        data["backups_available"] = backups is not None
        data["services"] = checker.get()
        data["certs"] = certs.get()
        data["links"] = bookmarks
        data["actions"] = allowed_actions if actions_on else []
        data["alerts_on"] = notifier.enabled
        data["backup_hours"] = alerts.backup_hours
        data["now"] = int(time.time())
        data["version"] = __version__
        data["updated"] = time.strftime("%H:%M:%S", time.localtime(snap["updated"])) if snap["updated"] else "-"
        return data

    @app.route("/")
    def index():
        return render_template("index.html", refresh=cfg.get("refresh_seconds", 15))

    @app.route("/healthz")
    def healthz():
        return "ok"

    @app.route("/api/status")
    def status():
        return jsonify(build_status())

    @app.route("/api/history")
    def api_history():
        kind = request.args.get("kind", "node")
        name = request.args.get("name", "")
        if kind not in ("node", "guest") or not re.fullmatch(r"[\w.-]{1,64}", name):
            return jsonify({"error": "invalid parameters"}), 400
        try:
            hours = int(request.args.get("hours", 24))
        except ValueError:
            hours = 24
        return jsonify({"points": history.series(kind, name, hours)})

    @app.route("/api/export.json")
    def export_json():
        return jsonify(build_status())

    @app.route("/api/export.md")
    def export_md():
        return Response(to_markdown(build_status()), mimetype="text/markdown",
                        headers={"Content-Disposition": "attachment; filename=homelab.md"})

    @app.route("/api/action", methods=["POST"])
    def action():
        if not actions_on:
            return jsonify({"error": "actions are disabled"}), 403
        if request.headers.get("X-Purrmox") != "1":
            return jsonify({"error": "invalid request"}), 400
        body = request.get_json(silent=True) or {}
        act_name = body.get("action")
        try:
            gid = int(body.get("id"))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid id"}), 400
        if act_name not in allowed_actions or gid in protected:
            return jsonify({"error": "action not allowed"}), 403
        now = time.time()
        action_log[:] = [t for t in action_log if now - t < 60]
        if len(action_log) >= 5:
            return jsonify({"error": "too many actions, please wait"}), 429
        # Node and type always come from the server-side snapshot, never from the client.
        guest = next((g for g in (monitor.get()["summary"] or {"guests": []})["guests"] if g["id"] == gid), None)
        if not guest:
            return jsonify({"error": "unknown VM or container"}), 404
        kind = "qemu" if guest["type"] == "VM" else "lxc"
        try:
            action_client.post(f"/nodes/{guest['node']}/{kind}/{gid}/status/{act_name}")
        except ProxmoxError as e:
            return jsonify({"error": str(e)}), 502
        action_log.append(now)
        notifier.send(f"Action executed: {act_name}", f"{guest['name']} ({guest['type']} {gid}) via Purrmox.", "info")
        return jsonify({"ok": True})

    def start_background():
        """Start all background workers (discovery, checks, notifications, monitoring)."""
        discovery.start()
        checker.start()
        certs.start()
        notifier.start()
        monitor.start()
        if (cfg.get("discord") or {}).get("enabled") and not notifier.enabled:
            print("Discord notifications are disabled: set DISCORD_BOT_TOKEN and check channel_id.")
        if notifier.enabled and notifier.notify_start:
            notifier.send("Purrmox started", "Monitoring is active.", "info")

    app.purrmox = SimpleNamespace(monitor=monitor, discovery=discovery, checker=checker, certs=certs,
                                  history=history, notifier=notifier, alerts=alerts,
                                  build_status=build_status, start_background=start_background)
    return app


def main(argv=None):
    """Command-line entry point: load the configuration, start the workers and serve."""
    parser = argparse.ArgumentParser(prog="purrmox", description="Purrmox - a status dashboard for Proxmox VE.")
    parser.add_argument("-c", "--config", default=os.environ.get("PURRMOX_CONFIG", DEFAULT_PATH),
                        help="path to the YAML configuration file (default: %(default)s)")
    parser.add_argument("--version", action="version", version=f"Purrmox {__version__}")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    app = create_app(cfg)
    app.purrmox.start_background()
    listen = cfg.get("listen", {})
    host, port = listen.get("host", "127.0.0.1"), listen.get("port", 8080)
    try:
        from waitress import serve
        print(f"Purrmox {__version__} listening on http://{host}:{port} (waitress)")
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port)


if __name__ == "__main__":
    main()
