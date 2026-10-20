"""
ADB client interface for Lemu instances.
"""
import subprocess
import time
from pathlib import Path
from lemu.config import load_instance_config

class LemuADB:
    def __init__(self, vmid: int = 0):
        self.vmid = vmid
        self.config = load_instance_config(vmid)
        self.port = self.config.get("adb_port", 21503 + (vmid * 10))
        self.target = f"127.0.0.1:{self.port}"

    def run_adb(self, *args, timeout: float = 30.0) -> subprocess.CompletedProcess:
        cmd = ["adb", "-s", self.target] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def connect(self) -> bool:
        cmd = ["adb", "connect", self.target]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0)
        return "connected" in res.stdout.lower()

    def disconnect(self):
        cmd = ["adb", "disconnect", self.target]
        subprocess.run(cmd, capture_output=True, text=True, timeout=5.0)

    def get_state(self) -> str:
        res = self.run_adb("get-state", timeout=3.0)
        return res.stdout.strip()

    def is_online(self) -> bool:
        self.connect()
        return self.get_state() == "device"

    def wait_for_device(self, max_wait: float = 30.0) -> bool:
        t0 = time.time()
        while time.time() - t0 < max_wait:
            if self.is_online():
                return True
            time.sleep(1.0)
        return False

    def install_app(self, apk_path: str | Path) -> bool:
        path = Path(apk_path).resolve()
        if not path.exists():
            print(f"[-] APK file not found: {path}")
            return False
        print(f"[*] Installing APK '{path.name}' to instance {self.vmid}...")
        self.connect()
        res = self.run_adb("install", "-r", "-g", str(path), timeout=120.0)
        print(res.stdout.strip())
        return "Success" in res.stdout

    def uninstall_app(self, package_name: str) -> bool:
        print(f"[*] Uninstalling '{package_name}' from instance {self.vmid}...")
        self.connect()
        res = self.run_adb("uninstall", package_name, timeout=30.0)
        print(res.stdout.strip())
        return "Success" in res.stdout

    def start_app(self, package_name: str, activity: str | None = None) -> bool:
        self.connect()
        if activity:
            comp = f"{package_name}/{activity}"
            res = self.run_adb("shell", "am", "start", "-n", comp)
        else:
            res = self.run_adb("shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1")
        return res.returncode == 0

    def stop_app(self, package_name: str) -> bool:
        self.connect()
        res = self.run_adb("shell", "am", "force-stop", package_name)
        return res.returncode == 0

    def shell(self, command: str) -> str:
        self.connect()
        res = self.run_adb("shell", command)
        return res.stdout
