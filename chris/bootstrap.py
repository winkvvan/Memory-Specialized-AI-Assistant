"""First-run initialization for user-editable state files.

The repository ships generic templates, not a personal memory or personality.
On first run, missing state files under the configured project data directory
are copied from ``templates/``. Existing files are never overwritten.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from chris.config import PROJECT_ROOT, Config

DEFAULT_DATA_ROOT = PROJECT_ROOT / "data"
DEFAULT_TEMPLATES_DIR = PROJECT_ROOT / "templates"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _copy_if_missing(template: Path, target: Path) -> bool:
    if target.exists():
        return False
    if not template.exists():
        raise FileNotFoundError(f"Missing generic state template: {template}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, target)
    return True


def ensure_default_state_files(
    cfg: Config,
    *,
    data_root: Path | None = None,
    templates_dir: Path | None = None,
) -> list[Path]:
    """Create missing personality and memory files from generic templates.

    Only paths inside ``data_root`` are initialized. Custom state locations are
    intentionally left alone; users can copy the templates themselves.
    """
    if cfg.test_mode:
        return []

    root = (data_root or DEFAULT_DATA_ROOT).resolve()
    templates = (templates_dir or DEFAULT_TEMPLATES_DIR).resolve()
    created: list[Path] = []

    configured_files = (
        (cfg.personality_file, "personality.json"),
        (cfg.memory_file, "memory.json"),
    )
    for configured, template_name in configured_files:
        target = cfg.resolve(configured)
        if not _is_under(target, root):
            continue
        if _copy_if_missing(templates / template_name, target):
            created.append(target)
        if template_name == "memory.json":
            archive = target.with_name(target.stem + ".archive.json")
            if _copy_if_missing(templates / "memory.archive.json", archive):
                created.append(archive)
    return created
