"""
Lemu universal entry point.

Installed as the ``lemu`` console script. With no arguments it launches the
graphical multi-instance manager; with arguments it behaves exactly like
``lemuc`` (the CLI).
"""
import sys

from lemu.cli import main as cli_main


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)

    if not args:
        from lemu.gui import main as gui_main
        gui_main()
        return 0

    return cli_main(args)


if __name__ == "__main__":
    sys.exit(main())
