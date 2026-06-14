"""The batch-export manifest — the work order the app ships to the
render worker (spec/60 §1).

The app resolves everything that needs the gateway / journals at
build time: each unit carries its source, its complete recipe (the
exact ``_render_one`` inputs — the CHOICE or explicit params, crop,
rotation, aspect, style) and its destination directory. The worker
process needs only this file — it never touches ``event.db``.

The wire format is JSON: the app writes the manifest to a temp file
and passes its path on the worker command line
(``Mira.exe --render-worker <manifest.json>``). Unknown keys
are dropped on load so an older worker binary survives a newer
manifest (forward compatibility across the source/packaged split).

Pure logic — no Qt.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

MANIFEST_VERSION = 1

# ``collision`` values — mirror core.cull_export.CollisionPolicy
# semantics without importing the enum into the wire format.
COLLISION_UNIQUE = "unique"
COLLISION_OVERRIDE = "override"


@dataclass(frozen=True)
class PhotoUnit:
    """One photo (or snapshot still) to render.

    The recipe fields are exactly the
    :func:`core.process_export_engine._render_one` inputs; resolution
    order there governs (explicit ``params`` → ``look`` CHOICE →
    ``auto_on`` → identity).
    """

    unit_id: str                    # app-side identity, echoed back
    source: str                     # absolute source path
    dest_dir: str                   # absolute output dir (day folder)
    file_type: str = "jpeg"         # ExportFileType.value
    jpeg_quality: int = 90
    params: Optional[dict] = None   # explicit slider values
    look: Optional[dict] = None     # the spec/54 CHOICE dict
    auto_on: bool = True
    style: Optional[str] = None     # resolved app-side (per-item map)
    crop_norm: Optional[tuple] = None
    crop_angle: float = 0.0
    rotation: int = 0
    aspect_label: str = "Original"

    @classmethod
    def from_dict(cls, d: dict) -> "PhotoUnit":
        known = {f.name for f in fields(cls)}
        kw = {k: v for k, v in d.items() if k in known}
        if kw.get("crop_norm") is not None:
            kw["crop_norm"] = tuple(float(v) for v in kw["crop_norm"])
        return cls(**kw)


@dataclass(frozen=True)
class ClipUnit:
    """One picked clip (segment) to render. The recipe is a
    fully-resolved ExportPlan-shaped dict (spec/60 §1 — the worker
    never re-derives anything from the gateway): ``in_ms``/``out_ms``
    are the segment's absolute milliseconds on the source timeline;
    ``params`` is the resolved tone Params dict (compiled app-side on
    the rep frame — the worker has no event.db to re-resolve from).

    ``base_name`` is the stem of the output file (collision policy
    appends `` (n)`` exactly like photos). The clip lane runs
    one-at-a-time; ``base_name``/``dest_dir`` arbitrate naming across
    concurrent jobs only — the in-job clip lane is serial.
    """

    unit_id: str
    source: str
    dest_dir: str
    base_name: str                 # stem; the worker appends ``.mp4``
    plan: dict = field(default_factory=dict)
    style: Optional[str] = None    # carried for lineage only

    @classmethod
    def from_dict(cls, d: dict) -> "ClipUnit":
        known = {f.name for f in fields(cls)}
        kw = {k: v for k, v in d.items() if k in known}
        return cls(**kw)


@dataclass(frozen=True)
class ExportManifest:
    """A whole batch job, fully resolved."""

    units: tuple[PhotoUnit, ...]
    clips: tuple[ClipUnit, ...] = ()
    collision: str = COLLISION_UNIQUE
    version: int = MANIFEST_VERSION

    def to_json(self) -> str:
        return json.dumps({
            "version": self.version,
            "collision": self.collision,
            "units": [asdict(u) for u in self.units],
            "clips": [asdict(c) for c in self.clips],
        }, indent=1)

    @classmethod
    def from_json(cls, text: str) -> "ExportManifest":
        d = json.loads(text)
        return cls(
            units=tuple(PhotoUnit.from_dict(u) for u in d.get("units", [])),
            clips=tuple(ClipUnit.from_dict(c) for c in d.get("clips", [])),
            collision=str(d.get("collision", COLLISION_UNIQUE)),
            version=int(d.get("version", MANIFEST_VERSION)),
        )

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "ExportManifest":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


__all__ = [
    "COLLISION_OVERRIDE",
    "COLLISION_UNIQUE",
    "ClipUnit",
    "ExportManifest",
    "MANIFEST_VERSION",
    "PhotoUnit",
]
