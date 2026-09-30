"""Preserve the initial model independently of mutable best/latest checkpoints."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def preserve_initial_baseline(model, checkpoints_dir: str) -> tuple[Path, bool]:
    """Create M0 once, atomically; resuming a run never replaces its baseline."""
    directory = Path(checkpoints_dir)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "model_rl_m0.pt"
    if destination.exists():
        return destination, False
    fd, temporary = tempfile.mkstemp(prefix=".m0-", suffix=".pt", dir=directory)
    os.close(fd)
    try:
        model.save(temporary)
        try:
            # Unlike replace(), a link cannot overwrite an existing M0, even
            # when two training invocations start concurrently.
            os.link(temporary, destination)
        except FileExistsError:
            return destination, False
        return destination, True
    finally:
        Path(temporary).unlink(missing_ok=True)
