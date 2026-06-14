# Mira

**Photography workflow tool for serious amateurs.** Windows desktop, offline,
no cloud, no telemetry.

Mira is the descendant of [Miracraft](../Miracraft/). One product, two branches:

- **XMC** — the full enthusiast version. Today's working branch. Includes
  every helper, every adjustment, every advanced surface.
- **MC** — the streamlined version. Built after XMC ships, by carving down.
  Same codebase, slimmer surface.

The long-term destination is **one MC**, with streamlining coming from user
profile + how the user works — not from two parallel codebases.

## The four phases

Every event flows through:

1. **Collect** — capture from SD card / past photos, with day plan and Quick Sweep.
2. **Pick** — one decision pass across all captured content, default-Discard.
3. **Edit** — non-destructive tone + crop + export to processed JPEGs.
4. **Share** — build Cuts (time-budgeted, ordered sets) and hand off to PTE.

## Get started (dev)

```powershell
pip install -e .[dev]
launch.bat          # run from source
verify.bat          # run tests
build.bat           # build standalone exe (Nuitka)
ISCC.exe installer.iss   # build installer (Inno Setup)
```

MC is closed-source freeware, so the distributed build needs the
commercial Riverbank PyQt6 (the PyPI `PyQt6` package is GPL). After
`pip install -e .[dev]`, swap the bindings:

```powershell
pip uninstall -y PyQt6
pip install ".\pyqt6 commercial\pyqt6_commercial-6.11.0-cp310-abi3-win_amd64.whl"
```

The `pyqt6 commercial\` directory is gitignored — keep the .whl files
in your local checkout. WebEngine isn't used; skip its wheel.

The dir also holds `pyqt-commercial.sip` — the Riverbank license token
(licensee + signature). Wheel installs don't read it at runtime; it's
only consulted by source builds and PyQt-derived C++ extensions, neither
of which MC does. Keep an off-machine backup of the `.sip` somewhere
safe (cloud drive, password manager) as proof-of-purchase in case you
ever need a re-download or a source build later.

### Build step — Nuitka commercial

`build.bat` runs Nuitka to produce `dist\Mira.exe`. The PyPI
`nuitka` package is GPL/free; Nelson holds a Nuitka Commercial license
(private repo at <https://github.com/Nuitka/Nuitka-commercial.git>).
Install commercial Nuitka in place of the free one before running
`build.bat` for any distributed binary:

```powershell
pip uninstall -y nuitka
pip install git+https://github.com/Nuitka/Nuitka-commercial.git
```

For dev runs (`launch.bat`) and tests (`verify.bat`), Nuitka is not
involved — the swap only matters for build.

## Where things live

- `mira/` — the app
- `core/` — pure-logic modules (no Qt)
- `assets/` — themes, brand profiles, scenarios, icons
- `bin/` — bundled binaries (ExifTool, FFmpeg — not committed)
- `spec/` — design docs; `00-charter.md` is the constitution
- `docs/` — supporting reference docs
- `tests/` — pytest suite

See [`CLAUDE.md`](CLAUDE.md) for working-with-AI conventions.
