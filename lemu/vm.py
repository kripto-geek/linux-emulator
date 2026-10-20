"""
QEMU-KVM process supervisor for Lemu instances.
"""
import os
import sys
import time
import socket
import json
import signal
import subprocess
from pathlib import Path
from lemu.config import (
    BASE_DIR,
    DEFAULT_GUEST,
    MEMU_SYSTEM_IMAGE,
    GUEST_MEMU,
    GUEST_X86_64,
    X86_64_CPU,
    X86_64_INITRD,
    X86_64_KERNEL,
    X86_64_CMDLINE,
    guest_master_image,
    get_instance_dir,
    load_instance_config,
)
from lemu.deps import require_all

class LemuVM:
    def __init__(self, vmid: int = 0):
        self.vmid = vmid
        self.inst_dir = get_instance_dir(vmid)
        self.config = load_instance_config(vmid)
        self.pid_file = self.inst_dir / "qemu.pid"
        self.log_file = self.inst_dir / "vm.log"
        self.qmp_sock = Path(f"/tmp/lemu_{vmid}_qmp.sock")
        self.data_image = self.inst_dir / "data.qcow2"

    def ensure_data_image(self):
        if not self.data_image.exists():
            print(f"[+] Creating copy-on-write data disk overlay for instance {self.vmid}...")
            master = guest_master_image(self.config)
            if not master.exists():
                raise FileNotFoundError(
                    f"Guest master image is missing: {master}\n"
                    f"    Build it first (see README: 'Building the Android-x86 master image')."
                )
            rel_master = os.path.relpath(master, self.inst_dir)
            cmd = [
                "qemu-img", "create", "-f", "qcow2",
                "-b", rel_master, "-F", "qcow2",
                str(self.data_image)
            ]
            subprocess.run(cmd, check=True)

    def get_pid(self) -> int | None:
        if not self.pid_file.exists():
            return None
        try:
            pid = int(self.pid_file.read_text().strip())
            # Check if process is actually running
            os.kill(pid, 0)
            return pid
        except (ValueError, OSError):
            if self.pid_file.exists():
                self.pid_file.unlink(missing_ok=True)
            return None

    def is_running(self) -> bool:
        return self.get_pid() is not None

    def build_command(self, headless: bool = False, display_backend: str | None = None) -> list[str]:
        """Assemble the qemu-system-x86_64 argv for this instance (no side effects)."""
        # Configure display arguments with ungrab options
        if headless:
            display_arg = "none"
        else:
            backend = display_backend or self.config.get("display", "gtk")
            if backend == "gtk":
                display_arg = "gtk,zoom-to-fit=on"
            elif backend == "sdl":
                display_arg = "sdl,grab-on-hover=off"
            else:
                display_arg = backend

        guest = self.config.get("guest", DEFAULT_GUEST)
        vga = self.config.get("vga", "std")
        cpus = self.config.get("cpus", 4)
        mem = self.config.get("memory_mb", 4096)
        adb_port = self.config.get("adb_port", 21503 + (self.vmid * 10))
        console_log = self.inst_dir / "console.log"

        # CPU model. Android-x86 must see the genuine host CPU vendor or its
        # 32-bit compat vDSO is built around sysenter, which cannot execute on
        # an AMD host in long mode (see config: X86_64_CPU).
        cpu = self.config.get("cpu") or (X86_64_CPU if guest == GUEST_X86_64 else "host")

        cmd = [
            "qemu-system-x86_64",
            "-name", f"Lemu - {self.config.get('name', 'MEmu')} (ID: {self.vmid})",
            "-enable-kvm",
            "-cpu", cpu,
            "-smp", str(cpus),
            "-m", str(mem),
        ]

        if guest == GUEST_X86_64:
            # Android-x86 is booted through QEMU's external kernel + initramfs
            # instead of a bootloader on the disk (see config: boot artifacts).
            for label, path in (("kernel", X86_64_KERNEL), ("initrd", X86_64_INITRD)):
                if not path.exists():
                    raise FileNotFoundError(
                        f"Android-x86 boot {label} is missing: {path}\n"
                        f"    Build it first (see README: 'Building the Android-x86 master image')."
                    )
            cmd += [
                "-kernel", str(X86_64_KERNEL),
                "-initrd", str(X86_64_INITRD),
                "-append", X86_64_CMDLINE,
            ]

        if guest == GUEST_MEMU:
            # MEmu guest layout: sda = master system disk, sdb = per-instance userdata.
            cmd += [
                "-drive", f"file={MEMU_SYSTEM_IMAGE},format=qcow2,if=ide,index=0",
                "-drive", f"file={self.data_image},format=qcow2,if=ide,index=1",
            ]
        else:
            # Android-x86 layout: the per-instance COW overlay is the whole boot
            # disk (system + userdata), so each instance gets its own /data for
            # free and the master image is never written to.
            cmd += ["-drive", f"file={self.data_image},format=qcow2,if=ide,index=0"]

        # Optional Android-x86 installation ISO. Booting it writes into the
        # instance overlay, so the master image stays pristine.
        install_iso = self.config.get("install_iso")
        if install_iso:
            cmd += [
                "-drive", f"file={install_iso},media=cdrom,readonly=on,if=ide,index=2",
                "-boot", "order=d",
            ]

        cmd += [
            "-netdev", f"user,id=net0,hostfwd=tcp:127.0.0.1:{adb_port}-:5555",
            "-device", "virtio-net-pci,netdev=net0",
            "-device", "qemu-xhci",
            "-device", "usb-tablet",
            # ttyS0: serial console, captured to a file so Android boot output
            # can be inspected without a graphical window.
            "-chardev", f"file,id=charconsole0,path={console_log}",
            "-device", "isa-serial,chardev=charconsole0,index=0",
        ]

        # Extra 8250 UARTs (ttyS1..). Android init.rc in the MEmu image chmods
        # /dev/ttyS10-15 and chowns /dev/ttyS4, so those nodes must exist or init
        # logs "errno=9 /dev/ttyS<n>: no such file or directory" on every boot.
        # A PC only wires up 4 ISA UARTs, so the rest are exposed through PCI
        # multiport cards (pci-serial-4x = 4x 16550 each), one unix socket per
        # port so a host-side transport can attach later. Note the guest kernel
        # needs 8250.nr_uarts >= serial_ports + 1 for all of them to register.
        serial_ports = max(0, int(self.config.get("serial_ports", 24)))
        for card in range((serial_ports + 3) // 4):
            ports = []
            for slot in range(4):
                idx = card * 4 + slot + 1
                if idx > serial_ports:
                    break
                cmd += [
                    "-chardev",
                    f"socket,id=serial{idx},path=/tmp/lemu_{self.vmid}_serial{idx}.sock,server=on,wait=off",
                ]
                ports.append(f"chardev{slot + 1}=serial{idx}")
            if ports:
                cmd += ["-device", "pci-serial-4x," + ",".join(ports)]

        cmd += [
            "-vga", vga,
            "-display", display_arg,
            "-qmp", f"unix:{self.qmp_sock},server,nowait",
            "-pidfile", str(self.pid_file),
            "-daemonize",
        ]

        if self.config.get("audio", True):
            backend = self.config.get("audio_backend", "pa")
            cmd += ["-audiodev", f"{backend},id=snd0", "-device", "AC97,audiodev=snd0"]

        return cmd

    def start(self, headless: bool = False, display_backend: str | None = None) -> bool:
        if self.is_running():
            print(f"[!] Instance {self.vmid} is already running (PID: {self.get_pid()})")
            return True

        require_all()
        self.ensure_data_image()
        if self.qmp_sock.exists():
            self.qmp_sock.unlink(missing_ok=True)

        cmd = self.build_command(headless=headless, display_backend=display_backend)

        print(f"[*] Launching Lemu instance {self.vmid} ({self.config.get('name')})...")
        print(
            f"[*] Memory: {self.config.get('memory_mb', 4096)} MB | "
            f"Cores: {self.config.get('cpus', 4)} | "
            f"ADB Port: {self.config.get('adb_port', 21503 + self.vmid * 10)} | "
            f"Guest: {self.config.get('guest', DEFAULT_GUEST)}"
        )

        with open(self.log_file, "a", encoding="utf-8") as log_f:
            res = subprocess.run(cmd, stdout=log_f, stderr=log_f)

        if res.returncode != 0:
            print(f"[-] Failed to launch QEMU instance {self.vmid}. See {self.log_file}")
            return False

        # Wait for PID file
        for _ in range(20):
            if self.is_running():
                print(f"[+] Lemu instance {self.vmid} successfully started (PID: {self.get_pid()})")
                return True
            time.sleep(0.2)

        print("[-] Instance process timed out during startup.")
        return False

    def stop(self, timeout: float = 6.0) -> bool:
        pid = self.get_pid()
        if not pid:
            print(f"[!] Instance {self.vmid} is not running.")
            return True

        print(f"[*] Stopping instance {self.vmid} (PID {pid})...")
        # Try graceful QMP shutdown
        try:
            self._qmp_command("system_powerdown")
        except Exception:
            pass

        # Wait for exit
        t_start = time.time()
        while time.time() - t_start < timeout:
            if not self.is_running():
                print(f"[+] Instance {self.vmid} shut down cleanly.")
                self.qmp_sock.unlink(missing_ok=True)
                return True
            time.sleep(0.3)

        # Force terminate
        print("[!] Graceful shutdown timed out. Sending SIGTERM...")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

        time.sleep(1.0)
        if self.is_running():
            print("[!] Process still alive. Sending SIGKILL...")
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

        self.pid_file.unlink(missing_ok=True)
        self.qmp_sock.unlink(missing_ok=True)
        print(f"[+] Instance {self.vmid} stopped.")
        return True

    def _qmp_command(self, execute: str, arguments: dict | None = None) -> dict:
        if not self.qmp_sock.exists():
            raise RuntimeError(f"QMP socket {self.qmp_sock} does not exist.")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect(str(self.qmp_sock))
        try:
            s.recv(1024)  # greeting
            s.sendall(json.dumps({"execute": "qmp_capabilities"}).encode() + b"\n")
            s.recv(1024)
            payload = {"execute": execute}
            if arguments:
                payload["arguments"] = arguments
            s.sendall(json.dumps(payload).encode() + b"\n")
            resp = s.recv(4096)
            return json.loads(resp.decode())
        finally:
            s.close()

    def screendump(self, output_path: str) -> bool:
        try:
            res = self._qmp_command("screendump", {"filename": output_path})
            return "error" not in res
        except Exception as e:
            print(f"[-] screendump error: {e}")
            return False
