"""CPU and memory history stored in SQLite (standard library only)."""
import sqlite3
import threading
import time

from purrmox.proxmox import mem_percent


class History:
    """Record periodic samples per node and guest and return down-sampled series."""

    def __init__(self, path="history.db", retention_days=7, interval=60):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.retention = retention_days * 86400
        self.interval = interval
        self.last_record = 0
        self.last_prune = 0
        with self.lock:
            self.conn.execute("CREATE TABLE IF NOT EXISTS samples "
                              "(ts INTEGER, kind TEXT, name TEXT, cpu REAL, mem REAL)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx ON samples(kind, name, ts)")
            self.conn.commit()

    def record(self, summary):
        """Store one sample per online node and running guest, at most once per interval."""
        now = int(time.time())
        if now - self.last_record < self.interval - 5:
            return
        rows = [(now, "node", n["name"], n["cpu"], mem_percent(n)) for n in summary["nodes"] if n["online"]]
        rows += [(now, "guest", str(g["id"]), g["cpu"], mem_percent(g)) for g in summary["guests"] if g["running"]]
        with self.lock:
            self.conn.executemany("INSERT INTO samples VALUES (?,?,?,?,?)", rows)
            if now - self.last_prune > 3600:
                self.conn.execute("DELETE FROM samples WHERE ts < ?", (now - self.retention,))
                self.last_prune = now
            self.conn.commit()
        self.last_record = now

    def series(self, kind, name, hours=24, points=96):
        """Return averaged samples for the given entity, bucketed into at most ``points`` points."""
        hours = max(1, min(int(hours), self.retention // 3600))
        now = int(time.time())
        since = now - hours * 3600
        with self.lock:
            rows = self.conn.execute("SELECT ts, cpu, mem FROM samples WHERE kind=? AND name=? AND ts>=? ORDER BY ts",
                                     (kind, str(name), since)).fetchall()
        bucket = hours * 3600 / points
        groups = {}
        for ts, cpu, mem in rows:
            groups.setdefault(int((ts - since) / bucket), []).append((ts, cpu, mem))
        out = []
        for key in sorted(groups):
            g = groups[key]
            out.append({"t": g[len(g) // 2][0],
                        "cpu": round(sum(x[1] for x in g) / len(g), 1),
                        "mem": round(sum(x[2] for x in g) / len(g), 1)})
        return out
