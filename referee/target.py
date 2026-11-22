"""SSH/SCP access to a QNX target (QEMU on localhost:2222 by default)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

HOME = Path.home()
KEY = Path(os.environ.get("REFEREE_KEY", HOME / ".ssh" / "referee_qnx"))
KNOWN_HOSTS = HOME / ".ssh" / "known_hosts_referee"


class TargetError(RuntimeError):
    pass


class Target:
    def __init__(self, host: str = "127.0.0.1", port: int = 2222, user: str = "qnxuser"):
        self.host, self.port, self.user = host, int(port), user

    @property
    def dest(self) -> str:
        return f"{self.user}@{self.host}"

    def _opts(self) -> list[str]:
        return ["-i", str(KEY),
                "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=10",
                "-o", "LogLevel=ERROR"]

    def ssh(self, command: str, timeout: float | None = 60, check: bool = True) -> subprocess.CompletedProcess:
        p = subprocess.run(["ssh", "-p", str(self.port), *self._opts(), self.dest, command],
                           capture_output=True, text=True, timeout=timeout)
        if check and p.returncode != 0:
            raise TargetError(f"ssh failed ({p.returncode}): {command}\n{p.stderr.strip()}")
        return p

    def put(self, local: Path, remote_path: str) -> None:
        p = subprocess.run(["scp", "-q", "-P", str(self.port), *self._opts(), str(local), f"{self.dest}:{remote_path}"],
                           capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            raise TargetError(f"scp to target failed: {local} -> {remote_path}\n{p.stderr.strip()}")

    def get(self, remote_path: str, local: Path) -> bool:
        p = subprocess.run(["scp", "-q", "-P", str(self.port), *self._opts(), f"{self.dest}:{remote_path}", str(local)],
                           capture_output=True, text=True, timeout=120)
        return p.returncode == 0

    def auth_methods(self) -> set[str]:
        """The login methods the RUNNING sshd offers (e.g. {'publickey', 'password'}), read
        from ssh's debug output for a user that doesn't exist. Sends no password."""
        p = subprocess.run(["ssh", "-v", "-p", str(self.port), "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
                            "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
                            "-o", "PubkeyAuthentication=no", "-o", "ConnectTimeout=10",
                            f"referee-auth-probe@{self.host}", "true"],
                           capture_output=True, text=True, timeout=30)
        for line in p.stderr.splitlines():
            key = "Authentications that can continue:"
            if key in line:
                return {m.strip() for m in line.split(key, 1)[1].split(",") if m.strip()}
        return set()

    def reachable(self) -> bool:
        try:
            return self.ssh("echo ok", timeout=20, check=False).stdout.strip() == "ok"
        except subprocess.TimeoutExpired:
            return False
