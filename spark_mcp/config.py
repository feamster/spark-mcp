"""Configuration for Spark MCP Server."""

from pathlib import Path
import json
from typing import Optional


CONFIG_FILE = Path.home() / ".mcp-config" / "spark" / "config.json"

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


def get_templates_dir() -> Path:
    """Get the directory for PDF templates."""
    templates_dir = CONFIG_FILE.parent / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    return templates_dir


def save_template(template_name: str, template_data: dict) -> Path:
    """Save a PDF template to the templates directory."""
    templates_dir = get_templates_dir()
    template_path = templates_dir / f"{template_name}.json"
    with open(template_path, 'w') as f:
        json.dump(template_data, f, indent=2)
    return template_path


def load_template(template_name: str) -> Optional[dict]:
    """Load a PDF template from the templates directory."""
    templates_dir = get_templates_dir()
    template_path = templates_dir / f"{template_name}.json"
    if not template_path.exists():
        return None
    try:
        with open(template_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def list_templates() -> list:
    """List all available PDF templates."""
    templates_dir = get_templates_dir()
    templates = []
    for template_path in templates_dir.glob("*.json"):
        try:
            with open(template_path) as f:
                data = json.load(f)
                templates.append({
                    "name": template_path.stem,
                    "fields": len(data.get("fields", [])),
                    "description": data.get("description", "")
                })
        except (json.JSONDecodeError, IOError):
            continue
    return templates


def delete_template(template_name: str) -> bool:
    """Delete a PDF template."""
    templates_dir = get_templates_dir()
    template_path = templates_dir / f"{template_name}.json"
    if template_path.exists():
        template_path.unlink()
        return True
    return False
