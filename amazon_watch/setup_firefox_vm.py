"""Install an isolated Firefox/driver in this project's user directory, without sudo."""
import json
import os
import hashlib
import shlex
import re
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

root = Path.home() / ".local/share/blackfriday-watch"
downloads = root / "browser-downloads"
downloads.mkdir(exist_ok=True)


def download(url, destination):
    if destination.exists():
        return
    request = urllib.request.Request(url, headers={"User-Agent": "BlackFridayWatch setup"})
    with urllib.request.urlopen(request, timeout=90) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


firefox = root / "browser/firefox/firefox"
if not firefox.exists():
    archive = downloads / "firefox.tar.xz"
    download("https://download.mozilla.org/?product=firefox-latest&os=linux64&lang=en-US", archive)
    (root / "browser").mkdir(exist_ok=True)
    with tarfile.open(archive) as handle:
        handle.extractall(root / "browser", filter="data")
driver = root / "browser/geckodriver"
if not driver.exists():
    with urllib.request.urlopen("https://api.github.com/repos/mozilla/geckodriver/releases/latest", timeout=30) as response:
        release = json.load(response)
    asset = next(x for x in release["assets"] if x["name"].endswith("linux64.tar.gz"))
    archive = downloads / "geckodriver.tar.gz"
    download(asset["browser_download_url"], archive)
    with tarfile.open(archive) as handle:
        handle.extractall(root / "browser", filter="data")

# Extract distribution libraries locally. Existing server packages/services are untouched.
packages = ["libgtk-3-0t64", "libatk1.0-0t64", "libatk-bridge2.0-0t64", "libasound2t64",
            "libdbus-glib-1-2", "libx11-xcb1", "libxt6t64", "libxrandr2", "libxdamage1", "libxcomposite1",
            "libxfixes3", "libxcursor1", "libxi6", "libxrender1", "libpangocairo-1.0-0", "libpango-1.0-0",
            "libcairo2", "libcairo-gobject2", "libgdk-pixbuf-2.0-0", "libatspi2.0-0t64", "libxkbcommon0",
            "libthai0", "libharfbuzz0b", "libxcb-render0", "libpixman-1-0", "libfribidi0", "libdatrie1",
            "libgraphite2-3", "libxcb-shm0", "libepoxy0", "libxtst6", "libxinerama1", "libcups2t64"]
packages.extend(["libpangoft2-1.0-0", "libfontconfig1", "libfreetype6", "libbrotli1"])
packages.extend(["libwayland-cursor0", "libwayland-client0", "libwayland-egl1", "libegl1", "libglvnd0"])
dependency_tree = subprocess.run(["apt-cache", "depends", "--recurse", "--no-recommends", "--no-suggests",
                                  "--no-conflicts", "--no-breaks", "--no-replaces", "--no-enhances",
                                  "libgtk-3-0t64", "libdbus-glib-1-2", "libasound2t64"], capture_output=True, text=True)
for name in dependency_tree.stdout.splitlines():
    if re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", name):
        installed = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Status}", name], capture_output=True, text=True)
        if installed.stdout.strip() != "installed":
            packages.append(name)
packages = list(dict.fromkeys(packages))
libraries = root / "browser-libs"
libraries.mkdir(exist_ok=True)
for package in packages:
    if not list(downloads.glob(package + "_*.deb")):
        result = subprocess.run(["apt-get", "download", package], cwd=downloads, capture_output=True, text=True, timeout=90)
        if result.returncode:
            # The regional Ubuntu mirror occasionally omits an indexed package.
            # Fetch the identical signed-index artifact from Ubuntu's main archive.
            uris = subprocess.run(["apt-get", "--print-uris", "download", package], capture_output=True, text=True)
            for line in uris.stdout.splitlines():
                parts = shlex.split(line)
                if len(parts) < 4 or not parts[0].startswith("http") or "/ubuntu/pool/" not in parts[0]:
                    continue
                suffix = parts[0].split("/ubuntu/pool/", 1)[1]
                destination = downloads / parts[1]
                try:
                    download("https://archive.ubuntu.com/ubuntu/pool/" + suffix, destination)
                    kind, digest = parts[3].split(":", 1)
                    if hashlib.new(kind.lower(), destination.read_bytes()).hexdigest() != digest:
                        destination.unlink()
                        raise RuntimeError("Ubuntu package checksum mismatch")
                except Exception as exc:
                    print(f"Library unavailable: {package}: {type(exc).__name__}", flush=True)
for archive in downloads.glob("*.deb"):
    subprocess.run(["dpkg-deb", "-x", str(archive), str(libraries)], check=True)
print("Firefox and driver installed in project directory", flush=True)
environment = {**os.environ, "LD_LIBRARY_PATH": str(firefox.parent) + ":" + str(libraries / "usr/lib/x86_64-linux-gnu")}
result = subprocess.run([str(firefox), "--version"], env=environment, capture_output=True, text=True)
print(result.stdout.strip(), flush=True)
if result.returncode:
    print(result.stderr[-600:], flush=True)
