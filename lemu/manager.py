"""
Multi-instance manager for Lemu.
"""
import os
import shutil
import subprocess
from pathlib import Path
from lemu.config import (
    INSTANCES_DIR,
    load_instance_config,
    save_instance_config,
    get_instance_dir,
)
from lemu.vm import LemuVM

class LemuManager:
    @staticmethod
    def get_all_vmids() -> list[int]:
        if not INSTANCES_DIR.exists():
            return []
        vmids = []
        for entry in INSTANCES_DIR.iterdir():
            if entry.is_dir() and entry.name.isdigit():
                vmids.append(int(entry.name))
        vmids.sort()
        return vmids

    @staticmethod
    def get_next_vmid() -> int:
        existing = LemuManager.get_all_vmids()
        if not existing:
            return 0
        return max(existing) + 1

    @classmethod
    def list_instances(cls) -> list[dict]:
        vmids = cls.get_all_vmids()
        # Always ensure at least instance 0 exists
        if 0 not in vmids:
            cls.create_instance(name="MEmu")
            vmids = cls.get_all_vmids()

        instances = []
        for vmid in vmids:
            cfg = load_instance_config(vmid)
            vm = LemuVM(vmid)
            running = vm.is_running()
            pid = vm.get_pid() if running else None
            instances.append({
                "vmid": vmid,
                "name": cfg.get("name", f"MEmu_{vmid}"),
                "running": running,
                "pid": pid,
                "adb_port": cfg.get("adb_port", 21503 + (vmid * 10)),
                "cpus": cfg.get("cpus", 4),
                "memory_mb": cfg.get("memory_mb", 4096),
                "resolution": f"{cfg.get('resolution_width', 1280)}x{cfg.get('resolution_height', 720)}",
                "display": cfg.get("display", "sdl"),
            })
        return instances

    @classmethod
    def create_instance(cls, name: str | None = None, cpus: int = 4, memory_mb: int = 4096) -> int:
        vmid = cls.get_next_vmid()
        inst_dir = get_instance_dir(vmid)
        cfg = load_instance_config(vmid)
        cfg["name"] = name or (f"MEmu_{vmid}" if vmid > 0 else "MEmu")
        cfg["cpus"] = cpus
        cfg["memory_mb"] = memory_mb
        cfg["adb_port"] = 21503 + (vmid * 10)
        save_instance_config(vmid, cfg)

        # Initialize base data overlay
        vm = LemuVM(vmid)
        vm.ensure_data_image()
        print(f"[+] Created instance {vmid}: {cfg['name']} (Port: {cfg['adb_port']})")
        return vmid

    @classmethod
    def clone_instance(cls, src_vmid: int, new_name: str | None = None) -> int:
        src_vm = LemuVM(src_vmid)
        if src_vm.is_running():
            raise RuntimeError(f"Cannot clone instance {src_vmid} while it is running. Stop it first.")

        new_vmid = cls.get_next_vmid()
        new_inst_dir = get_instance_dir(new_vmid)
        src_cfg = load_instance_config(src_vmid)

        new_cfg = dict(src_cfg)
        new_cfg["name"] = new_name or f"{src_cfg.get('name', 'MEmu')}_copy"
        new_cfg["adb_port"] = 21503 + (new_vmid * 10)
        save_instance_config(new_vmid, new_cfg)

        src_data = src_vm.data_image
        new_data = new_inst_dir / "data.qcow2"

        if src_data.exists():
            print(f"[*] Cloning data disk from instance {src_vmid} to {new_vmid}...")
            # Use qemu-img to create a clone overlay or copy
            cmd = ["qemu-img", "create", "-f", "qcow2", "-b", str(src_data), "-F", "qcow2", str(new_data)]
            subprocess.run(cmd, check=True)
        else:
            new_vm = LemuVM(new_vmid)
            new_vm.ensure_data_image()

        print(f"[+] Cloned instance {src_vmid} -> {new_vmid} ({new_cfg['name']})")
        return new_vmid

    @classmethod
    def delete_instance(cls, vmid: int) -> bool:
        vm = LemuVM(vmid)
        if vm.is_running():
            vm.stop()

        inst_dir = get_instance_dir(vmid)
        if inst_dir.exists():
            shutil.rmtree(inst_dir)
            print(f"[+] Deleted instance {vmid}.")
            return True
        return False

    @classmethod
    def resolve_vmid(cls, ident: str | int) -> int:
        if isinstance(ident, int) or (isinstance(ident, str) and ident.isdigit()):
            return int(ident)
        # Search by name
        for inst in cls.list_instances():
            if inst["name"].lower() == str(ident).lower():
                return inst["vmid"]
        raise ValueError(f"Instance '{ident}' not found.")
