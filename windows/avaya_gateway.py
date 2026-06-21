#!/usr/bin/env python3
import datetime
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import threading
import time
import traceback


APP_NAME = "AvayaGateway"
AUTO_VALUES = {"", "auto", "detect", "dhcp"}


class Tee:
    def __init__(self, *streams):
        self.streams = [stream for stream in streams if stream is not None]

    def write(self, data):
        for stream in self.streams:
            try:
                stream.write(data)
                stream.flush()
            except Exception:
                pass

    def flush(self):
        for stream in self.streams:
            try:
                stream.flush()
            except Exception:
                pass


def app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundled_dir():
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def load_env_file(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def detect_local_ip_once():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((os.environ.get("SIP_REMOTE_HOST", "185.45.152.164"), 5060))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass

    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except OSError:
        pass

    return ""


def powershell_ip_for_interface(alias):
    if platform.system().lower() != "windows" or not alias:
        return ""
    escaped = alias.replace("'", "''")
    script = f"""
$cfg = Get-NetIPConfiguration |
  Where-Object {{ ($_.InterfaceAlias -eq '{escaped}' -or [string]$_.InterfaceIndex -eq '{escaped}') -and $_.IPv4Address }} |
  Select-Object -First 1
if ($cfg) {{
  [PSCustomObject]@{{ ip = @($cfg.IPv4Address)[0].IPAddress }} | ConvertTo-Json -Compress
}}
"""
    try:
        output = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=8,
        ).strip()
        if not output:
            return ""
        parsed = json.loads(output)
        return str(parsed.get("ip") or "")
    except Exception:
        return ""


def detect_local_ip():
    for _ in range(60):
        ip = detect_local_ip_once()
        if ip:
            return ip
        time.sleep(1)
    return "127.0.0.1"


def configure_environment(root):
    load_env_file(root / ".env")

    os.environ.setdefault("APP_ROOT", str(root))
    os.environ.setdefault("AVAYA_ENV_PATH", str(root / ".env"))
    os.environ.setdefault("AVAYA_LOG_DIR", str(root / "logs"))

    configured_host = os.environ.get("SIP_ADVERTISE_HOST", "auto").strip()
    configured_interface = os.environ.get("AVAYA_INTERFACE_ALIAS", "").strip()
    if configured_host.lower() in AUTO_VALUES:
        server_ip = powershell_ip_for_interface(configured_interface) or detect_local_ip()
    else:
        server_ip = configured_host

    os.environ["SIP_ADVERTISE_HOST"] = server_ip
    os.environ["AVAYA_INTERFACE_ALIAS"] = configured_interface
    os.environ.setdefault("SIP_ADVERTISE_PORT", "5060")
    os.environ.setdefault("SIP_REMOTE_HOST", "185.45.152.164")
    os.environ.setdefault("SIP_REMOTE_PORT", "5060")
    os.environ.setdefault("AVAYA_EXTENSION", "373316-100")
    os.environ.setdefault("AVAYA_DOMAIN", "pbx.zadarma.com")
    os.environ.setdefault("AVAYA_LOGO_LABEL", "LTM")

    configured_logo_url = os.environ.get("AVAYA_LOGO_URL", "auto").strip()
    if configured_logo_url.lower() in AUTO_VALUES:
        os.environ["AVAYA_LOGO_URL"] = f"http://{server_ip}/ltm-logo-232x140.jpg"

    os.environ.setdefault("HTTP_ROOT", str(root / "http"))
    os.environ.setdefault("HTTP_PORT", "80")
    os.environ.setdefault("SIP_PORT", "5060")
    os.environ.setdefault("SYSLOG_PORT", "514")
    os.environ.setdefault("ENABLE_HTTP", "1")
    os.environ.setdefault("ENABLE_SIP", "1")
    os.environ.setdefault("ENABLE_SYSLOG", "1")


def replace_or_add(lines, prefix_regex, new_line):
    changed = False
    found = False
    output = []
    for line in lines:
        if prefix_regex.match(line):
            found = True
            if line != new_line:
                changed = True
            output.append(new_line)
        else:
            output.append(line)
    if not found:
        output.append(new_line)
        changed = True
    return output, changed


