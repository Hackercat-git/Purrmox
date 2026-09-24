"""Discord notifications via a bot (REST API). The token is read from the environment or config."""
import os
import queue
import sys
import threading
import time
from datetime import datetime, timezone

import requests

from purrmox import __version__

API = "https://discord.com/api/v10"
COLORS = {"info": 0x4C9AFF, "ok": 0x3FB950, "warn": 0xE3B341, "bad": 0xF85149}
USER_AGENT = f"Purrmox (https://github.com/Hackercat-git/Purrmox, {__version__})"


class Discord:
    def __init__(self, cfg):
        cfg = cfg or {}
        self.token = os.environ.get("DISCORD_BOT_TOKEN") or cfg.get("bot_token") or ""
        self.channel_id = str(cfg.get("channel_id") or "")
        self.guild_id = str(cfg.get("guild_id") or "")
        self.mention_user = str(cfg.get("mention_user_id") or "")
        self.notify_start = bool(cfg.get("notify_start", True))
        self.enabled = bool(cfg.get("enabled") and self.token and self.channel_id)
        self.q = queue.Queue()
        self.last_error = None

    def _headers(self):
        return {"Authorization": f"Bot {self.token}", "Content-Type": "application/json",
                "User-Agent": USER_AGENT}

    def start(self):
        if self.enabled:
            threading.Thread(target=self._worker, daemon=True).start()

    def send(self, title, text="", level="info", fields=None, ping=False):
        """Queue a message; it is delivered by a background worker."""
        if self.enabled:
            self.q.put((title, text, level, fields or [], ping))

    def _log(self, msg):
        self.last_error = msg
        print(f"[discord] {msg}", file=sys.stderr)

    def _verify(self):
        """Ensure the channel belongs to the expected guild, so alerts never go to the wrong place."""
        try:
            r = requests.get(f"{API}/channels/{self.channel_id}", headers=self._headers(), timeout=8)
            if r.status_code != 200:
                self._log(f"channel not accessible (HTTP {r.status_code}); notifications disabled")
                return False
            if self.guild_id and str(r.json().get("guild_id")) != self.guild_id:
                self._log("channel does not belong to the configured guild; notifications disabled")
                return False
            return True
        except requests.RequestException as e:
            self._log(f"verification failed: {e.__class__.__name__}")
            return False

    def _worker(self):
        if not self._verify():
            self.enabled = False
            return
        while True:
            item = self.q.get()
            try:
                self._post(*item)
            except Exception as e:
                self._log(f"sending failed: {e.__class__.__name__}")
            time.sleep(1)

    def _post(self, title, text, level, fields, ping):
        embed = {"title": title[:250], "description": text[:3500], "color": COLORS.get(level, COLORS["info"]),
                 "footer": {"text": "Purrmox"},
                 "timestamp": datetime.now(timezone.utc).isoformat()}
        if fields:
            embed["fields"] = [{"name": str(k)[:250], "value": str(v)[:1000], "inline": True} for k, v in fields[:10]]
        payload = {"embeds": [embed]}
        if ping and self.mention_user:
            payload["content"] = f"<@{self.mention_user}>"
            payload["allowed_mentions"] = {"users": [self.mention_user]}
        for _ in range(2):
            r = requests.post(f"{API}/channels/{self.channel_id}/messages", json=payload,
                              headers=self._headers(), timeout=10)
            if r.status_code == 429:
                time.sleep(min(float(r.json().get("retry_after", 2)), 30))
                continue
            if r.status_code >= 300:
                self._log(f"HTTP {r.status_code} while sending (check the bot permissions in the channel)")
            return
