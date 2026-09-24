"""Loading and validation of the configuration file."""
import sys

import yaml

from purrmox import auth

DEFAULT_PATH = "config.yaml"


def load_config(path=DEFAULT_PATH):
    """Read the YAML configuration and exit with a clear message when it is unusable."""
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        sys.exit(f"Configuration file '{path}' not found. Copy config.example.yaml and fill it in.")
    px = cfg.get("proxmox") or {}
    missing = [k for k in ("host", "token_id", "token_secret") if not px.get(k)]
    if missing:
        sys.exit("Missing keys under 'proxmox' in the configuration: " + ", ".join(missing))
    host = (cfg.get("listen") or {}).get("host", "127.0.0.1")
    if host not in auth.LOOPBACK and not auth.enabled(cfg) and not cfg.get("allow_insecure"):
        sys.exit("Purrmox is configured to listen on the network but 'auth' is not set. "
                 "Generate a hash with 'python -m purrmox.hashpw' and set auth.username and "
                 "auth.password_hash in the configuration.")
    return cfg