def update_phone_settings(http_root, server_ip):
    settings_path = http_root / "46xxsettings.txt"
    if not settings_path.exists():
        print(f"WARNING: cannot update missing settings file: {settings_path}")
        return

    lines = settings_path.read_text(encoding="utf-8", errors="replace").splitlines()
    replacements = (
        (r"^SET\s+SIP_CONTROLLER_LIST\s+", f"SET SIP_CONTROLLER_LIST {server_ip}:5060;transport=udp"),
        (r"^SET\s+CONFIGURATION_SERVER\s+", f"SET CONFIGURATION_SERVER {server_ip}"),
        (r"^SET\s+LOGOS\s+", f"SET LOGOS {os.environ.get('AVAYA_LOGO_LABEL', 'LTM')}=http://{server_ip}/ltm-logo-232x140.jpg"),
        (r"^SET\s+LOGSRVR\s+", f"SET LOGSRVR {server_ip}"),
    )

    changed_any = False
    for pattern, new_line in replacements:
        lines, changed = replace_or_add(lines, re.compile(pattern, re.IGNORECASE), new_line)
        changed_any = changed_any or changed

    if changed_any:
        settings_path.write_text("\n".join(lines) + "\n", encoding="ascii")
        print(f"updated {settings_path} with detected IP {server_ip}")


def setup_logging(root):
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = open(log_dir / "avaya-gateway.log", "a", buffering=1, encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, log_file)
    sys.stderr = Tee(sys.__stderr__, log_file)
    print("")
    print(f"--- {APP_NAME} started {datetime.datetime.now().isoformat(timespec='seconds')} ---")
    return log_dir


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def supervise(name, target):
    while True:
        try:
            print(f"{name} starting")
            target()
        except Exception:
            print(f"{name} crashed; restarting in 5 seconds")
            traceback.print_exc()
            time.sleep(5)


def syslog_loop(log_path):
    port = int(os.environ.get("SYSLOG_PORT", "514"))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    print(f"syslog listening on udp/{port}, writing {log_path}")

    with open(log_path, "a", buffering=1, encoding="utf-8") as log_file:
        log_file.write(f"--- syslog started {datetime.datetime.now().isoformat()} ---\n")
        while True:
            data, addr = sock.recvfrom(65535)
            now = datetime.datetime.now().isoformat(timespec="seconds")
            message = data.decode("utf-8", "replace").rstrip()
            line = f"{now} {addr[0]}:{addr[1]} {message}"
            print(line)
            log_file.write(line + "\n")


def main():
    root = app_dir()
    configure_environment(root)
    log_dir = setup_logging(root)

    http_root = Path(os.environ["HTTP_ROOT"])
    if not http_root.exists():
        print(f"WARNING: HTTP_ROOT does not exist: {http_root}")
    else:
        update_phone_settings(http_root, os.environ["SIP_ADVERTISE_HOST"])

    shim_path = bundled_dir() / "avaya-shim" / "avaya_shim.py"
    shim = load_module("avaya_shim", shim_path)

    workers = []
    if os.environ.get("ENABLE_SIP", "1").lower() not in {"0", "false", "no"}:
        workers.append(("sip", shim.sip_proxy_loop))
    if os.environ.get("ENABLE_HTTP", "1").lower() not in {"0", "false", "no"}:
        workers.append(("http", shim.http_loop))
    if os.environ.get("ENABLE_SYSLOG", "1").lower() not in {"0", "false", "no"}:
        workers.append(("syslog", lambda: syslog_loop(log_dir / "avaya-syslog.log")))

    print(f"app dir: {root}")
    print(f"http root: {os.environ['HTTP_ROOT']}")
    print(f"server ip advertised to phone: {os.environ['SIP_ADVERTISE_HOST']}")
    print(f"sip remote: {os.environ['SIP_REMOTE_HOST']}:{os.environ['SIP_REMOTE_PORT']}")

    for name, target in workers:
        thread = threading.Thread(target=supervise, args=(name, target), daemon=True)
        thread.start()

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
