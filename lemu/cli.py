#!/usr/bin/env python3
"""
lemuc - Command Line Interface for Lemu (MEmu Play Linux Reconstruction).
Drop-in replacement for Windows memuc.exe.

Entry point registered as the ``lemuc`` console script.
"""

import argparse
import os
import sys
from pathlib import Path

# Ensure project root is in sys.path (also works when installed via pip).
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from lemu.vm import LemuVM
from lemu.manager import LemuManager
from lemu.adb import LemuADB
from lemu.config import load_instance_config


def print_instances_table(instances: list[dict]) -> None:
    print(f"{'ID':<4} {'NAME':<15} {'STATUS':<10} {'PID':<8} {'PORT':<8} {'CPUS':<5} {'RAM':<8} {'DISPLAY'}")
    print("-" * 75)
    for inst in instances:
        status = "RUNNING" if inst["running"] else "STOPPED"
        pid_str = str(inst["pid"]) if inst["pid"] else "-"
        print(f"{inst['vmid']:<4} {inst['name']:<15} {status:<10} {pid_str:<8} {inst['adb_port']:<8} {inst['cpus']:<5} {inst['memory_mb']}MB   {inst['display']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lemuc",
        description="Lemu CLI - Multi-instance controller for MEmu Android on Linux",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    p_list = subparsers.add_parser("list", aliases=["listvms"], help="List all instances")

    p_start = subparsers.add_parser("start", help="Start an instance")
    p_start.add_argument("-i", "--vmid", type=int, default=0, help="Instance ID")
    p_start.add_argument("-n", "--name", type=str, help="Instance name")
    p_start.add_argument("-b", "--headless", action="store_true", help="Run without GUI window")
    p_start.add_argument("-d", "--display", choices=["sdl", "gtk", "none"], help="Display backend")

    p_stop = subparsers.add_parser("stop", help="Stop an instance")
    p_stop.add_argument("-i", "--vmid", type=int, default=0, help="Instance ID")
    p_stop.add_argument("-n", "--name", type=str, help="Instance name")

    p_stopall = subparsers.add_parser("stopall", help="Stop all running instances")

    p_create = subparsers.add_parser("create", help="Create a new instance")
    p_create.add_argument("-n", "--name", type=str, help="Instance name")
    p_create.add_argument("--cpus", type=int, default=4, help="CPU cores")
    p_create.add_argument("--memory", type=int, default=4096, help="RAM in MB")

    p_clone = subparsers.add_parser("clone", help="Clone an existing instance")
    p_clone.add_argument("-i", "--vmid", type=int, required=True, help="Source Instance ID")
    p_clone.add_argument("-r", "--nametag", type=str, help="New instance name")

    p_remove = subparsers.add_parser("remove", help="Delete an instance")
    p_remove.add_argument("-i", "--vmid", type=int, required=True, help="Instance ID")

    p_install = subparsers.add_parser("installapp", help="Install APK into instance")
    p_install.add_argument("-i", "--vmid", type=int, default=0, help="Instance ID")
    p_install.add_argument("apk", type=str, help="Path to .apk file")

    p_uninstall = subparsers.add_parser("uninstallapp", help="Uninstall package from instance")
    p_uninstall.add_argument("-i", "--vmid", type=int, default=0, help="Instance ID")
    p_uninstall.add_argument("package", type=str, help="Android package name")

    p_startapp = subparsers.add_parser("startapp", help="Start Android application")
    p_startapp.add_argument("-i", "--vmid", type=int, default=0, help="Instance ID")
    p_startapp.add_argument("package", type=str, help="Android package name")
    p_startapp.add_argument("-a", "--activity", type=str, help="Optional main activity")

    p_stopapp = subparsers.add_parser("stopapp", help="Force stop Android application")
    p_stopapp.add_argument("-i", "--vmid", type=int, default=0, help="Instance ID")
    p_stopapp.add_argument("package", type=str, help="Android package name")

    p_port = subparsers.add_parser("getadbipport", help="Get ADB IP and port for instance")
    p_port.add_argument("-i", "--vmid", type=int, default=0, help="Instance ID")

    p_adb = subparsers.add_parser("adb", help="Execute ADB command on instance")
    p_adb.add_argument("-i", "--vmid", type=int, default=0, help="Instance ID")
    p_adb.add_argument("adb_args", nargs=argparse.REMAINDER, help="Arguments passed to adb")

    p_console = subparsers.add_parser("console", help="Open ADB shell on instance")
    p_console.add_argument("-i", "--vmid", type=int, default=0, help="Instance ID")

    p_keymap = subparsers.add_parser(
        "keymap", help="Map host keys to Android touch gestures (game keymapper)")
    p_keymap.add_argument("-i", "--vmid", type=int, default=0, help="Instance ID")
    p_keymap.add_argument("-j", "--injector", choices=["auto", "touch", "input"],
                          default="auto",
                          help="How gestures reach the guest: 'touch' = native uinput "
                               "touchscreen (~1 ms, real multi-touch), 'input' = "
                               "adb shell input (~80 ms), 'auto' tries touch then falls "
                               "back (default: auto)")
    p_keymap.add_argument("-p", "--profile", type=str, default=None,
                          help="Force a profile from keymap.json instead of "
                               "auto-detecting the foreground game")
    p_keymap.add_argument("--source", type=str, default=None,
                          help="Input source for the 'input' injector (e.g. 'touchscreen'); "
                               "default auto")
    p_keymap.add_argument("--list", action="store_true", help="Print bindings and exit")
    p_keymap.add_argument("-v", "--verbose", action="store_true", help="Log every injected action")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        print_instances_table(LemuManager.list_instances())
        return 0

    cmd = args.command

    if cmd in ("list", "listvms"):
        print_instances_table(LemuManager.list_instances())

    elif cmd == "start":
        vmid = args.vmid
        if args.name:
            vmid = LemuManager.resolve_vmid(args.name)
        vm = LemuVM(vmid)
        vm.start(headless=args.headless, display_backend=args.display)

    elif cmd == "stop":
        vmid = args.vmid
        if args.name:
            vmid = LemuManager.resolve_vmid(args.name)
        vm = LemuVM(vmid)
        vm.stop()

    elif cmd == "stopall":
        instances = LemuManager.list_instances()
        for inst in instances:
            if inst["running"]:
                LemuVM(inst["vmid"]).stop()

    elif cmd == "create":
        vmid = LemuManager.create_instance(args.name, args.cpus, args.memory)
        print_instances_table(LemuManager.list_instances())

    elif cmd == "clone":
        new_vmid = LemuManager.clone_instance(args.vmid, args.nametag)
        print_instances_table(LemuManager.list_instances())

    elif cmd == "remove":
        LemuManager.delete_instance(args.vmid)
        print_instances_table(LemuManager.list_instances())

    elif cmd == "installapp":
        adb = LemuADB(args.vmid)
        adb.install_app(args.apk)

    elif cmd == "uninstallapp":
        adb = LemuADB(args.vmid)
        adb.uninstall_app(args.package)

    elif cmd == "startapp":
        adb = LemuADB(args.vmid)
        adb.start_app(args.package, args.activity)

    elif cmd == "stopapp":
        adb = LemuADB(args.vmid)
        adb.stop_app(args.package)

    elif cmd == "getadbipport":
        adb = LemuADB(args.vmid)
        print(f"127.0.0.1:{adb.port}")

    elif cmd == "adb":
        adb = LemuADB(args.vmid)
        res = adb.run_adb(*args.adb_args)
        if res.stdout:
            sys.stdout.write(res.stdout)
        if res.stderr:
            sys.stderr.write(res.stderr)

    elif cmd == "console":
        adb = LemuADB(args.vmid)
        adb.connect()
        os.system(f"adb -s {adb.target} shell")

    elif cmd == "keymap":
        from lemu.keymap import run_keymapper
        return run_keymapper(
            args.vmid,
            source=args.source,
            list_only=args.list,
            verbose=args.verbose,
            injector=args.injector,
            profile=args.profile,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
