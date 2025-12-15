"""Configuration for Spark MCP Server."""

from pathlib import Path
import json
from typing import Optional


CONFIG_FILE = Path.home() / ".config" / "spark-mcp" / "config.json"

# Default configuration
DEFAULTS = {
    "signature_image_path": str(Path.home() / "Documents/letter-template/sig.png"),
    "pdf_output_dir": str(Path.home() / "Downloads"),
}


def load_config() -> dict:
    """Load configuration from file, with defaults."""
    config = DEFAULTS.copy()

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                user_config = json.load(f)
                config.update(user_config)
        except (json.JSONDecodeError, IOError):
            pass

    return config


def save_config(config: dict) -> None:
    """Save configuration to file."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


def get_signature_path() -> Optional[str]:
    """Get the configured signature image path."""
    config = load_config()
    path = Path(config.get("signature_image_path", "")).expanduser()
    return str(path) if path.exists() else None


def get_output_dir() -> str:
    """Get the configured PDF output directory."""
    config = load_config()
    return config.get("pdf_output_dir", str(Path.home() / "Downloads"))
