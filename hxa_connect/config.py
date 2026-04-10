"""Configuration from environment variables and config file."""

import json
import os
from pathlib import Path


def get_config() -> dict:
    """Load configuration from env vars, with optional JSON config file override."""
    config_file = os.environ.get("HXA_CONFIG_FILE", "")
    file_cfg = {}
    if config_file and Path(config_file).exists():
        try:
            file_cfg = json.loads(Path(config_file).read_text())
        except Exception:
            pass

    return {
        "hub_url": file_cfg.get("hub_url") or os.environ.get("HXA_HUB_URL", "https://www.ucai.net/connect"),
        "org_id": file_cfg.get("org_id") or os.environ.get("HXA_ORG_ID", ""),
        "org_secret": file_cfg.get("org_secret") or os.environ.get("HXA_ORG_SECRET", ""),
        "agent_name": file_cfg.get("agent_name") or os.environ.get("HXA_AGENT_NAME", ""),
        "agent_token": file_cfg.get("agent_token") or os.environ.get("HXA_AGENT_TOKEN", ""),
        "bridge_port": int(file_cfg.get("bridge_port") or os.environ.get("HXA_BRIDGE_PORT", "9200")),
        "data_dir": file_cfg.get("data_dir") or os.environ.get("HXA_DATA_DIR",
                                                                 os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~")), "components/hxa-connect")),
    }
