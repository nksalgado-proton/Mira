"""Orchestrate event creation, validation, and status transitions.

v1 simplification (2026-05-14, per Nelson's spec): event creation no
longer auto-populates ``participants``, ``checklist``, or
``whatsapp_message``. Those fields remain on the ``Event`` dataclass
as empty defaults — the Curate / Distribute flow may resurface a
few of them later in dedicated UIs.

The ``checklist_generator`` and ``message_generator`` modules stay
in the repo as dead code pending a separate cleanup pass; this
module just stops calling them.
"""

from datetime import date, datetime
from pathlib import Path

from core.models import Event, EventStatus, TripDay
from core.path_builder import (
    ensure_event_tree,
    event_folder_name,
    event_root_path,
)
from core.settings import load_settings
from core.trip_plan_parser import parse_trip_plan
# Legacy ``data.event_store.save_event`` is used by ``create_event`` only,
# which MC's flow bypasses (it goes via the gateway). Imported lazily inside
# that function so this module loads cleanly without a legacy ``data/`` tree.


def create_event(
    name: str,
    start_date: date,
    end_date: date,
    trip_plan_text: str = "",
) -> Event:
    """Create a new event from the minimum-viable inputs.

    Only ``name``, ``start_date``, ``end_date``, and optional
    ``trip_plan_text`` are accepted. ``google_album_name`` is
    derived from ``year - name`` as a convenience for the future
    Distribute step; everything else stays empty on the Event
    dataclass and is populated by other workflows (Cull, Curate,
    etc.) if ever needed.

    ``trip_plan_text`` is parsed with the home-timezone from settings
    so a plan that omits ``[TZ:..]`` tags inherits the user's home
    TZ for the first day; subsequent days inherit from the previous.
    """
    event = Event(
        name=name,
        start_date=start_date,
        end_date=end_date,
    )

    # Google Photos album name — derived metadata, harmless to keep.
    # The album LINK itself is populated by the future Distribute UI.
    year = start_date.year if start_date else ""
    event.google_album_name = f"{year} - {name}" if year else name

    if trip_plan_text.strip():
        settings = load_settings()
        home_tz = settings.get("home_timezone")
        event.trip_days = parse_trip_plan(trip_plan_text, start_date, home_tz)

    return event


def update_trip_plan(event: Event, text: str) -> None:
    """Re-parse trip plan text and update the event."""
    if event.start_date:
        settings = load_settings()
        home_tz = settings.get("home_timezone")
        event.trip_days = parse_trip_plan(text, event.start_date, home_tz)


def create_folder_structure(event: Event, base_path: str = "") -> tuple[Path, list[str]]:
    """Create the event folder structure on disk.

    Materializes the spec/57 tree at the event root (fixed English
    names on disk; bytes at the two ends, the database in the middle):

        <event-root>/
            Original Media/_cameras/
            Original Media/_phones/
            Original Media/_other/
            Edited Media/
            Cuts/

    ``Picked Media/`` (the derived links doorway) is built lazily on
    entering Edit, and ``Original Media/Merged/`` on the first stack
    adoption — neither is pre-created here.

    **Event-root path policy (Nelson 2026-05-22):** Mira does
    NOT impose a ``/trips/`` (or any other) subdirectory. The user's
    directory structure is theirs to decide:

    * If the caller passed an explicit ``base_path``, the event
      lives at ``<base_path>/<event_folder_name>/`` directly.
    * If ``base_path`` is empty, the global setting
      ``photos_base_path`` is used the same way.
    * For a more deliberate layout, the new-event dialog can pass
      an *already-fully-resolved* path as ``base_path`` and we'll
      just use it as the event root (no event-folder-name appended).
      The distinction is: if ``base_path`` ends with a directory
      named exactly ``event_folder_name(event)``, it IS the event
      root; otherwise we append the folder name.

    **Per-day folders are NOT created here** (2026-05-14 change). The
    ingest creates ``Dia N - description/`` lazily under
    ``Original Media/<bucket>/`` at copy time. Pre-creating from the
    plan was fragile: descriptions change mid-trip; lazy creation
    sidesteps the whole problem.

    Idempotent: re-running on an existing event just ensures the
    stage folders exist — never deletes, never errors on already-
    present dirs.

    Returns ``(event_root_path, warnings)``. ``warnings`` kept for
    API compatibility (currently always empty).
    """
    if not base_path:
        settings = load_settings()
        base_path = settings.get("photos_base_path", "")

    if not base_path:
        raise ValueError(
            "Photos base path is not set. The user must configure "
            "'photos_base_path' in settings (usually via onboarding) "
            "before events can be created on disk."
        )

    base = Path(base_path)
    folder = event_folder_name(event)
    # If the caller passed an absolute, already-resolved event root
    # (last segment matches the event folder name), use it verbatim;
    # otherwise append the event folder name to the base. Either way,
    # NO ``/trips/`` is inserted.
    if base.name == folder:
        root = base
    else:
        root = base / folder
    root.mkdir(parents=True, exist_ok=True)
    # Stamp the absolute event root on the event so subsequent
    # ``event_root_path(...)`` calls return it directly — and the
    # legacy-migration shim sees a modern path.
    event.photos_base_path = str(root)

    # The spec/57 tree: the two byte-ends + the handoff dir, via the
    # single tree-birthing helper (the gateway's materialise_event
    # calls the same one, so create + restore + legacy paths all
    # produce the identical skeleton).
    ensure_event_tree(root)

    return root, []


def save(event: Event) -> None:
    """Persist event to disk (legacy path — MC bypasses this via the gateway)."""
    from data.event_store import save_event  # noqa: PLC0415 — legacy import
    event.updated_at = datetime.now().isoformat()
    save_event(event)
