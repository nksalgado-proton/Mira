"""``python -m mira`` / the packaged ``Mira.exe`` entry.

ONE binary, two modes (spec/60 §1): ``--render-worker <manifest.json>``
runs the headless batch render worker — that branch never imports Qt —
and anything else launches the app. ``build.bat`` compiles THIS file;
``launch.bat``'s ``python -m mira.ui`` keeps working unchanged.
"""
import sys


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--render-worker":
        from core.render_worker import worker_main
        return worker_main(argv[1:])
    from mira.ui.app import main as ui_main
    return ui_main()


if __name__ == "__main__":
    raise SystemExit(main())
