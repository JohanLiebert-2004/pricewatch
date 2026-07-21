"""Reusable SSH tunnel for reaching the DB's private IP from machines
outside the OCI VCN - home PCs running the Big W / Chemist Warehouse local
sweeps, now that Postgres's public port 5432 is closed (P19 hardening).
Mirrors .github/actions/database-tunnel's approach (forward through the web
VM as a jump host) for use by scheduled local scripts instead of CI.

The web VM has a dedicated, low-privilege `ci-tunnel` Unix account whose
authorized_keys entries are all scoped `restrict,port-forwarding,
permitopen="10.42.1.9:5432"` - no shell, no PTY, no agent/X11 forwarding,
and forwarding is only ever permitted to the DB's private address. Each
consumer (GitHub Actions, this Windows PC, the Ubuntu laptop) has its own
dedicated key added to that same account; none of them share a private key.
"""
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit

TUNNEL_HOST = "159.13.59.184"
TUNNEL_USER = "ci-tunnel"
DB_PRIVATE_HOST = "10.42.1.9"
DB_PRIVATE_PORT = 5432

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_KNOWN_HOSTS = os.path.join(SCRIPT_DIR, "tunnel_known_hosts")


def _ssh_binary():
    # Windows 10+ ships OpenSSH's client at this path; Linux just has it on PATH.
    win_ssh = r"C:\Windows\System32\OpenSSH\ssh.exe"
    if os.name == "nt" and os.path.exists(win_ssh):
        return win_ssh
    return "ssh"


def _free_local_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def open_db_tunnel(ssh_key_path, known_hosts_path=None, local_port=None, timeout=15):
    """Open a local TCP forward to the DB's private address and yield
    'host:port' to connect through. Fails closed: never silently falls
    back to a direct/public connection.

    A fresh OS-assigned port is picked per call by default, so two sweeps
    that happen to overlap on the same machine can never race each other
    for the same local port."""
    local_port = local_port or _free_local_port()
    known_hosts_path = known_hosts_path or DEFAULT_KNOWN_HOSTS
    if not os.path.exists(ssh_key_path):
        raise FileNotFoundError(f"tunnel key not found: {ssh_key_path}")
    if not os.path.exists(known_hosts_path):
        raise FileNotFoundError(f"pinned known_hosts not found: {known_hosts_path}")
    proc = subprocess.Popen(
        [_ssh_binary(), "-i", ssh_key_path,
         "-o", f"UserKnownHostsFile={known_hosts_path}",
         "-o", "StrictHostKeyChecking=yes",
         "-o", "BatchMode=yes",
         "-o", "ExitOnForwardFailure=yes",
         "-o", "ServerAliveInterval=15",
         "-o", "ServerAliveCountMax=3",
         "-N", "-L", f"127.0.0.1:{local_port}:{DB_PRIVATE_HOST}:{DB_PRIVATE_PORT}",
         f"{TUNNEL_USER}@{TUNNEL_HOST}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + timeout
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(f"tunnel process exited early ({proc.returncode}): {out.strip()}")
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=0.5):
                    ready = True
                    break
            except OSError:
                time.sleep(0.3)
        if not ready:
            raise RuntimeError(f"tunnel did not become ready within {timeout}s")
        # The local forwarder can start accepting TCP before the SSH session
        # to the remote host has actually settled - a bare "port is open"
        # probe raced a session that closed a moment later (observed live:
        # sshd logged "session opened"/"session closed" one second apart,
        # and the crawl subprocess then hit a downstream ConnectionTimeout
        # against a tunnel that no longer existed). Give it a moment, then
        # re-confirm the process is still alive before trusting it.
        time.sleep(1.5)
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"tunnel died right after connecting ({proc.returncode}): {out.strip()}")
        yield f"127.0.0.1:{local_port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def tunneled_database_url(database_url, tunneled_host_port):
    """Rewrite DATABASE_URL's host:port to the local tunnel endpoint,
    keeping user/pass/dbname/sslmode untouched."""
    parts = urlsplit(database_url)
    userinfo = parts.netloc.rsplit("@", 1)[0] if "@" in parts.netloc else ""
    netloc = f"{userinfo}@{tunneled_host_port}" if userinfo else tunneled_host_port
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


if __name__ == "__main__":
    # Smoke test: open the tunnel and report readiness, for manual verification.
    key = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
        "~/.ssh/pricewatch_tunnel_local_ed25519")
    with open_db_tunnel(key) as endpoint:
        print(f"tunnel ready at {endpoint}")
        time.sleep(2)
    print("tunnel closed")
