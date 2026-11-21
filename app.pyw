#!/usr/bin/env python3
"""
7-Ordner-System Desktop-App Pro
Moderne, freundliche Windows-Oberflaeche:
- Quell- & Zielordner-Auswahl (Pull- & Push-Prinzip)
- Vorschau vor Ausfuehrung (Tabelle aller Dokumente & Zuordnungen)
- Anzeige des Quellpfads ("Gefunden in (Pfad)")
- Frische, farbige Duplikate-Steuerung mit Status-Buttons (Schritt 2):
  * Visuelle Farb-Hervorhebung des aktiven Modus (Bernstein / Smaragdgruen / Karminrot)
  * Saubere Zielordner-Spalte: Nur "—", kein stoerendes "wird uebersprungen"
  * "📋 Alle mitkopieren" / "⛔ Alle ueberspringen" / "🗑 Alle in Papierkorb loeschen"
  * Original ↔ Duplikat Rollentausch
  * "📄 In Standard-App oeffnen" per Doppelklick, Button & Kontextmenue
- Windows 11 Modern Look (kein Windows 97-Look, weiche Farben, Segoe UI, moderne Buttons)
- Zurueck-Knopf & Undo/Rueckgaengig-Funktion
"""

import os
import sys
import shutil
import json
import zlib
import re
import hashlib
import hmac
import secrets
import datetime
import calendar
import ctypes
import threading
import argparse
import base64
import io
import socket
import subprocess
import time
import urllib.parse
import urllib.request
import webbrowser
import time
try:
    import github_sync
except ModuleNotFoundError:
    import importlib.util as _ilu
    _gs_spec = _ilu.spec_from_file_location("github_sync", os.path.join(os.path.dirname(os.path.abspath(__file__)), "github_sync.py"))
    github_sync = _ilu.module_from_spec(_gs_spec)
    _gs_spec.loader.exec_module(github_sync)
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from tkinter import font as tkfont
import subprocess
from PIL import Image, ImageDraw, ImageTk, ImageOps

try:
    from send2trash import send2trash
    HAS_SEND2TRASH = True
except ImportError:
    HAS_SEND2TRASH = False

# Basis-Pfad (beruecksichtigt PyInstaller Frozen Exe)
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent


def is_network_path(path: Path | str) -> bool:
    """Erkennt UNC-Netzwerkpfade (\\\\Server\\Freigabe) sowie verbundene Windows-Netzlaufwerke."""
    s = str(path or "").strip()
    if not s:
        return False
    if s.startswith("\\\\") or s.startswith("//"):
        return True
    if os.name == "nt":
        try:
            drive = os.path.splitdrive(os.path.abspath(s))[0]
            if drive and len(drive) >= 2 and drive[1] == ":":
                if ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4:
                    return True
        except Exception:
            pass
    return False


def get_drive_display_info(drv: Path) -> tuple[bool, str]:
    """Liefert (ist_netzlaufwerk, Anzeigename) fuer einen Laufwerksbuchstaben."""
    drv_str = str(drv).rstrip("\\")
    if os.name != "nt":
        return False, drv_str
    is_net = False
    share_hint = ""
    try:
        is_net = ctypes.windll.kernel32.GetDriveTypeW(drv_str + "\\") == 4
        if is_net:
            buf = ctypes.create_unicode_buffer(512)
            buf_len = ctypes.c_ulong(len(buf))
            res = ctypes.windll.mpr.WNetGetConnectionW(drv_str, buf, ctypes.byref(buf_len))
            if res == 0 and buf.value:
                share_hint = buf.value.strip()
    except Exception:
        pass
    if is_net and share_hint:
        return True, f"{drv_str} ({share_hint})"
    return is_net, drv_str


def resolve_app_data_dir(base_dir: Path) -> Path:
    """Nutzt im Firmen-/Netzwerk-/Programme-Ordner sicher %APPDATA%\\7-Ordner-Pro, lokal sonst BASE_DIR."""
    low = str(base_dir).lower()
    pf_roots = [
        os.environ.get("ProgramFiles", "").lower(),
        os.environ.get("ProgramFiles(x86)", "").lower(),
        os.environ.get("ProgramW6432", "").lower(),
        os.environ.get("WINDIR", "C:\\Windows").lower(),
    ]
    is_shared_or_sys = is_network_path(base_dir) or any(r and (low == r or low.startswith(r + "\\")) for r in pf_roots)
    if not is_shared_or_sys:
        try:
            probe = base_dir / f".write_probe_{os.getpid()}.tmp"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return base_dir
        except Exception:
            pass
    app_data_root = Path(os.environ.get("APPDATA") or str(Path.home()))
    target = app_data_root / "7-Ordner-Pro"
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except Exception:
        return base_dir


DATA_DIR = resolve_app_data_dir(BASE_DIR)
INBOX_DIR = BASE_DIR / "01_Eingang_Inbox"
HISTORY_FILE = DATA_DIR / "sort_history.json"
WINDOW_STATE_FILE = DATA_DIR / "window_state.json"
CUSTOM_RULES_FILE = DATA_DIR / "custom_rules.json"
SECURITY_FILE = DATA_DIR / "security_config.json"
APP_VERSION = "v1.0.98"

_DEFAULT_SALT_HEX = "376f72646e65725f64656661756c745f73616c7432303236"
_APP_GUARD_HMAC_KEY = b"7-Ordner-Pro-IT-Guard-Integrity-2026"


def _hash_admin_password(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        120_000,
    ).hex()


_DEFAULT_ADMIN_PW_HASH = _hash_admin_password("1234", _DEFAULT_SALT_HEX)


# ------------------------------------------------------------------
# BRÜCKE ZU DATEI-SUCHMASCHINE PRO (App-Discovery, Favoriten & Start)
# ------------------------------------------------------------------

def get_shared_bridge_dir() -> Path:
    """Gemeinsames Austauschverzeichnis unter %APPDATA%\\7-Ordner-Pro."""
    root = Path(os.environ.get("APPDATA") or str(Path.home()))
    target = root / "7-Ordner-Pro"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return target


BRIDGE_FILE = get_shared_bridge_dir() / "app_bridge.json"


def _load_bridge_registry() -> dict:
    if BRIDGE_FILE.exists():
        try:
            data = json.loads(BRIDGE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def register_seven_folder_bridge() -> Path:
    """Registriert den aktuellen Startpfad von 7-Ordner-Pro für Datei-Suchmaschine Pro."""
    data = _load_bridge_registry()
    entry = {
        "version": APP_VERSION,
        "dir": str(BASE_DIR),
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    if getattr(sys, "frozen", False):
        entry["exe"] = sys.executable
    else:
        exe_cand = BASE_DIR / "7-Ordner-Pro.exe"
        if exe_cand.is_file():
            entry["exe"] = str(exe_cand)
        script_cand = Path(__file__).resolve()
        if script_cand.is_file():
            entry["script"] = str(script_cand)
    data["seven_folder"] = entry
    try:
        BRIDGE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return BRIDGE_FILE


CURRENT_THEME_MODE = "light"

THEME_PALETTES = {
    "light": {
        "bg_app": "#f8fafc",
        "bg_card": "#ffffff",
        "bg_input": "#ffffff",
        "bg_subtle": "#f1f5f9",
        "bg_hover": "#e2e8f0",
        "border": "#cbd5e1",
        "border_soft": "#e2e8f0",
        "fg_primary": "#0f172a",
        "fg_secondary": "#334155",
        "fg_muted": "#475569",
        "fg_hint": "#64748b",
        "fg_subtle": "#94a3b8",
        "accent": "#2563eb",
        "accent_hover": "#1d4ed8",
    },
    "dark": {
        "bg_app": "#0f172a",
        "bg_card": "#1e293b",
        "bg_input": "#0f172a",
        "bg_subtle": "#172234",
        "bg_hover": "#273549",
        "border": "#334155",
        "border_soft": "#334155",
        "fg_primary": "#f8fafc",
        "fg_secondary": "#e2e8f0",
        "fg_muted": "#cbd5e1",
        "fg_hint": "#94a3b8",
        "fg_subtle": "#64748b",
        "accent": "#2563eb",
        "accent_hover": "#3b82f6",
    },
}

_DARK_BG_REMAP = {
    "#f8fafc": "#0f172a",
    "#ffffff": "#1e293b",
    "#f1f5f9": "#172234",
    "#e2e8f0": "#273549",
    "#cbd5e1": "#334155",
    "#eff6ff": "#172554",
    "#dbeafe": "#1e3a8a",
    "#bfdbfe": "#1d4ed8",
    "#e0f2fe": "#0c4a6e",
    "#bae6fd": "#075985",
    "#f0f9ff": "#082f49",
    "#ecfeff": "#0f292e",
    "#cffafe": "#134e4a",
    "#fffbeb": "#422006",
    "#fef3c7": "#78350f",
    "#fde68a": "#78350f",
    "#ecfdf5": "#064e3b",
    "#dcfce7": "#14532d",
    "#d1fae5": "#065f46",
    "#fef2f2": "#450a0a",
    "#fee2e2": "#7f1d1d",
    "#ede9fe": "#312e81",
    "#ffedd5": "#431407",
}

_DARK_FG_REMAP = {
    "#0f172a": "#f8fafc",
    "#1e293b": "#f1f5f9",
    "#334155": "#e2e8f0",
    "#475569": "#cbd5e1",
    "#64748b": "#94a3b8",
    "#94a3b8": "#64748b",
    "#2563eb": "#60a5fa",
    "#1d4ed8": "#93c5fd",
    "#1e40af": "#93c5fd",
    "#0369a1": "#38bdf8",
    "#0f766e": "#2dd4bf",
    "#4f46e5": "#a5b4fc",
    "#dc2626": "#f87171",
    "#b91c1c": "#fca5a5",
    "#c2410c": "#fdba74",
    "#15803d": "#86efac",
    "#047857": "#34d399",
    "#b45309": "#fbbf24",
    "#92400e": "#fcd34d",
}

_DARK_BORDER_REMAP = {
    "#e2e8f0": "#334155",
    "#cbd5e1": "#334155",
    "#94a3b8": "#475569",
    "#93c5fd": "#2563eb",
    "#60a5fa": "#3b82f6",
    "#7dd3fc": "#0284c7",
    "#99f6e4": "#115e59",
    "#fca5a5": "#991b1b",
    "#c7d2fe": "#4338ca",
    "#fcd34d": "#92400e",
    "#6ee7b7": "#047857",
}


def theme_color(light_hex: str | None, role: str = "bg", mode: str | None = None) -> str | None:
    if not light_hex or not isinstance(light_hex, str):
        return light_hex
    active = (mode or CURRENT_THEME_MODE or "light").lower()
    if active != "dark":
        return light_hex
    low = light_hex.strip().lower()
    if role == "input_bg":
        return "#0f172a" if low in ("#ffffff", "#f8fafc", "#f1f5f9") else _DARK_BG_REMAP.get(low, light_hex)
    if role == "fg":
        if low == "#ffffff":
            return "#ffffff"
        return _DARK_FG_REMAP.get(low, light_hex)
    if role == "border":
        return _DARK_BORDER_REMAP.get(low, _DARK_BG_REMAP.get(low, light_hex))
    return _DARK_BG_REMAP.get(low, light_hex)


def get_shared_bridge_theme() -> str:
    reg = _load_bridge_registry()
    val = str(reg.get("theme") or "").strip().lower()
    return val if val in ("light", "dark") else "light"


def set_shared_bridge_theme(mode: str) -> str:
    clean = "dark" if str(mode or "").strip().lower() == "dark" else "light"
    try:
        BRIDGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        reg = _load_bridge_registry()
        reg["theme"] = clean
        reg["theme_updated_at"] = datetime.now().isoformat(timespec="seconds")
        BRIDGE_FILE.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return clean


def apply_dwm_dark_titlebar(win, dark: bool):
    """Aktiviert die native dunkle Windows-Titelleiste (DWMWA_USE_IMMERSIVE_DARK_MODE)."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = int(win.winfo_id())
        root_hwnd = ctypes.windll.user32.GetAncestor(hwnd, 2) or hwnd
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            try:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(root_hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
            except Exception:
                pass
    except Exception:
        pass


def apply_theme_to_widget_tree(widget, mode: str | None = None):
    """Aktualisiert rekursiv Hintergrund-, Rahmen- und Textfarben einer Tkinter-Hierarchie."""
    active = (mode or CURRENT_THEME_MODE or "light").lower()
    try:
        if not widget or not widget.winfo_exists():
            return
    except Exception:
        return

    # 1. Spezial-Widgets mit eigenem Theme-Refresh
    if hasattr(widget, "refresh_theme") and callable(widget.refresh_theme):
        try:
            widget.refresh_theme(active)
        except Exception:
            pass
    else:
        is_input = isinstance(widget, (tk.Entry, tk.Text, tk.Spinbox))
        if not hasattr(widget, "_light_theme_opts"):
            base_opts = {}
            for opt in ("bg", "fg", "highlightbackground", "highlightcolor", "insertbackground", "selectcolor", "activebackground", "activeforeground", "readonlybackground"):
                try:
                    val = str(widget.cget(opt))
                    if val.startswith("#"):
                        base_opts[opt] = val
                except Exception:
                    pass
            widget._light_theme_opts = base_opts

        base = getattr(widget, "_light_theme_opts", {})
        updates = {}
        for opt, orig in base.items():
            if opt in ("bg", "activebackground", "readonlybackground", "selectcolor"):
                role = "input_bg" if (is_input and opt in ("bg", "readonlybackground")) else "bg"
                updates[opt] = theme_color(orig, role=role, mode=active)
            elif opt in ("highlightbackground", "highlightcolor"):
                updates[opt] = theme_color(orig, role="border", mode=active)
            else:
                updates[opt] = theme_color(orig, role="fg", mode=active)
        if is_input and "bg" not in updates and "background" not in updates:
            updates["bg"] = "#0f172a" if active == "dark" else "#ffffff"
        if updates:
            try:
                widget.configure(**updates)
            except Exception:
                pass

    # 2. Kinder rekursiv aktualisieren (bei create_rounded_entry erledigt refresh_theme bereits alles)
    if not getattr(widget, "_handles_own_children_theme", False):
        try:
            for ch in widget.winfo_children():
                apply_theme_to_widget_tree(ch, active)
        except Exception:
            pass


def find_suchmaschine_app() -> dict:
    """Ermittelt Installationspfad, EXE, Skript, Config und Port von Datei-Suchmaschine Pro."""
    reg = _load_bridge_registry()
    sm = reg.get("suchmaschine", {}) if isinstance(reg.get("suchmaschine"), dict) else {}

    candidate_dirs = []
    if sm.get("dir"):
        candidate_dirs.append(Path(sm["dir"]))
    candidate_dirs.extend([
        BASE_DIR,
        BASE_DIR.parent / "Datei-Suchmaschine",
        BASE_DIR.parent,
    ])
    for root_scan in (BASE_DIR, BASE_DIR.parent):
        try:
            for sub in list(root_scan.iterdir())[:80]:
                if sub.is_dir() and not sub.name.startswith((".", "$")):
                    candidate_dirs.append(sub)
        except Exception:
            pass
    candidate_dirs.append(
        Path.home() / ".gemini" / "antigravity" / "scratch" / "Datei-Suchmaschine"
    )
    if getattr(sys, "frozen", False):
        try:
            candidate_dirs.append(Path(sys.executable).resolve().parent / "Datei-Suchmaschine")
        except Exception:
            pass

    res = {
        "port": int(sm.get("port") or 5005),
        "dir": "",
        "exe": "",
        "vbs": "",
        "script": "",
        "config_file": "",
    }

    for k in ("exe", "vbs", "script", "config_file"):
        val = str(sm.get(k) or "").strip()
        if val and os.path.isfile(val):
            res[k] = val
            if not res["dir"]:
                res["dir"] = str(Path(val).parent)

    for d in candidate_dirs:
        try:
            if not d or not d.is_dir():
                continue
            if not res["dir"]:
                res["dir"] = str(d)
            if not res["exe"]:
                p = d / "Datei-Suchmaschine-Pro.exe"
                if p.is_file():
                    res["exe"] = str(p)
            if not res["vbs"]:
                p = d / "Start_Hintergrund.vbs"
                if p.is_file():
                    res["vbs"] = str(p)
            if not res["script"]:
                p = d / "app.py"
                if p.is_file():
                    res["script"] = str(p)
            if not res["config_file"]:
                p = d / "config.json"
                if p.is_file():
                    res["config_file"] = str(p)
        except Exception:
            continue

    return res


def load_suchmaschine_favorites() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Liest gespeicherte Favoriten- & Netzwerk-Pfade aus Datei-Suchmaschine config.json."""
    info = find_suchmaschine_app()
    cfg_path = info.get("config_file")
    if not cfg_path or not os.path.isfile(cfg_path):
        return [], []
    try:
        cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    except Exception:
        return [], []
    saved = []
    network = []
    for item in (cfg.get("saved_paths") or []):
        if isinstance(item, dict) and item.get("path"):
            saved.append((str(item.get("name") or Path(item["path"]).name or item["path"]), str(item["path"])))
    for item in (cfg.get("network_paths") or []):
        if isinstance(item, dict) and item.get("path"):
            network.append((str(item.get("name") or item["path"]), str(item["path"])))
    return saved, network


def _save_path_to_suchmaschine_config(config_file: Path, path_str: str, name: str | None = None) -> bool:
    """Fügt einen neu sortierten Zielordner idempotent zu Datei-Suchmaschine config.json hinzu."""
    try:
        cfg = {}
        if config_file.is_file():
            cfg = json.loads(config_file.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict):
                cfg = {}
        saved = cfg.setdefault("saved_paths", [])
        norm = os.path.normpath(path_str)
        norm_low = norm.lower().rstrip("\\/")
        if not any(isinstance(it, dict) and str(it.get("path", "")).lower().rstrip("\\/") == norm_low for it in saved):
            entry_name = (name or Path(norm).name or norm).strip()
            saved.append({
                "id": f"sf_{int(time.time())}",
                "name": entry_name,
                "path": norm,
            })
            config_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def is_local_port_open(port: int = 5005, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, int(port))) == 0
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def open_path_in_suchmaschine(target_path: str | Path | None = None, auto_start: bool = True) -> dict:
    """Speichert optional target_path als Favorit, startet Datei-Suchmaschine bei Bedarf und öffnet es im Browser."""
    register_seven_folder_bridge()
    info = find_suchmaschine_app()
    port = int(info.get("port") or 5005)
    norm_path = os.path.normpath(str(target_path).strip()) if target_path and str(target_path).strip() else ""

    server_running = is_local_port_open(port)
    started_server = False

    if norm_path:
        if server_running:
            try:
                payload = json.dumps({"name": Path(norm_path).name or norm_path, "path": norm_path}).encode("utf-8")
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/save-path",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=1.5):
                    pass
            except Exception:
                if info.get("config_file"):
                    _save_path_to_suchmaschine_config(Path(info["config_file"]), norm_path)
        elif info.get("config_file"):
            _save_path_to_suchmaschine_config(Path(info["config_file"]), norm_path)

    def _build_suchmaschine_cmd():
        extra = ["--open-path", norm_path] if norm_path else []
        if info.get("exe") and os.path.isfile(info["exe"]):
            return [info["exe"], *extra], os.path.dirname(info["exe"]), True
        if info.get("script") and os.path.isfile(info["script"]):
            py_exe = sys.executable if not getattr(sys, "frozen", False) else "python"
            pyw = os.path.join(os.path.dirname(py_exe), "pythonw.exe")
            runner = pyw if os.path.isfile(pyw) else py_exe
            return [runner, info["script"], *extra], os.path.dirname(info["script"]), True
        if info.get("vbs") and os.path.isfile(info["vbs"]):
            return ["wscript.exe", info["vbs"]], os.path.dirname(info["vbs"]), False
        return None, None, False

    opened_native = False
    if not server_running and auto_start:
        cmd, cwd, has_native = _build_suchmaschine_cmd()
        if not cmd:
            return {
                "success": False,
                "error": "Datei-Suchmaschine Pro wurde auf diesem PC nicht gefunden.",
            }

        creation_flags = 0
        if os.name == "nt":
            creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            subprocess.Popen(cmd, cwd=cwd, creationflags=creation_flags)
            started_server = True
            opened_native = has_native
        except Exception as exc:
            return {"success": False, "error": f"Datei-Suchmaschine konnte nicht gestartet werden: {exc}"}

        deadline = time.monotonic() + 7.0
        while time.monotonic() < deadline:
            if is_local_port_open(port, timeout=0.25):
                server_running = True
                break
            time.sleep(0.2)

    url = f"http://localhost:{port}/"
    if norm_path:
        url = f"http://localhost:{port}/?open_path={urllib.parse.quote(norm_path)}"

    reused_existing = False
    if server_running and not started_server:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/bridge/open-in-active",
                data=json.dumps({"path": norm_path}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=1.2) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
                reused_existing = bool(body.get("reused_existing"))
        except Exception:
            reused_existing = False

    if not reused_existing and not opened_native:
        cmd, cwd, has_native = _build_suchmaschine_cmd()
        if cmd and has_native:
            try:
                cflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
                subprocess.Popen(cmd, cwd=cwd, creationflags=cflags)
                opened_native = True
            except Exception:
                opened_native = False
        if not opened_native:
            try:
                webbrowser.open(url)
            except Exception:
                pass

    return {
        "success": True,
        "url": url,
        "path": norm_path,
        "started_server": started_server,
        "server_running": server_running,
        "reused_existing": reused_existing,
    }


def load_security_config() -> dict:
    """Lädt die gehashte IT-Passwort-Konfiguration (Standard ab Werk: 1234)."""
    try:
        if SECURITY_FILE.exists():
            data = json.loads(SECURITY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("salt") and data.get("hash"):
                return {
                    "salt": str(data["salt"]),
                    "hash": str(data["hash"]),
                    "is_default": bool(data.get("is_default", False)),
                }
    except Exception:
        pass
    return {"salt": _DEFAULT_SALT_HEX, "hash": _DEFAULT_ADMIN_PW_HASH, "is_default": True}


def is_default_admin_password() -> bool:
    cfg = load_security_config()
    return bool(cfg.get("is_default", False) and hmac.compare_digest(cfg["hash"], _DEFAULT_ADMIN_PW_HASH))


def verify_admin_password(password: str) -> bool:
    """Prüft das eingegebene IT-Passwort per PBKDF2-SHA256 & konstant-zeitlichem Vergleich."""
    if not isinstance(password, str) or not password:
        return False
    cfg = load_security_config()
    try:
        cand = _hash_admin_password(password, cfg["salt"])
        return hmac.compare_digest(cand, cfg["hash"])
    except Exception:
        return False


def save_admin_password(new_password: str) -> None:
    """Speichert ein neues IT-Passwort gesalzen & gehasht (kein Klartext) in security_config.json."""
    clean = str(new_password or "")
    if len(clean) < 4:
        raise ValueError("Das Passwort muss mindestens 4 Zeichen lang sein.")
    salt_hex = secrets.token_hex(16)
    pw_hash = _hash_admin_password(clean, salt_hex)
    payload = {
        "salt": salt_hex,
        "hash": pw_hash,
        "is_default": False,
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    SECURITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECURITY_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def sign_guard_state(exclude_sys: bool, network_guard: bool) -> str:
    cfg = load_security_config()
    msg = f"{int(bool(exclude_sys))}:{int(bool(network_guard))}:{cfg['hash']}".encode("utf-8")
    return hmac.new(_APP_GUARD_HMAC_KEY, msg, hashlib.sha256).hexdigest()


def verify_guard_state(exclude_sys: bool, network_guard: bool, sig: str) -> bool:
    if not isinstance(sig, str) or not sig:
        return False
    expected = sign_guard_state(exclude_sys, network_guard)
    return hmac.compare_digest(sig, expected)
_ORIG_ASKYESNO = messagebox.askyesno
_ORIG_SHOWINFO = messagebox.showinfo
_ORIG_SHOWWARNING = messagebox.showwarning
_ORIG_SHOWERROR = messagebox.showerror

SEVEN_SYSTEM_ROOTS = {
    "01_eingang_inbox",
    "02_finanzen_steuern",
    "03_vertraege_versicherungen",
    "04_beruf_bildung",
    "05_gesundheit_familie",
    "06_wohnen_eigentum",
    "07_archiv_historie",
    "07_bilder",
    "08_programme_installer",
    "09_videos_medien",
    "10_ordner_projekte",
}

DOCUMENT_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".odt", ".ods",
    ".txt", ".csv", ".rtf", ".scan", ".epub", ".vstx", ".zip", ".json",
    ".html", ".htm", ".mht", ".mhtml"
}
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".bmp", ".gif",
    ".tif", ".tiff", ".webp", ".heic", ".heif", ".avif", ".svg", ".ico",
    ".raw", ".cr2", ".nef", ".arw", ".dng"
}
INSTALLER_EXTENSIONS = {".exe", ".msi", ".msix", ".appx", ".appxbundle", ".iso"}
MEDIA_EXTENSIONS = {
    ".ts", ".mts", ".m2ts", ".mpeg", ".mpg", ".mpe", ".mp4", ".m4v",
    ".mkv", ".avi", ".mov", ".wmv", ".webm", ".flv", ".vob", ".3gp",
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"
}
EBOOK_EXTENSIONS = {".epub", ".mobi", ".azw", ".azw3", ".fb2", ".lit", ".ibooks"}
TEXT_DOC_EXTENSIONS = {
    ".txt", ".pdf", ".doc", ".docx", ".rtf", ".md", ".log", ".scan",
    ".epub", ".mobi", ".azw", ".azw3", ".fb2", ".vstx", ".zip", ".json", ".xml", ".html", ".htm", ".mht", ".mhtml"
}
OFFICE_EXTENSIONS = {
    ".docx", ".doc", ".docm", ".dotx", ".dotm",
    ".xlsx", ".xls", ".xlsm", ".xlsb", ".xltx", ".xltm",
    ".pptx", ".ppt", ".pptm", ".ppsx", ".pps",
    ".odt", ".ods", ".odp", ".csv"
}
PROTECTED_APP_FILES = {"7-ordner-pro.exe", "sort_history.json", "window_state.json", "custom_rules.json"}
ALL_EXTENSIONS = DOCUMENT_EXTENSIONS | IMAGE_EXTENSIONS | INSTALLER_EXTENSIONS | MEDIA_EXTENSIONS | EBOOK_EXTENSIONS | TEXT_DOC_EXTENSIONS | OFFICE_EXTENSIONS
VALID_EXTENSIONS = ALL_EXTENSIONS

IGNORED_DIRS = {
    "system volume information", "$recycle.bin", "windows", "program files",
    "program files (x86)", "appdata", "node_modules", ".git", ".idea", ".vscode"
}

SYSTEM_NOISE_DIRS = {
    "programdata", "all users", "perflogs", "recovery",
    "$windows.~bt", "$windows.~ws", "$winreagent", "msocache", "config.msi",
    "winsxs", "driverstore", "softwaredistribution",
    "__pycache__", ".venv", "venv", "site-packages", "dist-packages",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nuget", ".cache",
    "onedrivetemp", "system32", "syswow64",
}
SYSTEM_DRIVE_ROOT_NOISE_DIRS = {"intel", "amd", "nvidia", "drivers", "inetpub", "swsetup", "dell", "hp"}
PYTHON_RUNTIME_DIR_REGEX = re.compile(r"^python\d{2,4}(?:[._-]\d+)?$", re.IGNORECASE)


def is_system_noise_dir(dir_path: Path) -> bool:
    """Prueft, ob ein Unterordner ein typischer Windows-, Treiber- oder Entwicklerordner ist."""
    name_low = dir_path.name.lower()
    if name_low in SYSTEM_NOISE_DIRS:
        return True
    if PYTHON_RUNTIME_DIR_REGEX.match(name_low):
        return True
    if name_low in SYSTEM_DRIVE_ROOT_NOISE_DIRS:
        try:
            parent = dir_path.parent
            if (dir_path.drive and str(parent) == dir_path.drive + "\\") or (dir_path.anchor and str(parent) == dir_path.anchor):
                return True
        except Exception:
            pass
    return False

DUP_SUFFIX_REGEX = re.compile(r'(\s+\d+|\s*\(\d+\)|_kopie|_copy|_\d+)$', re.IGNORECASE)

_WIN_ATTR_OFFLINE = 0x00001000
_WIN_ATTR_RECALL_ON_OPEN = 0x00040000
_WIN_ATTR_RECALL_ON_DATA_ACCESS = 0x00400000
_WIN_CLOUD_PLACEHOLDER_MASK = _WIN_ATTR_OFFLINE | _WIN_ATTR_RECALL_ON_OPEN | _WIN_ATTR_RECALL_ON_DATA_ACCESS


def is_cloud_only_file(path: Path) -> bool:
    """Prueft nur ueber NTFS-Metadaten (ohne Oeffnen!), ob eine Datei reiner Cloud-/OneDrive-Platzhalter ist."""
    try:
        st = os.stat(path, follow_symlinks=False)
        attrs = getattr(st, "st_file_attributes", 0)
        return bool(attrs & _WIN_CLOUD_PLACEHOLDER_MASK)
    except Exception:
        return False


def get_onedrive_roots() -> list[Path]:
    """Liefert bekannte OneDrive-Stammverzeichnisse des aktuellen Windows-Benutzers."""
    roots = []
    seen = set()
    for env_key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        val = str(os.environ.get(env_key, "") or "").strip()
        if not val:
            continue
        try:
            p = Path(val).resolve()
        except Exception:
            p = Path(val)
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            roots.append(p)
    return roots


def get_onedrive_accounts() -> dict[str, list[tuple[str, Path]]]:
    """Ermittelt getrennte OneDrive-Ordner fuer Privat (Personal) und Firma (Business/SharePoint)."""
    personal: list[tuple[str, Path]] = []
    business: list[tuple[str, Path]] = []
    seen_personal: set[str] = set()
    seen_business: set[str] = set()

    def _add(bucket: list[tuple[str, Path]], seen: set[str], label: str, raw_path: str | Path | None):
        if not raw_path:
            return
        try:
            p = Path(str(raw_path).strip())
            if not p.exists() or not p.is_dir():
                return
            p_res = p.resolve()
        except Exception:
            return
        key = str(p_res).lower()
        if key not in seen:
            seen.add(key)
            bucket.append((label.strip() or p_res.name, p_res))

    # 1. Windows-Registry (liefert zuverlaessig getrennte Personal- & Business-Konten inkl. Anzeigename)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\OneDrive\Accounts") as base_key:
            idx = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(base_key, idx)
                    idx += 1
                except OSError:
                    break
                try:
                    with winreg.OpenKey(base_key, sub_name) as sub_key:
                        try:
                            user_folder, _ = winreg.QueryValueEx(sub_key, "UserFolder")
                        except OSError:
                            user_folder = ""
                        try:
                            disp_name, _ = winreg.QueryValueEx(sub_key, "DisplayName")
                        except OSError:
                            disp_name = ""
                        try:
                            is_biz, _ = winreg.QueryValueEx(sub_key, "Business")
                        except OSError:
                            is_biz = 1 if sub_name.lower().startswith("business") else 0
                        if user_folder:
                            if is_biz:
                                _add(business, seen_business, str(disp_name or "OneDrive Firma"), str(user_folder))
                            else:
                                _add(personal, seen_personal, str(disp_name or "OneDrive Privat"), str(user_folder))
                except Exception:
                    pass
    except Exception:
        pass

    # 2. Umgebungsvariablen (OneDriveConsumer / OneDriveCommercial / OneDrive)
    _add(personal, seen_personal, "OneDrive Privat", os.environ.get("OneDriveConsumer", ""))
    _add(business, seen_business, "OneDrive Firma", os.environ.get("OneDriveCommercial", ""))

    env_od = str(os.environ.get("OneDrive", "") or "").strip()
    if env_od:
        try:
            p_od = Path(env_od).resolve()
            key_od = str(p_od).lower()
            name_low = p_od.name.lower()
            if key_od not in seen_business and key_od not in seen_personal:
                if name_low.startswith("onedrive -") and "personal" not in name_low and "persönlich" not in name_low:
                    _add(business, seen_business, p_od.name, p_od)
                else:
                    _add(personal, seen_personal, "OneDrive Privat", p_od)
        except Exception:
            pass

    # 3. Standardordner unter %USERPROFILE% durchsuchen
    try:
        home = Path.home()
        if (home / "OneDrive").exists():
            p_def = (home / "OneDrive").resolve()
            if str(p_def).lower() not in seen_business:
                _add(personal, seen_personal, "OneDrive Privat", p_def)
        for child in home.iterdir():
            if not child.is_dir():
                continue
            nl = child.name.lower()
            if nl.startswith("onedrive -") or nl.startswith("onedrive-"):
                res_key = str(child.resolve()).lower()
                if "personal" in nl or "persönlich" in nl:
                    if res_key not in seen_business:
                        _add(personal, seen_personal, child.name, child)
                else:
                    if res_key not in seen_personal:
                        _add(business, seen_business, child.name, child)
    except Exception:
        pass

    return {"personal": personal, "business": business}


def find_onedrive_executable() -> Path | None:
    """Sucht die installierte Windows-OneDrive-Anwendung (OneDrive.exe)."""
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "OneDrive" / "OneDrive.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft OneDrive" / "OneDrive.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft OneDrive" / "OneDrive.exe",
    ]
    for cand in candidates:
        try:
            if str(cand) and cand.is_file():
                return cand
        except Exception:
            pass
    return None


def launch_onedrive_signin() -> tuple[bool, str]:
    """Startet den Windows-OneDrive-Anmeldeassistenten fuer Privat- oder Firmenkonten."""
    exe = find_onedrive_executable()
    if exe is not None:
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen([str(exe), "/configure"], creationflags=flags)
            return True, (
                "Die Microsoft OneDrive-Anmeldung wurde gestartet.\n"
                "Nach dem Einloggen erscheint Ihr OneDrive-Ordner hier automatisch unter „Direktwahl“."
            )
        except Exception:
            try:
                os.startfile(str(exe))
                return True, "Microsoft OneDrive wurde gestartet. Bitte melden Sie sich dort mit Ihrem Konto an."
            except Exception as e:
                return False, f"OneDrive konnte nicht gestartet werden:\n{e}"
    try:
        os.startfile("odopen://sync")
        return True, "Die OneDrive-Anmeldung wurde über Windows geöffnet."
    except Exception:
        return False, (
            "Microsoft OneDrive wurde auf diesem PC nicht gefunden.\n"
            "Sie können OneDrive kostenlos über Windows oder office.com installieren und danach hier nutzen."
        )


def connect_network_share(unc_path: str, username: str = "", password: str = "") -> tuple[bool, str]:
    """Verbindet optional einen Netzwerkpfad (\\\\Server\\Freigabe) mit Anmeldedaten ueber Windows 'net use'."""
    raw = str(unc_path or "").strip().strip('"').rstrip("\\")
    if not raw:
        return False, "Bitte geben Sie einen gültigen Netzwerkpfad ein (z. B. \\\\Server\\Freigabe)."
    if username.strip() or password:
        cmd = ["net", "use", raw]
        if password:
            cmd.append(password)
        cmd.append(f"/user:{username.strip()}" if username.strip() else "")
        cmd = [part for part in cmd if part]
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15, creationflags=flags)
            if res.returncode != 0:
                err_txt = (res.stderr or res.stdout or "").strip()
                return False, err_txt or "Verbindung mit dem Server fehlgeschlagen."
        except Exception as e:
            return False, f"Fehler beim Verbinden des Netzlaufwerks:\n{e}"
    return True, raw


def is_onedrive_dir(dir_path: Path, onedrive_roots: list[Path] | None = None) -> bool:
    """Erkennt OneDrive-Ordner anhand des Namens oder der Windows-OneDrive-Umgebungspfade."""
    name_low = dir_path.name.lower()
    if name_low == "onedrive" or name_low.startswith("onedrive -") or name_low.startswith("onedrive-"):
        return True
    roots = get_onedrive_roots() if onedrive_roots is None else onedrive_roots
    if roots:
        try:
            d_res = dir_path.resolve()
        except Exception:
            d_res = dir_path
        for root in roots:
            if d_res == root or root in d_res.parents:
                return True
    return False

def is_7_folder_archive(dir_path: Path) -> bool:
    """Prueft, ob ein Verzeichnis bereits ein 7-Ordner-Archiv ist, um Re-Scan zu vermeiden."""
    try:
        subnames = {sub.name for sub in dir_path.iterdir() if sub.is_dir()}
        return "01_Eingang_Inbox" in subnames and ("07_Archiv_Historie" in subnames or "07_Bilder" in subnames)
    except Exception:
        return False

# Regelwerk fuer 7-Ordner-System mit 2-stelligen Unterordnern (01_ bis 07_)
RULES = [
    # 02 Finanzen & Steuern
    {
        "target": "02_Finanzen_Steuern/01_Bank_Kontoauszuege",
        "label": "Bank / Kontoauszug",
        "keywords": ["kontoauszug", "sparkasse", "dkb", "ing", "comdirect", "bank", "visa", "mastercard", "paypal", "ueberweisung"]
    },
    {
        "target": "02_Finanzen_Steuern/02_Steuererklaerung",
        "label": "Steuern / Finanzamt",
        "keywords": ["steuern", "steuererklaerung", "steuererklärung", "finanzamt", "elster", "lohnsteuer", "einkommensteuer", "steuerbescheid"]
    },
    {
        "target": "02_Finanzen_Steuern/03_Geldanlage_Vorsorge",
        "label": "Depot / Vorsorge / Boerse",
        "keywords": ["depot", "trade_republic", "scalable", "aktien", "etf", "fonds", "bauspar", "rente", "altersvorsorge", "börse", "boerse", "investor", "pitchdeck", "inventur", "jwdw"]
    },
    {
        "target": "02_Finanzen_Steuern/04_Kredite_Darlehen",
        "label": "Kredite & Darlehen",
        "keywords": ["kredit", "darlehen", "tilgung", "zinsen", "finanzierung", "schufa"]
    },
    {
        "target": "02_Finanzen_Steuern/05_Quittungen_Belege",
        "label": "Quittungen & Barbelege",
        "keywords": ["quittung", "barzahlung", "bewirtung"]
    },

    # 03 Vertraege & Versicherungen
    {
        "target": "03_Vertraege_Versicherungen/01_Versicherungen",
        "label": "Versicherungen",
        "keywords": ["haftpflicht", "hausrat", "versicherung", "allianz", "huk", "ergo", "devk", "unfallversicherung", "rechtsschutz", "versicherungsbestaetigung"]
    },
    {
        "target": "03_Vertraege_Versicherungen/02_Vertraege_Mobilfunk_Internet",
        "label": "Mobilfunk & Internet",
        "keywords": ["telekom", "vodafone", "o2", "1und1", "mobilfunk", "internet", "router", "handy", "dsl", "simkarte"]
    },
    {
        "target": "03_Vertraege_Versicherungen/03_Fahrzeuge_Kfz",
        "label": "Kfz & Fahrzeuge",
        "keywords": ["kfz", "auto", "tuev", "tüv", "adac", "zulassung", "fahrzeugschein", "strafzettel", "bußgeld", "utzfahrzeuge", "fahrzeug"]
    },
    {
        "target": "03_Vertraege_Versicherungen/04_Mitgliedschaften_Abos",
        "label": "Mitgliedschaften & Abos",
        "keywords": ["mitglied", "verein", "fitnessstudio", "abonnement", "abo", "bahncard"]
    },
    {
        "target": "03_Vertraege_Versicherungen/05_Vollmachten_Verfuegungen",
        "label": "Vollmachten & Verfügungen",
        "keywords": ["vollmacht", "patientenverfuegung", "vorsorgevollmacht", "testament", "notar"]
    },

    # 04 Beruf & Bildung
    {
        "target": "04_Beruf_Bildung/01_Gehaltsabrechnungen",
        "label": "Gehalt / Lohn",
        "keywords": ["gehalt", "entgelt", "lohn", "lohnabrechnung", "gehaltsabrechnung", "bezuegemitteilung"]
    },
    {
        "target": "04_Beruf_Bildung/02_Arbeitsvertraege",
        "label": "Arbeitsverträge / Aufträge",
        "keywords": ["arbeitsvertrag", "anstellungsvertrag", "kuendigung", "aufhebungsvertrag", "abmahnung", "abwart", "aufträge", "auftraege"]
    },
    {
        "target": "04_Beruf_Bildung/03_Zeugnisse_Zertifikate",
        "label": "Technik & Dokumente",
        "keywords": ["zeugnis", "zertifikat", "diplom", "bachelor", "master", "weiterbildung", "schulzeugnis", "verriegelung", "spanner", "antrieb", "taenzer", "vstx"]
    },
    {
        "target": "04_Beruf_Bildung/04_Bewerbungen_Lebenslauf",
        "label": "Bewerbungen & Lebenslauf",
        "keywords": ["lebenslauf", "bewerbung", "anschreiben", "motivationsschreiben"]
    },

    # 05 Gesundheit & Familie
    {
        "target": "05_Gesundheit_Familie/01_Krankenkasse",
        "label": "Krankenkasse",
        "keywords": ["aok", "tk", "techniker", "barmer", "krankenkasse", "erstattung", "krankengeld", "bonusprogramm"]
    },
    {
        "target": "05_Gesundheit_Familie/02_Aerzte_Befunde",
        "label": "Gesundheit & Ernährung",
        "keywords": ["arzt", "befund", "rezept", "krankenhaus", "labor", "impfung", "zahnarzt", "patientenakte", "diagnose", "fasten", "glucose", "ernaehrung", "muffin", "kalorien", "halbrahm"]
    },
    {
        "target": "05_Gesundheit_Familie/03_Familienurkunden",
        "label": "Familienurkunden",
        "keywords": ["geburtsurkunde", "heiratsurkunde", "stammbuch", "taufschein", "sterbeurkunde"]
    },
    {
        "target": "05_Gesundheit_Familie/04_Impfungen_Vorsorge",
        "label": "Impfungen & Vorsorge",
        "keywords": ["impfpass", "impfbuch", "vorsorgepass", "mutterpass"]
    },

    # 06 Wohnen & Eigentum
    {
        "target": "06_Wohnen_Eigentum/01_Miete_Nebenkosten",
        "label": "Miete & Umzug",
        "keywords": ["miete", "mietvertrag", "nebenkosten", "betriebskosten", "heizkosten", "vermieter", "strom", "gas", "wasser", "vattenfall", "eon", "auszug", "reinigung", "mietverband", "mv-merkbl", "umzugsangebot"]
    },
    {
        "target": "06_Wohnen_Eigentum/02_Geraete_Garantien_Belege",
        "label": "Belege, Rechnungen & Einkäufe",
        "keywords": ["garantie", "rechnung", "kassenbon", "mediamarkt", "amazon", "saturn", "otto", "beleg", "lieferschein", "kaufbeleg", "samsung", "jura", "entkalkung", "sideboard", "topper", "jysk", "coop", "ricardo", "chf"]
    },
    {
        "target": "06_Wohnen_Eigentum/03_Handwerker_Renovierung",
        "label": "Handwerker & Installationen",
        "keywords": ["handwerker", "renovierung", "maler", "sanitaer", "wartung", "schornsteinfeger", "installation", "sicherheitsnachweis"]
    },
    {
        "target": "06_Wohnen_Eigentum/04_Hausverwaltung_Grundbuch",
        "label": "Hausverwaltung & Grundbuch",
        "keywords": ["grundbuch", "hausverwaltung", "eigentuemer", "wohnungseigentum", "wirtschaftsplan"]
    },

    # 07 Archiv & Historie
    {
        "target": "07_Archiv_Historie/01_Ausweise_Reisepaesse",
        "label": "Ausweise & Pässe",
        "keywords": ["ausweis", "reisepass", "fuehrerschein", "personalausweis", "meldebescheinigung", "pass"]
    },
    {
        "target": "07_Archiv_Historie/02_Buecher_Medien",
        "label": "Bücher & Medien",
        "keywords": ["teufelsleib", "franz, andreas", "epub", "roman", "buch", "kochbuch", "backbuch", "ebook", "e-book"]
    },
    {
        "target": "07_Archiv_Historie/03_Abgeschlossenes",
        "label": "Archiv & Alt",
        "keywords": ["archiv", "historisch", "abgeschlossen", "alt", "geburtstag"]
    }
]

def extract_pdf_keywords(path: Path) -> str:
    """Extrahiert Text aus PDF-Streams fuer inhaltsbasierte Sortierung."""
    try:
        data = path.read_bytes()
        streams = re.findall(rb'stream[\r\n]+(.*?)[\r\n]+endstream', data, re.DOTALL)
        found_words = []
        for s in streams[:12]:
            try:
                decomp = zlib.decompress(s).decode('latin1', 'ignore')
                words = re.findall(r'[A-Za-z0-9äöüÄÖÜß\.\-_]{4,}', decomp)
                for w in words:
                    w_lower = w.lower()
                    if any(term in w_lower for term in ['coop', 'migros', 'rechnung', 'auftrag', 'schweiz', 'konto', 'steuererkl', 'finanzamt', 'miete', 'versicherung', 'gehalt']):
                        found_words.append(w_lower)
            except Exception:
                pass
        return " ".join(found_words)
    except Exception:
        return ""

def file_hash(path: Path) -> str:
    hasher = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return ""

def format_file_size(size_bytes: int) -> str:
    if size_bytes < 0:
        return "—"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


_PDF_PREVIEW_CACHE: dict[tuple, tuple] = {}


def _is_readable_pdf_text(text: str) -> bool:
    """Filtert binaere CID-/Zeichensatz-Kryptik (z. B. '!"#$%&...') aus PDF-Streams heraus."""
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) < 12:
        return False
    # Typische Sequenz aus CID/CMap-Tabellen abweisen
    if '!"#$%&' in clean or "#$%&'()" in clean:
        return False
    letters = sum(1 for ch in clean if ch.isalpha())
    strange = sum(
        1 for ch in clean
        if not (ch.isalnum() or ch.isspace() or ch in ".,;:!?-_()[]/€$%&+@#*„“‚‘'\"°=")
    )
    if letters < max(8, len(clean) * 0.42):
        return False
    if strange > len(clean) * 0.12:
        return False
    # Mindestens einige normale Wörter mit >=3 Buchstaben verlangen
    real_words = re.findall(r"[A-Za-zäöüÄÖÜß]{3,}", clean)
    return len(real_words) >= 3


def _render_pdf_page_image(path: Path, max_img_size: tuple[int, int] = (310, 250)) -> tuple[Image.Image | None, int]:
    """Rendert Seite 1 eines PDFs ueber die integrierte Windows-PDF-Engine (ohne Zusatzpakete)."""
    if os.name != "nt" or not path.is_file():
        return None, 0
    try:
        st = path.stat()
        cache_key = (str(path).lower(), float(st.st_mtime), int(st.st_size), tuple(max_img_size))
    except Exception:
        cache_key = None

    if cache_key and cache_key in _PDF_PREVIEW_CACHE:
        cached_img, cached_pages = _PDF_PREVIEW_CACHE[cache_key]
        return (cached_img.copy() if cached_img is not None else None), cached_pages

    ps = """
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}
function AwaitAction($WinRtTask) {
    $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
}
[Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime] | Out-Null
$inPath = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('__B64__'))
$fs = [System.IO.File]::OpenRead($inPath)
try {
    $ras = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($fs)
    $pdf = Await ([Windows.Data.Pdf.PdfDocument]::LoadFromStreamAsync($ras)) ([Windows.Data.Pdf.PdfDocument])
    $page = $pdf.GetPage(0)
    $opts = New-Object Windows.Data.Pdf.PdfPageRenderOptions
    $opts.DestinationWidth = __WIDTH__
    $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    AwaitAction ($page.RenderToStreamAsync($stream, $opts))
    $netStream = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($stream)
    $netStream.Position = 0
    $ms = New-Object System.IO.MemoryStream
    $netStream.CopyTo($ms)
    $b64Png = [System.Convert]::ToBase64String($ms.ToArray())
    Write-Output ($pdf.PageCount.ToString() + '|' + $b64Png)
} finally {
    $fs.Dispose()
}
"""
    b64_path = base64.b64encode(str(path).encode("utf-8")).decode("ascii")
    dest_w = max(480, min(960, int(max_img_size[0] * 2)))
    ps_cmd = ps.replace("__B64__", b64_path).replace("__WIDTH__", str(dest_w))
    enc = base64.b64encode(ps_cmd.encode("utf-16-le")).decode("ascii")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
            capture_output=True,
            text=True,
            encoding="ascii",
            errors="ignore",
            timeout=5,
            creationflags=creation_flags,
        )
        if proc.returncode != 0 or "|" not in (proc.stdout or ""):
            return None, 0
        line = proc.stdout.strip().splitlines()[-1]
        pages_str, b64_png = line.split("|", 1)
        page_count = int(pages_str)
        png_bytes = base64.b64decode(b64_png)
        with Image.open(io.BytesIO(png_bytes)) as raw_pdf_img:
            rendered = raw_pdf_img.convert("RGB")
        rendered.thumbnail(max_img_size, Image.Resampling.LANCZOS)
        if cache_key:
            if len(_PDF_PREVIEW_CACHE) >= 32:
                _PDF_PREVIEW_CACHE.pop(next(iter(_PDF_PREVIEW_CACHE)))
            _PDF_PREVIEW_CACHE[cache_key] = (rendered.copy(), page_count)
        return rendered, page_count
    except Exception:
        return None, 0


def extract_file_preview_data(path: Path, is_dir: bool = False, max_img_size: tuple[int, int] = (310, 250)) -> dict:
    """Erstellt eine leichte, sichere In-App-Vorschau (Bild, Text, Office/PDF-Auszug, ZIP oder Ordnerliste)."""
    info = {"kind": "info", "badge": "", "text": "", "image": None}
    if not path.exists():
        info["text"] = "Die Datei oder der Ordner ist am Quellpfad nicht mehr vorhanden."
        return info

    if is_dir or path.is_dir():
        info["kind"] = "dir"
        entries = []
        total_items = 0
        try:
            for child in sorted(path.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
                total_items += 1
                if len(entries) < 30:
                    prefix = "📁 " if child.is_dir() else "📄 "
                    entries.append(prefix + child.name)
        except Exception as exc:
            info["text"] = f"Ordnerinhalt konnte nicht gelesen werden:\n{exc}"
            return info
        info["badge"] = "1 Eintrag" if total_items == 1 else f"{total_items} Einträge"
        if total_items > 30:
            entries.append(f"\n… und {total_items - 30} weitere Einträge")
        info["text"] = "\n".join(entries) if entries else "Dieser Ordner ist leer."
        return info

    ext = path.suffix.lower()
    pil_image_exts = {".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".ico"}
    if ext in pil_image_exts:
        try:
            with Image.open(path) as raw_img:
                fixed = ImageOps.exif_transpose(raw_img)
                orig_w, orig_h = fixed.size
                if fixed.mode not in ("RGB", "RGBA"):
                    fixed = fixed.convert("RGBA")
                else:
                    fixed = fixed.copy()
            fixed.thumbnail(max_img_size, Image.Resampling.LANCZOS)
            info["kind"] = "image"
            info["badge"] = f"{orig_w} × {orig_h} px"
            info["image"] = fixed
            return info
        except Exception:
            info["badge"] = f"Bild ({ext.upper().lstrip('.')})"
            info["text"] = "Für dieses Bildformat steht keine direkte Bildvorschau zur Verfügung."
            return info

    text_exts = {
        ".txt", ".csv", ".json", ".log", ".md", ".ini", ".cfg", ".xml",
        ".html", ".htm", ".py", ".ps1", ".bat", ".sql", ".rtf",
    }
    if ext in text_exts:
        try:
            raw = path.read_bytes()[:16384]
            try:
                txt = raw.decode("utf-8")
            except UnicodeDecodeError:
                txt = raw.decode("cp1252", errors="replace")
            if len(txt) > 5000:
                txt = txt[:5000] + "\n\n… (Auszug gekürzt)"
            info["kind"] = "text"
            info["badge"] = f"Textvorschau ({ext.upper().lstrip('.')})"
            info["text"] = txt.strip() or "(Leere Datei)"
            return info
        except Exception as exc:
            info["text"] = f"Datei konnte nicht gelesen werden:\n{exc}"
            return info

    if ext == ".docx":
        try:
            with zipfile.ZipFile(path, "r") as zf:
                xml_bytes = zf.read("word/document.xml")
            root = ET.fromstring(xml_bytes)
            paras = []
            for p_el in root.iter():
                if p_el.tag.endswith("}p"):
                    runs = [t_el.text for t_el in p_el.iter() if t_el.tag.endswith("}t") and t_el.text]
                    if runs:
                        paras.append("".join(runs))
                    if len("\n".join(paras)) > 4500:
                        break
            info["kind"] = "text"
            info["badge"] = "Word-Dokument (.docx)"
            info["text"] = "\n".join(paras)[:4500] or "Kein direkt lesbarer Text im Dokument gefunden."
            return info
        except Exception:
            pass

    if ext in (".xlsx", ".xlsm"):
        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = set(zf.namelist())
                shared_strings = []
                if "xl/sharedStrings.xml" in names:
                    ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                    for si in ss_root:
                        if si.tag.endswith("}si") or si.tag == "si":
                            parts = [t.text for t in si.iter() if (t.tag.endswith("}t") or t.tag == "t") and t.text]
                            shared_strings.append("".join(parts).strip())

                sheet_names = []
                if "xl/workbook.xml" in names:
                    wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
                    for sh in wb_root.iter():
                        if sh.tag.endswith("}sheet") or sh.tag == "sheet":
                            nm = sh.attrib.get("name")
                            if nm:
                                sheet_names.append(nm)

                def _sheet_sort_key(n: str):
                    m = re.search(r"(\d+)\.xml$", n)
                    return (0, int(m.group(1))) if m else (1, n)

                sheet_files = sorted(
                    [n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")],
                    key=_sheet_sort_key,
                )
                lines = []
                for idx, sfile in enumerate(sheet_files[:4]):
                    sroot = ET.fromstring(zf.read(sfile))
                    rows_out = []
                    for row_el in sroot.iter():
                        if not (row_el.tag.endswith("}row") or row_el.tag == "row"):
                            continue
                        row_vals = []
                        for c_el in row_el:
                            if not (c_el.tag.endswith("}c") or c_el.tag == "c"):
                                continue
                            ctype = c_el.attrib.get("t", "")
                            val_txt = ""
                            if ctype == "inlineStr":
                                parts = [t.text for t in c_el.iter() if (t.tag.endswith("}t") or t.tag == "t") and t.text]
                                val_txt = "".join(parts).strip()
                            else:
                                v_el = next((ch for ch in c_el if ch.tag.endswith("}v") or ch.tag == "v"), None)
                                raw_v = (v_el.text or "").strip() if v_el is not None else ""
                                if ctype == "s" and raw_v.isdigit():
                                    s_idx = int(raw_v)
                                    val_txt = shared_strings[s_idx] if 0 <= s_idx < len(shared_strings) else ""
                                elif ctype == "b":
                                    val_txt = "WAHR" if raw_v == "1" else "FALSCH"
                                else:
                                    val_txt = raw_v
                            if val_txt:
                                row_vals.append(val_txt)
                        if row_vals:
                            rows_out.append("  |  ".join(row_vals[:10]))
                        if len(rows_out) >= 35:
                            break
                    if rows_out:
                        title = sheet_names[idx] if idx < len(sheet_names) else f"Blatt {idx + 1}"
                        lines.append(f"[{title}]\n" + "\n".join(rows_out))
                    if len("\n\n".join(lines)) > 4200:
                        break

                info["badge"] = f"Excel-Tabelle ({ext.lstrip('.')})"
                if lines:
                    info["kind"] = "text"
                    info["text"] = "\n\n".join(lines)[:4500]
                elif shared_strings:
                    info["kind"] = "text"
                    info["text"] = "  |  ".join(s for s in shared_strings if s)[:4500] or "Leere Excel-Arbeitsmappe."
                else:
                    info["kind"] = "info"
                    info["text"] = "Diese Excel-Arbeitsmappe ist leer oder enthält nur Formatierungen/Diagramme."
                return info
        except Exception:
            info["badge"] = f"Excel-Tabelle ({ext.lstrip('.')})"
            info["text"] = "Excel-Vorschau nicht lesbar (evtl. passwortgeschützt).\nKlicken Sie unten auf „Standard-App“."
            return info

    if ext in (".xls", ".xlsb"):
        info["badge"] = f"Excel-Datei ({ext.lstrip('.')})"
        info["text"] = "Älteres bzw. binäres Excel-Format.\nKlicken Sie unten auf „Standard-App“, um die Tabelle in Excel zu öffnen."
        return info

    if ext == ".pptx":
        try:
            with zipfile.ZipFile(path, "r") as zf:
                slides = sorted(n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
                lines = []
                for idx, sname in enumerate(slides[:4], 1):
                    root = ET.fromstring(zf.read(sname))
                    texts = [t.text.strip() for t in root.iter() if t.tag.endswith("}t") and t.text and t.text.strip()]
                    if texts:
                        lines.append(f"[Folie {idx}] " + " ".join(texts))
                info["kind"] = "text"
                info["badge"] = f"PowerPoint ({len(slides)} Folien)"
                info["text"] = "\n\n".join(lines)[:4500] or "Keine Textinhalte auf den ersten Folien gefunden."
                return info
        except Exception:
            pass

    if ext == ".zip":
        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = zf.namelist()
            sample = "\n".join(f"• {n}" for n in names[:30])
            if len(names) > 30:
                sample += f"\n\n… und {len(names) - 30} weitere Einträge"
            info["kind"] = "text"
            info["badge"] = f"ZIP-Archiv ({len(names)} Einträge)"
            info["text"] = sample or "Leeres ZIP-Archiv."
            return info
        except Exception:
            pass

    if ext == ".pdf":
        rendered_img, page_count = _render_pdf_page_image(path, max_img_size=max_img_size)
        if rendered_img is not None:
            info["kind"] = "image"
            if page_count == 1:
                info["badge"] = "PDF • Seite 1/1"
            elif page_count > 1:
                info["badge"] = f"PDF • Seite 1/{page_count}"
            else:
                info["badge"] = "PDF-Vorschau"
            info["image"] = rendered_img
            return info

        try:
            data = path.read_bytes()[:512 * 1024]
            streams = re.findall(rb'stream[\r\n]+(.*?)[\r\n]+endstream', data, re.DOTALL)
            snippets = []
            for s in streams[:15]:
                try:
                    dec = zlib.decompress(s).decode("latin1", "ignore")
                    words = re.findall(r'\(([^()]{2,120})\)', dec)
                    if words:
                        snippets.append(" ".join(words))
                except Exception:
                    pass
                if len(" ".join(snippets)) > 3000:
                    break
            joined = re.sub(r'\s+', ' ', " ".join(snippets)).strip()
            readable = _is_readable_pdf_text(joined)
            info["kind"] = "text" if readable else "info"
            info["badge"] = "PDF-Dokument"
            info["text"] = (
                joined[:3500]
                if readable
                else "PDF-Text konnte nicht direkt extrahiert werden (z. B. Bild-Scan oder verschlüsselter Zeichensatz).\n\nKlicken Sie unten auf „Standard-App“, um das PDF vollständig zu betrachten."
            )
            return info
        except Exception:
            pass

    info["badge"] = ext.upper().lstrip(".") or "DATEI"
    info["text"] = "Für diesen Dateityp steht keine eingebettete Vorschau zur Verfügung.\nNutzen Sie bei Bedarf „Standard-App“ unten."
    return info

def _rule_matches_text(kw: str, text: str) -> bool:
    """Prueft, ob ein Regel-Stichwort in einem kleingeschriebenen Suchtext vorkommt."""
    if not kw or not text:
        return False
    if len(kw) <= 3:
        return bool(re.search(r'\b' + re.escape(kw) + r'\b', text, re.IGNORECASE))
    return kw in text


def load_custom_rules() -> list[dict]:
    """Laedt benutzerdefinierte Stichwort->Zielordner-Regeln aus custom_rules.json."""
    try:
        if not CUSTOM_RULES_FILE.exists():
            return []
        raw = json.loads(CUSTOM_RULES_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        cleaned = []
        seen_kw = set()
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            kw = str(entry.get("keyword") or "").strip().lower()
            target = str(entry.get("target") or "").strip().replace("\\", "/")
            label = str(entry.get("label") or "").strip() or (target.split("/")[-1] if target else "Benutzerdefiniert")
            if len(kw) < 2 or not target or kw in seen_kw:
                continue
            seen_kw.add(kw)
            cleaned.append({"keyword": kw, "target": target, "label": label})
        return cleaned
    except Exception:
        return []


def write_custom_rules(rules: list[dict]) -> None:
    """Speichert benutzerdefinierte Regeln sicher in custom_rules.json."""
    try:
        CUSTOM_RULES_FILE.write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def save_custom_rule(keyword: str, target: str, label: str = "") -> list[dict]:
    """Speichert oder aktualisiert eine eigene Stichwort-Regel fuer kuenftige Scans."""
    kw = str(keyword or "").strip().lower()
    tgt = str(target or "").strip().replace("\\", "/")
    lbl = str(label or "").strip() or (tgt.split("/")[-1] if tgt else "Benutzerdefiniert")
    if len(kw) < 2 or not tgt:
        return load_custom_rules()
    rules = [r for r in load_custom_rules() if r["keyword"] != kw]
    rules.insert(0, {"keyword": kw, "target": tgt, "label": lbl})
    write_custom_rules(rules)
    return rules


def delete_custom_rule(keyword: str) -> list[dict]:
    """Entfernt eine gespeicherte Stichwort-Regel."""
    kw = str(keyword or "").strip().lower()
    rules = [r for r in load_custom_rules() if r["keyword"] != kw]
    write_custom_rules(rules)
    return rules


def suggest_rule_keyword(filename: str) -> str:
    """Schlaegt aus dem Dateinamen ein sinnvolles Stichwort fuer eine Regel vor."""
    stem = Path(filename).stem
    stopwords = {"scan", "dok", "dokument", "img", "dsc", "foto", "bild", "datei", "copy", "kopie", "neu", "alt", "file", "pdf"}
    tokens = re.findall(r"[A-Za-zäöüÄÖÜß]{3,}", stem)
    candidates = [t.lower() for t in tokens if t.lower() not in stopwords]
    if candidates:
        candidates.sort(key=len, reverse=True)
        return candidates[0]
    cleaned = re.sub(r"[^A-Za-z0-9äöüÄÖÜß_-]+", "", stem).strip().lower()
    return cleaned if len(cleaned) >= 2 else ""


def get_available_target_options() -> list[tuple[str, str]]:
    """Liefert alle verfuegbaren Zielordner (7-Ordner-System + eigene Regeln)."""
    options = [("01_Eingang_Inbox", "Unbekannt -> Inbox")]
    seen = {"01_Eingang_Inbox"}
    for rule in RULES:
        tgt = rule["target"]
        if tgt not in seen:
            seen.add(tgt)
            options.append((tgt, rule["label"]))
    for tgt, lbl in [
        ("07_Archiv_Historie/04_Bilder_Fotos", "Bilder & Fotos"),
        ("08_Programme_Installer", "Programme & Installer"),
        ("09_Videos_Medien", "Videos & Medien"),
        ("10_Ordner_Projekte", "Ordner & Projekte"),
    ]:
        if tgt not in seen:
            seen.add(tgt)
            options.append((tgt, lbl))
    for crule in load_custom_rules():
        tgt = crule["target"]
        if tgt not in seen:
            seen.add(tgt)
            options.append((tgt, crule["label"]))
    return options


def find_target(filename: str, content: str = "", ext: str = ""):
    ext_lower = ext.lower() if ext else ""
    if ext_lower in IMAGE_EXTENSIONS:
        return "07_Archiv_Historie/04_Bilder_Fotos", "Bilder & Fotos"
    if ext_lower in INSTALLER_EXTENSIONS:
        return "08_Programme_Installer", "Programme & Installer"
    if ext_lower in MEDIA_EXTENSIONS:
        return "09_Videos_Medien", "Videos & Medien"
    text = f"{filename} {content}".lower()
    for crule in load_custom_rules():
        if _rule_matches_text(crule["keyword"], text):
            return crule["target"], crule["label"]
    # E-Books (.epub, .mobi, .azw3, ...) gehören immer in Bücher & Medien –
    # Wörter im Buchtitel (z. B. "Rezepte", "Arzt", "Testament", "Miete") dürfen sie nie umleiten!
    if ext_lower in EBOOK_EXTENSIONS:
        return "07_Archiv_Historie/02_Buecher_Medien", "Bücher & Medien"
    fname_low = str(filename or "").lower()
    if any(k in fname_low for k in ("kochbuch", "backbuch")):
        return "07_Archiv_Historie/02_Buecher_Medien", "Bücher & Medien"
    for rule in RULES:
        for kw in rule["keywords"]:
            if _rule_matches_text(kw, text):
                return rule["target"], rule["label"]
    return "01_Eingang_Inbox", "Unbekannt -> Inbox"

def find_folder_target(dir_path: Path) -> tuple[str, str, int, int]:
    """Bestimmt den passenden Zielordner für einen ganzen Unterordner, ohne ihn zu zerpflücken."""
    inner_count = 0
    inner_bytes = 0
    img_cnt = 0
    media_cnt = 0
    inst_cnt = 0
    ebook_cnt = 0
    sample_names = []

    try:
        for root, dirs, files in os.walk(dir_path):
            dirs[:] = [d for d in dirs if d.lower() not in IGNORED_DIRS and not d.startswith(".")]
            for fname in files:
                if fname.startswith(".") or fname.startswith("~$"):
                    continue
                inner_count += 1
                try:
                    inner_bytes += (Path(root) / fname).stat().st_size
                except Exception:
                    pass
                if len(sample_names) < 40:
                    sample_names.append(fname)
                f_ext = Path(fname).suffix.lower()
                if f_ext in IMAGE_EXTENSIONS:
                    img_cnt += 1
                elif f_ext in MEDIA_EXTENSIONS:
                    media_cnt += 1
                elif f_ext in INSTALLER_EXTENSIONS:
                    inst_cnt += 1
                elif f_ext in EBOOK_EXTENSIONS:
                    ebook_cnt += 1
    except Exception:
        pass

    t_sub, t_lbl = find_target(dir_path.name, "", "")
    if t_sub != "01_Eingang_Inbox":
        return t_sub, t_lbl, inner_count, inner_bytes

    if inner_count > 0:
        if ebook_cnt / inner_count >= 0.7:
            return "07_Archiv_Historie/02_Buecher_Medien", "Bücher & Medien", inner_count, inner_bytes
        if img_cnt / inner_count >= 0.7:
            return "07_Archiv_Historie/04_Bilder_Fotos", "Bilder & Fotos", inner_count, inner_bytes
        if media_cnt / inner_count >= 0.7:
            return "09_Videos_Medien", "Videos & Medien", inner_count, inner_bytes
        if inst_cnt / inner_count >= 0.7:
            return "08_Programme_Installer", "Programme & Installer", inner_count, inner_bytes

    if sample_names:
        t_sub2, t_lbl2 = find_target(dir_path.name, " ".join(sample_names), "")
        if t_sub2 != "01_Eingang_Inbox":
            return t_sub2, t_lbl2, inner_count, inner_bytes

    return "10_Ordner_Projekte", "Ordner & Projekte", inner_count, inner_bytes

def score_for_original(path_stem: str) -> int:
    """Dateien mit sauberen Namen (ohne ' 2', ' (1)', '_kopie') erhalten Vorrang als Original."""
    match = DUP_SUFFIX_REGEX.search(path_stem)
    if match:
        return 100 + len(path_stem)
    return 0


DUP_ORIGINAL_STRATEGIES = (
    ("clean_name", "Sauberer Name (ohne _kopie / (1))"),
    ("newest", "Neueste Datei (Änderungsdatum)"),
    ("oldest", "Älteste Datei (Änderungsdatum)"),
    ("shortest_path", "Kürzester Ordnerpfad"),
)


def duplicate_priority_key(item: dict, strategy: str = "clean_name"):
    """Sortierschlüssel für die Wahl des Originals innerhalb einer inhaltsgleichen Duplikat-Gruppe."""
    src_path = Path(item.get("src_path", ""))
    stem = src_path.stem if src_path else Path(str(item.get("name", ""))).stem
    name_score = score_for_original(stem)
    mtime_ts = float(item.get("mtime_ts", 0.0) or 0.0)
    full_str = str(src_path)
    path_depth = len(src_path.parts) if src_path else 999
    if strategy == "newest":
        return (-mtime_ts, name_score, len(full_str), full_str.lower())
    if strategy == "oldest":
        eff_ts = mtime_ts if mtime_ts > 0 else float("inf")
        return (eff_ts, name_score, len(full_str), full_str.lower())
    if strategy == "shortest_path":
        return (path_depth, len(full_str), name_score, full_str.lower())
    # Standard: "clean_name" (stabile Reihenfolge bei gleichem Score wie bisher)
    return name_score

# ------------------------------------------------------------------
# Moderne abgerundete Button-Komponenten (Windows 11 Fluent Look)
# ------------------------------------------------------------------
IMG_CACHE = {}

def _draw_vector_icon(d: ImageDraw.ImageDraw, kind: str, x0: int, y0: int, size: int, is_active: bool):
    """Zeichnet gestochen scharfe Vektor-Icons (3x Supersampling) direkt auf den Button."""
    s = size  # bereits skaliert (z.B. 18 * 3 = 54)
    u = s / 16.0
    lw = max(2, round(1.5 * u))

    if is_active:
        c_folder = "#ffffff"
        c_folder_fill = "#3b82f6"
        c_accent = "#93c5fd"
        c_ink = "#ffffff"
        c_doc_fill = "#1d4ed8"
        c_img_fill = "#1d4ed8"
        c_img_accent = "#a7f3d0"
        tile_colors = ["#ffffff", "#bae6fd", "#fef3c7", "#dbeafe"]
    else:
        c_folder = "#d97706"
        c_folder_fill = "#fef3c7"
        c_accent = "#2563eb"
        c_ink = "#334155"
        c_doc_fill = "#eff6ff"
        c_img_fill = "#ecfdf5"
        c_img_accent = "#059669"
        tile_colors = ["#2563eb", "#059669", "#d97706", "#64748b"]

    def px(vx, vy):
        return (round(x0 + vx * u), round(y0 + vy * u))

    def draw_folder_base(x_off=0.0, y_off=0.0, scale_w=1.0, scale_h=1.0):
        tx0, ty0 = 1.0 + x_off, 2.5 + y_off
        w_tot = 14.0 * scale_w
        h_tot = 11.5 * scale_h
        tab_w = 5.4 * scale_w
        tab_h = 2.2 * scale_h
        r = max(2, round(1.4 * u))
        d.polygon([
            px(tx0, ty0 + tab_h),
            px(tx0, ty0 + 0.5),
            px(tx0 + tab_w * 0.78, ty0 + 0.5),
            px(tx0 + tab_w, ty0 + tab_h),
        ], fill=c_folder)
        d.rounded_rectangle(
            [px(tx0, ty0 + tab_h - 0.3), px(tx0 + w_tot, ty0 + h_tot)],
            radius=r,
            fill=c_folder_fill,
            outline=c_folder,
            width=lw,
        )

    if kind == "folder_adjacent":
        draw_folder_base(scale_w=0.84, scale_h=0.90)
        badge_bg = "#ffffff" if is_active else "#2563eb"
        badge_fg = "#1d4ed8" if is_active else "#ffffff"
        d.ellipse([px(8.8, 8.4), px(15.2, 14.8)], fill=badge_bg, outline=("#1d4ed8" if is_active else "#ffffff"), width=max(1, round(0.8 * u)))
        d.line([px(10.3, 11.6), px(13.2, 11.6)], fill=badge_fg, width=max(2, round(1.3 * u)))
        d.polygon([px(12.2, 10.0), px(13.9, 11.6), px(12.2, 13.2)], fill=badge_fg)

    elif kind == "folder_sub":
        draw_folder_base(scale_w=0.84, scale_h=0.90)
        badge_bg = "#ffffff" if is_active else "#2563eb"
        badge_fg = "#1d4ed8" if is_active else "#ffffff"
        d.ellipse([px(8.8, 8.4), px(15.2, 14.8)], fill=badge_bg, outline=("#1d4ed8" if is_active else "#ffffff"), width=max(1, round(0.8 * u)))
        d.line([px(10.9, 9.9), px(10.9, 12.1), px(13.3, 12.1)], fill=badge_fg, width=max(2, round(1.2 * u)))
        d.polygon([px(12.3, 10.7), px(13.9, 12.1), px(12.3, 13.5)], fill=badge_fg)

    elif kind == "folder_same":
        draw_folder_base()
        d.ellipse([px(6.0, 7.5), px(10.0, 11.5)], fill=("#ffffff" if is_active else c_accent))

    elif kind == "usb_stick":
        r = max(2, round(1.4 * u))
        cap_fill = "#e2e8f0" if is_active else "#dbeafe"
        body_fill = "#ffffff" if is_active else c_accent
        body_outline = "#ffffff" if is_active else "#1d4ed8"
        d.rounded_rectangle([px(5.0, 1.4), px(11.0, 5.2)], radius=max(1, round(0.8 * u)), fill=cap_fill, outline=body_outline, width=max(1, round(1.0 * u)))
        d.rectangle([px(6.4, 2.5), px(7.4, 4.0)], fill=("#2563eb" if not is_active else "#1d4ed8"))
        d.rectangle([px(8.6, 2.5), px(9.6, 4.0)], fill=("#2563eb" if not is_active else "#1d4ed8"))
        d.rounded_rectangle([px(3.6, 4.8), px(12.4, 14.6)], radius=r, fill=body_fill, outline=body_outline, width=lw)
        d.ellipse([px(6.8, 8.6), px(9.2, 11.0)], fill=("#2563eb" if is_active else "#ffffff"))

    elif kind == "folder_split":
        d.rounded_rectangle([px(3.5, 1.4), px(12.5, 9.0)], radius=max(1, round(1.0 * u)), fill=("#1d4ed8" if is_active else "#ffffff"), outline=c_ink, width=max(2, round(1.2 * u)))
        d.line([px(5.2, 3.5), px(10.8, 3.5)], fill=("#bae6fd" if is_active else "#64748b"), width=max(1, round(1.0 * u)))
        d.line([px(5.2, 5.2), px(9.5, 5.2)], fill=("#bae6fd" if is_active else "#64748b"), width=max(1, round(1.0 * u)))
        d.rounded_rectangle(
            [px(1.2, 5.6), px(14.8, 14.2)],
            radius=max(2, round(1.5 * u)),
            fill=c_folder_fill,
            outline=c_folder,
            width=lw,
        )

    elif kind == "folder_whole":
        r = max(2, round(1.5 * u))
        d.rounded_rectangle([px(1.5, 2.0), px(14.5, 14.0)], radius=r, fill=c_folder_fill, outline=c_folder, width=lw)
        d.line([px(8.0, 2.0), px(8.0, 14.0)], fill=c_folder, width=lw)
        d.line([px(1.5, 6.2), px(14.5, 6.2)], fill=c_folder, width=lw)
        d.rectangle([px(6.4, 4.7), px(9.6, 7.7)], fill=c_folder)

    elif kind == "folder_ignore":
        c_skip = "#ffffff" if is_active else "#64748b"
        d.polygon([px(1.8, 3.0), px(7.4, 8.0), px(1.8, 13.0)], fill=c_skip)
        d.polygon([px(7.2, 3.0), px(12.8, 8.0), px(7.2, 13.0)], fill=c_skip)
        d.rounded_rectangle([px(12.8, 2.8), px(14.8, 13.2)], radius=max(1, round(0.6 * u)), fill=c_skip)

    elif kind == "all_files":
        r = max(2, round(1.3 * u))
        d.rounded_rectangle([px(1.4, 1.4), px(7.2, 7.2)], radius=r, fill=tile_colors[0])
        d.rounded_rectangle([px(8.8, 1.4), px(14.6, 7.2)], radius=r, fill=tile_colors[1])
        d.rounded_rectangle([px(1.4, 8.8), px(7.2, 14.6)], radius=r, fill=tile_colors[2])
        d.rounded_rectangle([px(8.8, 8.8), px(14.6, 14.6)], radius=r, fill=tile_colors[3])

    elif kind == "doc_only":
        c_border = "#ffffff" if is_active else "#2563eb"
        r = max(2, round(1.3 * u))
        d.rounded_rectangle([px(2.5, 1.3), px(13.5, 14.7)], radius=r, fill=c_doc_fill, outline=c_border, width=lw)
        d.polygon([px(9.6, 1.3), px(13.5, 5.2), px(9.6, 5.2)], fill=c_border)
        c_lines = "#ffffff" if is_active else "#334155"
        d.line([px(4.7, 7.3), px(11.3, 7.3)], fill=c_lines, width=max(2, round(1.3 * u)))
        d.line([px(4.7, 9.8), px(11.3, 9.8)], fill=c_lines, width=max(2, round(1.3 * u)))
        d.line([px(4.7, 12.3), px(9.1, 12.3)], fill=c_lines, width=max(2, round(1.3 * u)))

    elif kind == "office_doc":
        c_border = "#ffffff" if is_active else "#ea580c"
        c_fill = "#1d4ed8" if is_active else "#fff7ed"
        r = max(2, round(1.3 * u))
        d.rounded_rectangle([px(2.0, 1.5), px(14.0, 14.5)], radius=r, fill=c_fill, outline=c_border, width=lw)
        c_grid = "#ffffff" if is_active else "#c2410c"
        d.line([px(2.0, 5.8), px(14.0, 5.8)], fill=c_grid, width=max(1, round(1.1 * u)))
        d.line([px(2.0, 10.0), px(14.0, 10.0)], fill=c_grid, width=max(1, round(1.1 * u)))
        d.line([px(7.5, 5.8), px(7.5, 14.5)], fill=c_grid, width=max(1, round(1.1 * u)))

    elif kind == "images_only":
        c_border = "#ffffff" if is_active else "#059669"
        r = max(2, round(1.5 * u))
        d.rounded_rectangle([px(1.3, 2.0), px(14.7, 14.0)], radius=r, fill=c_img_fill, outline=c_border, width=lw)
        d.ellipse([px(3.5, 3.8), px(6.5, 6.8)], fill=("#fde68a"))
        c_mtn = "#ffffff" if is_active else c_img_accent
        d.polygon([px(2.5, 12.8), px(6.5, 7.6), px(9.8, 12.8)], fill=c_mtn)
        d.polygon([px(7.4, 12.8), px(10.7, 6.4), px(13.7, 12.8)], fill=("#6ee7b7" if is_active else "#10b981"))

    elif kind == "installer_app":
        # Software-/Installer-Fenster mit Download-Pfeil
        c_border = "#ffffff" if is_active else "#7c3aed"
        c_fill = "#1d4ed8" if is_active else "#f5f3ff"
        c_arr = "#ffffff" if is_active else "#7c3aed"
        r = max(2, round(1.5 * u))
        d.rounded_rectangle([px(1.5, 1.8), px(14.5, 14.2)], radius=r, fill=c_fill, outline=c_border, width=lw)
        d.line([px(1.5, 4.9), px(14.5, 4.9)], fill=c_border, width=lw)
        d.line([px(8.0, 6.6), px(8.0, 11.4)], fill=c_arr, width=max(2, round(1.5 * u)))
        d.polygon([px(5.2, 9.7), px(10.8, 9.7), px(8.0, 12.8)], fill=c_arr)

    elif kind == "media_video":
        # Abgerundetes Video-/Medien-Symbol mit Play-Dreieck
        c_border = "#ffffff" if is_active else "#e11d48"
        c_fill = "#1d4ed8" if is_active else "#fff1f2"
        c_play = "#ffffff" if is_active else "#e11d48"
        r = max(2, round(1.6 * u))
        d.rounded_rectangle([px(1.3, 2.2), px(14.7, 13.8)], radius=r, fill=c_fill, outline=c_border, width=lw)
        d.polygon([px(6.1, 4.9), px(11.6, 8.0), px(6.1, 11.1)], fill=c_play)

    elif kind == "cloud_onedrive":
        # Sauberes OneDrive-/Cloud-Wolken-Symbol
        c_outline = "#ffffff" if is_active else "#0284c7"
        c_fill = "#1d4ed8" if is_active else "#e0f2fe"
        d.ellipse([px(2.0, 6.2), px(7.6, 12.6)], fill=c_fill, outline=c_outline, width=lw)
        d.ellipse([px(4.5, 3.6), px(11.3, 10.6)], fill=c_fill, outline=c_outline, width=lw)
        d.ellipse([px(8.4, 5.8), px(14.2, 12.6)], fill=c_fill, outline=c_outline, width=lw)
        d.rectangle([px(4.4, 8.0), px(11.6, 12.2)], fill=c_fill)
        d.line([px(4.2, 12.4), px(11.8, 12.4)], fill=c_outline, width=lw)

    elif kind == "cloud_onedrive_biz":
        # OneDrive-Wolke mit dezentem Firmen-Badge unten rechts
        c_outline = "#ffffff" if is_active else "#0284c7"
        c_fill = "#1d4ed8" if is_active else "#e0f2fe"
        d.ellipse([px(1.6, 5.8), px(7.0, 12.0)], fill=c_fill, outline=c_outline, width=lw)
        d.ellipse([px(4.0, 3.2), px(10.6, 10.0)], fill=c_fill, outline=c_outline, width=lw)
        d.ellipse([px(7.8, 5.4), px(13.4, 12.0)], fill=c_fill, outline=c_outline, width=lw)
        d.rectangle([px(3.9, 7.6), px(10.8, 11.6)], fill=c_fill)
        d.line([px(3.8, 11.8), px(10.8, 11.8)], fill=c_outline, width=lw)
        # Kleines Firmen-Gebaeude-Badge (bernstein/indigo) unten rechts
        b_outline = "#ffffff" if is_active else "#b45309"
        b_fill = "#f59e0b"
        d.rounded_rectangle([px(9.2, 7.8), px(14.8, 14.4)], radius=max(1, round(0.9 * u)), fill=b_fill, outline=b_outline, width=max(1, round(1.0 * u)))
        d.rectangle([px(10.5, 9.2), px(11.6, 10.4)], fill="#ffffff")
        d.rectangle([px(12.4, 9.2), px(13.5, 10.4)], fill="#ffffff")
        d.rectangle([px(10.5, 11.4), px(11.6, 12.6)], fill="#ffffff")
        d.rectangle([px(12.4, 11.4), px(13.5, 12.6)], fill="#ffffff")

    elif kind == "network_globe":
        # Blauer Netzwerk-Globus fuer UNC-/Serverpfade
        c_outline = "#ffffff" if is_active else "#0284c7"
        c_fill = "#1d4ed8" if is_active else "#eff6ff"
        d.ellipse([px(2.0, 2.0), px(14.0, 14.0)], fill=c_fill, outline=c_outline, width=lw)
        d.ellipse([px(5.2, 2.1), px(10.8, 13.9)], outline=c_outline, width=max(1, round(1.1 * u)))
        d.line([px(2.2, 8.0), px(13.8, 8.0)], fill=c_outline, width=max(1, round(1.1 * u)))

    elif kind == "key_login":
        # Goldener/Bernstein-Schluessel fuer Cloud- & Netzwerk-Anmeldung
        c_key = "#ffffff" if is_active else "#d97706"
        c_key_fill = "#1d4ed8" if is_active else "#fef3c7"
        d.ellipse([px(2.0, 4.4), px(7.8, 10.2)], fill=c_key_fill, outline=c_key, width=lw)
        d.line([px(7.6, 7.3), px(14.2, 7.3)], fill=c_key, width=lw)
        d.line([px(11.6, 7.3), px(11.6, 10.4)], fill=c_key, width=max(1, round(1.3 * u)))
        d.line([px(13.8, 7.3), px(13.8, 10.9)], fill=c_key, width=max(1, round(1.3 * u)))

    elif kind == "shield_sys":
        # Schutzschild fuer System- & Entwicklerordner-Ausschluss
        c_outline = "#ffffff" if is_active else "#059669"
        c_fill = "#1d4ed8" if is_active else "#ecfdf5"
        c_check = "#a7f3d0" if is_active else "#059669"
        shield_pts = [
            px(8.0, 1.4),
            px(13.8, 3.4),
            px(13.3, 8.4),
            px(8.0, 14.5),
            px(2.7, 8.4),
            px(2.2, 3.4),
        ]
        d.polygon(shield_pts, fill=c_fill, outline=c_outline)
        # Kraeftige Kontur auf den Polygon-Kanten zeichnen
        d.line(shield_pts + [shield_pts[0]], fill=c_outline, width=lw, joint="curve")
        d.line([px(5.2, 8.1), px(7.2, 10.2), px(11.0, 5.8)], fill=c_check, width=max(2, round(1.6 * u)))

    elif kind == "shield_firm":
        # Schutzschild fuer Firmen- & Netzlaufwerk-Sicherheit (Schloss/Globus im Schild)
        c_outline = "#ffffff" if is_active else "#1d4ed8"
        c_fill = "#1d4ed8" if is_active else "#eff6ff"
        c_accent_in = "#bae6fd" if is_active else "#2563eb"
        shield_pts = [
            px(8.0, 1.4),
            px(13.8, 3.4),
            px(13.3, 8.4),
            px(8.0, 14.5),
            px(2.7, 8.4),
            px(2.2, 3.4),
        ]
        d.polygon(shield_pts, fill=c_fill, outline=c_outline)
        d.line(shield_pts + [shield_pts[0]], fill=c_outline, width=lw, joint="curve")
        d.arc([px(6.1, 4.3), px(9.9, 8.1)], start=180, end=360, fill=c_accent_in, width=max(2, round(1.3 * u)))
        d.rounded_rectangle([px(5.2, 6.8), px(10.8, 10.9)], radius=max(1, round(1.0 * u)), fill=c_accent_in)

    elif kind == "globe":
        # Gut sichtbarer 3x-Vektor-Globus (ersetzt blasses Unicode-Emoji)
        d.ellipse([px(1.5, 1.5), px(14.5, 14.5)], fill="#0ea5e9")
        d.ellipse([px(4.7, 1.7), px(11.3, 14.3)], outline="#e0f2fe", width=max(1, round(1.05 * u)))
        d.line([px(8.0, 1.7), px(8.0, 14.3)], fill="#ffffff", width=max(1, round(1.05 * u)))
        d.line([px(1.7, 8.0), px(14.3, 8.0)], fill="#ffffff", width=max(1, round(1.05 * u)))
        d.line([px(2.7, 4.8), px(13.3, 4.8)], fill="#e0f2fe", width=max(1, round(0.95 * u)))
        d.line([px(2.7, 11.2), px(13.3, 11.2)], fill="#e0f2fe", width=max(1, round(0.95 * u)))
        d.ellipse([px(1.5, 1.5), px(14.5, 14.5)], outline="#0369a1", width=max(2, round(1.15 * u)))

def attach_tooltip(widget, text: str):
    """Zeigt beim Schweben ueber einem Widget einen dezenten Hinweis, ohne bestehende Hover-Events zu ueberschreiben."""
    state = {"win": None, "job": None}

    def hide(_event=None):
        if state["job"] is not None:
            try:
                widget.after_cancel(state["job"])
            except Exception:
                pass
            state["job"] = None
        if state["win"] is not None:
            try:
                state["win"].destroy()
            except Exception:
                pass
            state["win"] = None

    def show_now():
        state["job"] = None
        if state["win"] is not None:
            return
        try:
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            x = widget.winfo_rootx()
            y = widget.winfo_rooty() + widget.winfo_height() + 6
            tw.wm_geometry(f"+{x}+{y}")
            is_dark = (CURRENT_THEME_MODE == "dark")
            tw.configure(bg="#334155" if is_dark else "#cbd5e1")
            lbl = tk.Label(
                tw,
                text=text,
                font=("Segoe UI", 8),
                bg="#1e293b" if is_dark else "#ffffff",
                fg="#f8fafc" if is_dark else "#1e293b",
                padx=8,
                pady=4,
            )
            lbl.pack(padx=1, pady=1)
            state["win"] = tw
        except Exception:
            pass

    def schedule_show(_event=None):
        hide()
        state["job"] = widget.after(350, show_now)

    widget.bind("<Enter>", schedule_show, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<Button-1>", hide, add="+")

def get_rounded_photo(w, h, r, bg, border_color=None, icon_kind=None, icon_x=0, icon_y=0, icon_size=18, is_active=False):
    """Erzeugt ein super-geglättetes abgerundetes Bild mit Antialiasing (inkl. optionalem Vektor-Icon)."""
    key = (w, h, r, bg, border_color, icon_kind, icon_x, icon_y, icon_size, is_active)
    if key in IMG_CACHE:
        return IMG_CACHE[key]
    scale = 3  # 3x Supersampling fuer weiche Kurven
    w3, h3, r3 = max(1, w * scale), max(1, h * scale), max(1, r * scale)
    im = Image.new("RGBA", (w3, h3), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if border_color:
        d.rounded_rectangle([0, 0, w3 - 1, h3 - 1], radius=r3, fill=bg, outline=border_color, width=1 * scale)
    else:
        d.rounded_rectangle([0, 0, w3 - 1, h3 - 1], radius=r3, fill=bg)
    if icon_kind:
        _draw_vector_icon(d, icon_kind, icon_x * scale, icon_y * scale, icon_size * scale, is_active)
    im = im.resize((w, h), Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(im)
    IMG_CACHE[key] = photo
    return photo

class ModernRoundedButton(tk.Canvas):
    """Eleganter abgerundeter Button mit exakter Icon-Ausrichtung, einheitlicher Breite & Hover-Effekt."""
    def __init__(self, parent, text, command=None, bg="#ffffff", fg="#1e293b",
                 hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"), r=8, padx=12, pady=6, icon_kind=None, min_width=0, min_height=0):
        self.parent = parent
        self.cmd = command
        self.r = r
        self.font_spec = font
        self.f_obj = tkfont.Font(font=font)
        self.padx = padx
        self.pady = pady
        self.cur_text = text
        self.icon_kind = icon_kind
        self.min_width = min_width
        self.min_height = min_height
        self.cur_bg = bg
        self.cur_hover_bg = hover_bg
        self.cur_border_color = border_color
        self.fg_color = fg
        self._base_colors = (bg, fg, hover_bg, border_color)
        self.set_colors(bg, fg, hover_bg, border_color)
        super().__init__(parent, width=self._cur_w, height=self._cur_h,
                         bg=parent["bg"], bd=0, highlightthickness=0, cursor="hand2")
        self.bg_item = self.create_image(0, 0, image=self.img_norm, anchor="nw")
        self.txt_item = self.create_text(
            self._text_x,
            self._cur_h // 2,
            text=self.cur_text,
            anchor=self._text_anchor,
            justify="center" if not self.icon_kind else "left",
            font=self.font_spec,
            fill=self.fg_color,
        )
        self.bind("<Button-1>", lambda e: self.cmd() if self.cmd else None)
        self.bind("<Enter>", lambda e: self.itemconfig(self.bg_item, image=self.img_hover))
        self.bind("<Leave>", lambda e: self.itemconfig(self.bg_item, image=self.img_norm))

    def _compute_layout(self):
        lines = self.cur_text.splitlines() or [""]
        clean_tw = max(self.f_obj.measure(ln) for ln in lines)
        text_h = self.f_obj.metrics("linespace") * len(lines)
        h = max(10, self.min_height, text_h + self.pady * 2 + 4)
        if not self.icon_kind:
            w = max(10, self.min_width, clean_tw + self.padx * 2)
            self._cur_w = w
            self._cur_h = h
            self._text_x = w // 2
            self._text_anchor = "center"
            return w, h, 0, 0, 18

        icon_size = 18
        gap = 9
        w = max(10, self.min_width, self.padx + icon_size + gap + clean_tw + self.padx)
        icon_x = self.padx
        icon_y = max(2, (h - icon_size) // 2)
        self._cur_w = w
        self._cur_h = h
        self._text_x = icon_x + icon_size + gap
        self._text_anchor = "w"
        return w, h, icon_x, icon_y, icon_size

    def set_colors(self, bg, fg, hover_bg=None, border_color=None, _from_theme: bool = False):
        if not _from_theme:
            self._base_colors = (bg, fg, hover_bg, border_color)
        is_solid = str(bg).lower() in ("#2563eb", "#ea580c", "#4f46e5", "#059669", "#dc2626") or str(fg).lower() == "#ffffff"
        if CURRENT_THEME_MODE == "dark":
            if is_solid:
                bg = bg
                fg = fg
                if hover_bg is not None:
                    hover_bg = "#3b82f6" if str(bg).lower() == "#2563eb" else hover_bg
                border_color = border_color
            else:
                bg = theme_color(bg, role="bg", mode="dark")
                fg = theme_color(fg, role="fg", mode="dark")
                if hover_bg is not None:
                    hover_bg = theme_color(hover_bg, role="bg", mode="dark")
                border_color = theme_color(border_color or "#cbd5e1", role="border", mode="dark")
        self.cur_bg = bg
        self.cur_hover_bg = hover_bg
        self.cur_border_color = border_color
        if hover_bg is None:
            if bg == "#2563eb": hover_bg = "#3b82f6" if CURRENT_THEME_MODE == "dark" else "#1d4ed8"
            elif bg == "#ea580c": hover_bg = "#c2410c"
            elif bg == "#4f46e5": hover_bg = "#4338ca"
            elif bg == "#059669": hover_bg = "#047857"
            elif bg == "#dc2626": hover_bg = "#b91c1c"
            elif bg == "#ffffff": hover_bg = "#f1f5f9"
            elif bg in ["#f1f5f9", "#f8fafc"]: hover_bg = "#e2e8f0"
            elif bg in ["#1e293b", "#172234", "#0f172a"]: hover_bg = "#273549"
            elif bg == "#e0f2fe": hover_bg = "#bae6fd"
            elif bg == "#eff6ff": hover_bg = "#dbeafe"
            else: hover_bg = bg
        is_active = bg in ("#2563eb", "#ea580c", "#4f46e5", "#059669", "#dc2626") or fg == "#ffffff"
        w, h, icon_x, icon_y, icon_size = self._compute_layout()
        self.img_norm = get_rounded_photo(w, h, self.r, bg, border_color, self.icon_kind, icon_x, icon_y, icon_size, is_active)
        self.img_hover = get_rounded_photo(w, h, self.r, hover_bg, border_color, self.icon_kind, icon_x, icon_y, icon_size, is_active)
        self.fg_color = fg

    def refresh_theme(self, mode=None):
        try:
            self.config(bg=self.parent.cget("bg"))
        except Exception:
            pass
        b_bg, b_fg, b_hov, b_brd = getattr(self, "_base_colors", (self.cur_bg, self.fg_color, self.cur_hover_bg, self.cur_border_color))
        self.set_colors(b_bg, b_fg, b_hov, b_brd, _from_theme=True)
        try:
            self.config(width=self._cur_w, height=self._cur_h)
            self.itemconfig(self.bg_item, image=self.img_norm)
            self.itemconfig(self.txt_item, text=self.cur_text, font=self.font_spec, fill=self.fg_color, anchor=self._text_anchor)
            self.coords(self.txt_item, self._text_x, self._cur_h // 2)
        except Exception:
            pass

    def update_style(self, text=None, bg=None, fg=None, hover_bg=None, border_color=None, font=None, icon_kind=None):
        recalc = False
        if text is not None:
            self.cur_text = text
            recalc = True
        if font is not None:
            self.font_spec = font
            self.f_obj = tkfont.Font(font=font)
            recalc = True
        if icon_kind is not None:
            self.icon_kind = icon_kind
            recalc = True
        if bg is not None:
            self.set_colors(bg, fg if fg is not None else self.fg_color, hover_bg, border_color)
        elif recalc:
            self.set_colors(self.cur_bg, fg if fg is not None else self.fg_color, self.cur_hover_bg, self.cur_border_color)
        elif fg is not None:
            self.fg_color = fg
        self.config(width=self._cur_w, height=self._cur_h)
        self.itemconfig(self.bg_item, image=self.img_norm)
        self.itemconfig(self.txt_item, text=self.cur_text, font=self.font_spec, fill=self.fg_color, anchor=self._text_anchor)
        self.coords(self.txt_item, self._text_x, self._cur_h // 2)

def get_calendar_icon_photo(fg="#475569", bg="#ffffff", size=15):
    key = ("cal_icon_btn_v2", fg, bg, size)
    if key in IMG_CACHE:
        return IMG_CACHE[key]
    scale = 3
    s = size * scale
    img = Image.new("RGBA", (s, s), bg)
    d = ImageDraw.Draw(img)
    u = s / 16.0
    px = lambda x, y: (x * u, y * u)
    lw = max(2, round(1.25 * u))
    r = max(2, round(1.8 * u))
    d.rounded_rectangle([px(1.5, 2.8), px(14.5, 14.5)], radius=r, fill=bg, outline=fg, width=lw)
    d.line([px(1.6, 6.2), px(14.4, 6.2)], fill=fg, width=lw)
    # Kalender-Ringe oben
    ring_w = max(2, round(1.3 * u))
    d.line([px(4.9, 1.1), px(4.9, 3.6)], fill=fg, width=ring_w)
    d.line([px(11.1, 1.1), px(11.1, 3.6)], fill=fg, width=ring_w)
    # Dezentes 3x2 Kalenderraster (Portfolio-Manager Stil)
    sq = max(2, round(1.5 * u))
    for gy in (8.1, 11.2):
        for gx in (4.2, 7.6, 11.0):
            x0, y0 = round(gx * u - sq / 2), round(gy * u - sq / 2)
            d.rectangle([x0, y0, x0 + sq, y0 + sq], fill=fg)
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    IMG_CACHE[key] = photo
    return photo


class CalendarPopup(tk.Toplevel):
    """Moderner deutscher Fluent-Kalender-Picker zur schnellen Datumsauswahl."""

    MONTHS_DE = [
        "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    ]
    MONTHS_SHORT_DE = [
        "Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
        "Jul", "Aug", "Sep", "Okt", "Nov", "Dez",
    ]
    WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

    def __init__(self, anchor_widget, target_var: tk.StringVar, on_select=None):
        super().__init__(anchor_widget.winfo_toplevel())
        self.anchor_widget = anchor_widget
        self.target_var = target_var
        self.on_select = on_select
        self.mode = "days"  # "days" | "months"
        self._closed = False

        today = datetime.date.today()
        self.today = today
        self.selected_date = self._parse_initial_date(target_var.get())
        ref = self.selected_date or today
        self.view_year = ref.year
        self.view_month = ref.month

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#cbd5e1")

        self.card = tk.Frame(self, bg="#ffffff", padx=12, pady=10)
        self.card.pack(fill="both", expand=True, padx=1, pady=1)

        # Header: Monat/Jahr + Auf/Ab Navigation
        hdr = tk.Frame(self.card, bg="#ffffff")
        hdr.pack(fill="x", pady=(0, 8))

        self.lbl_title = tk.Label(
            hdr,
            text="",
            font=("Segoe UI", 10, "bold"),
            bg="#ffffff",
            fg="#0f172a",
            cursor="hand2",
            anchor="w",
            padx=4,
            pady=2,
        )
        self.lbl_title.pack(side="left")
        self.lbl_title.bind("<Button-1>", lambda _e: self.toggle_mode())
        self.lbl_title.bind("<Enter>", lambda _e: self.lbl_title.config(bg="#f1f5f9"))
        self.lbl_title.bind("<Leave>", lambda _e: self.lbl_title.config(bg="#ffffff"))

        nav_box = tk.Frame(hdr, bg="#ffffff")
        nav_box.pack(side="right")
        self.btn_next = tk.Label(
            nav_box, text="↓", font=("Segoe UI", 10), bg="#ffffff", fg="#334155",
            cursor="hand2", width=3, pady=1,
        )
        self.btn_next.pack(side="right")
        self.btn_next.bind("<Button-1>", lambda _e: self.step_next())
        self.btn_next.bind("<Enter>", lambda _e: self.btn_next.config(bg="#f1f5f9"))
        self.btn_next.bind("<Leave>", lambda _e: self.btn_next.config(bg="#ffffff"))

        self.btn_prev = tk.Label(
            nav_box, text="↑", font=("Segoe UI", 10), bg="#ffffff", fg="#334155",
            cursor="hand2", width=3, pady=1,
        )
        self.btn_prev.pack(side="right", padx=(0, 2))
        self.btn_prev.bind("<Button-1>", lambda _e: self.step_prev())
        self.btn_prev.bind("<Enter>", lambda _e: self.btn_prev.config(bg="#f1f5f9"))
        self.btn_prev.bind("<Leave>", lambda _e: self.btn_prev.config(bg="#ffffff"))

        self.body = tk.Frame(self.card, bg="#ffffff")
        self.body.pack(fill="both", expand=True)

        # Footer: Loeschen links, Heute rechts
        ftr = tk.Frame(self.card, bg="#ffffff")
        ftr.pack(fill="x", pady=(8, 0))
        self.btn_clear = tk.Label(
            ftr, text="Löschen", font=("Segoe UI", 9), bg="#ffffff", fg="#0284c7",
            cursor="hand2", padx=4, pady=2,
        )
        self.btn_clear.pack(side="left")
        self.btn_clear.bind("<Button-1>", lambda _e: self.clear_date())
        self.btn_clear.bind("<Enter>", lambda _e: self.btn_clear.config(bg="#eff6ff"))
        self.btn_clear.bind("<Leave>", lambda _e: self.btn_clear.config(bg="#ffffff"))

        self.btn_today = tk.Label(
            ftr, text="Heute", font=("Segoe UI", 9), bg="#ffffff", fg="#0284c7",
            cursor="hand2", padx=4, pady=2,
        )
        self.btn_today.pack(side="right")
        self.btn_today.bind("<Button-1>", lambda _e: self.pick_today())
        self.btn_today.bind("<Enter>", lambda _e: self.btn_today.config(bg="#eff6ff"))
        self.btn_today.bind("<Leave>", lambda _e: self.btn_today.config(bg="#ffffff"))

        self._render()
        self._position_near_anchor()

        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<Button-1>", self._on_click_check)
        self.bind("<FocusOut>", self._on_focus_out)
        self.focus_set()
        try:
            self.grab_set()
        except Exception:
            pass

    @staticmethod
    def _parse_initial_date(raw: str):
        text = str(raw or "").strip()
        if not text:
            return None
        for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%m.%Y", "%Y-%m"):
            try:
                return datetime.datetime.strptime(text, fmt).date()
            except ValueError:
                pass
        if re.fullmatch(r"\d{4}", text):
            y = int(text)
            if 1900 <= y <= 2100:
                return datetime.date(y, 1, 1)
        return None

    def _position_near_anchor(self):
        self.update_idletasks()
        pw = self.winfo_reqwidth()
        ph = self.winfo_reqheight()
        ax = self.anchor_widget.winfo_rootx()
        ay = self.anchor_widget.winfo_rooty()
        aw = self.anchor_widget.winfo_width()
        ah = self.anchor_widget.winfo_height()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = min(max(6, ax), max(6, sw - pw - 6))
        if ay + ah + ph + 10 <= sh:
            y = ay + ah + 4
        else:
            y = max(6, ay - ph - 4)
        self.geometry(f"+{int(x)}+{int(y)}")

    def _on_click_check(self, event):
        if self._closed:
            return
        try:
            rx, ry = self.winfo_rootx(), self.winfo_rooty()
            rw, rh = self.winfo_width(), self.winfo_height()
            if not (rx <= event.x_root <= rx + rw and ry <= event.y_root <= ry + rh):
                self.close()
        except Exception:
            pass

    def _on_focus_out(self, _event=None):
        if self._closed:
            return
        self.after(25, self._verify_focus)

    def _verify_focus(self):
        if self._closed:
            return
        try:
            foc = self.focus_get()
            if foc is None or foc.winfo_toplevel() is not self:
                self.close()
        except Exception:
            pass

    def toggle_mode(self):
        self.mode = "months" if self.mode == "days" else "days"
        self._render()

    def step_prev(self):
        if self.mode == "months":
            self.view_year = max(1900, self.view_year - 1)
        else:
            if self.view_month == 1:
                self.view_month = 12
                self.view_year = max(1900, self.view_year - 1)
            else:
                self.view_month -= 1
        self._render()

    def step_next(self):
        if self.mode == "months":
            self.view_year = min(2100, self.view_year + 1)
        else:
            if self.view_month == 12:
                self.view_month = 1
                self.view_year = min(2100, self.view_year + 1)
            else:
                self.view_month += 1
        self._render()

    def _build_month_matrix(self):
        cal = calendar.Calendar(firstweekday=0)
        weeks = cal.monthdatescalendar(self.view_year, self.view_month)
        while len(weeks) < 6:
            last_day = weeks[-1][-1]
            weeks.append([last_day + datetime.timedelta(days=i + 1) for i in range(7)])
        return weeks[:6]

    def _render(self):
        for w in self.body.winfo_children():
            w.destroy()

        if self.mode == "months":
            self.lbl_title.config(text=f"{self.view_year} ▴")
            grid = tk.Frame(self.body, bg="#ffffff")
            grid.pack(fill="both", expand=True, pady=(4, 4))
            for idx, m_short in enumerate(self.MONTHS_SHORT_DE):
                m_num = idx + 1
                is_cur = (m_num == self.view_month)
                bg = "#dbeafe" if is_cur else "#ffffff"
                fg = "#1d4ed8" if is_cur else "#0f172a"
                fnt = ("Segoe UI", 9, "bold") if is_cur else ("Segoe UI", 9)
                cell = tk.Label(
                    grid, text=m_short, font=fnt, bg=bg, fg=fg,
                    width=7, height=2, cursor="hand2",
                    highlightthickness=1,
                    highlightbackground="#2563eb" if is_cur else "#ffffff",
                )
                cell.grid(row=idx // 3, column=idx % 3, padx=3, pady=3)
                cell.bind("<Button-1>", lambda _e, m=m_num: self._select_month(m))
                if not is_cur:
                    cell.bind("<Enter>", lambda _e, c=cell: c.config(bg="#f1f5f9"))
                    cell.bind("<Leave>", lambda _e, c=cell: c.config(bg="#ffffff"))
            return

        self.lbl_title.config(text=f"{self.MONTHS_DE[self.view_month - 1]} {self.view_year} ▾")
        grid = tk.Frame(self.body, bg="#ffffff")
        grid.pack(fill="both", expand=True)

        for col, wd in enumerate(self.WEEKDAYS_DE):
            tk.Label(
                grid, text=wd, font=("Segoe UI", 9), bg="#ffffff", fg="#334155",
                width=4, pady=2,
            ).grid(row=0, column=col, padx=1, pady=(0, 3))

        weeks = self._build_month_matrix()
        for r_idx, week in enumerate(weeks, start=1):
            for c_idx, day in enumerate(week):
                in_month = (day.month == self.view_month)
                is_sel = (self.selected_date == day)
                is_today = (self.today == day)

                if is_sel:
                    bg, fg = "#cbd5e1", "#0f172a"
                    fnt = ("Segoe UI", 9, "bold")
                    bd_col = "#0f172a"
                elif is_today:
                    bg, fg = "#ffffff", "#0f172a"
                    fnt = ("Segoe UI", 9, "bold")
                    bd_col = "#0f172a"
                else:
                    bg = "#ffffff"
                    fg = "#0f172a" if in_month else "#94a3b8"
                    fnt = ("Segoe UI", 9)
                    bd_col = "#ffffff"

                lbl = tk.Label(
                    grid,
                    text=str(day.day),
                    font=fnt,
                    bg=bg,
                    fg=fg,
                    width=3,
                    pady=3,
                    cursor="hand2",
                    highlightthickness=1,
                    highlightbackground=bd_col,
                )
                lbl.grid(row=r_idx, column=c_idx, padx=1, pady=1)
                lbl.bind("<Button-1>", lambda _e, d=day: self.select_date(d))
                if not is_sel:
                    hover_c = "#dbeafe" if is_today else "#f1f5f9"
                    lbl.bind("<Enter>", lambda _e, l=lbl, hc=hover_c: l.config(bg=hc))
                    lbl.bind("<Leave>", lambda _e, l=lbl, nb=bg: l.config(bg=nb))

    def _select_month(self, month: int):
        self.view_month = month
        self.mode = "days"
        self._render()

    def select_date(self, day: datetime.date):
        self.selected_date = day
        self.target_var.set(day.strftime("%d.%m.%Y"))
        if callable(self.on_select):
            self.on_select(day)
        self.close()

    def pick_today(self):
        self.select_date(self.today)

    def clear_date(self):
        self.selected_date = None
        self.target_var.set("")
        if callable(self.on_select):
            self.on_select(None)
        self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


def create_filter_box(parent, textvariable: tk.StringVar, width: int, height: int,
                      placeholder: str = "", prefix: str = "", on_calendar=None,
                      on_commit=None, on_focus_save=None):
    """Erstellt ein modernes, abgerundetes Eingabefeld (r=8) passend zu den Pill-Buttons."""
    outer = tk.Canvas(
        parent,
        width=width,
        height=height,
        bg=parent["bg"],
        bd=0,
        highlightthickness=0,
    )
    img_norm = get_rounded_photo(width, height, 8, "#ffffff", "#cbd5e1")
    img_focus = get_rounded_photo(width, height, 8, "#ffffff", "#2563eb")
    outer._img_norm = img_norm
    outer._img_focus = img_focus
    bg_item = outer.create_image(0, 0, image=img_norm, anchor="nw")

    inner = tk.Frame(outer, bg="#ffffff", bd=0, highlightthickness=0)
    outer.create_window(
        width // 2,
        height // 2,
        window=inner,
        width=max(20, width - 8),
        height=max(14, height - 4),
        anchor="center",
    )

    if prefix:
        lbl_pre = tk.Label(
            inner, text=prefix, font=("Segoe UI", 8, "bold"),
            bg="#ffffff", fg="#64748b", padx=0, pady=0, cursor="xterm",
        )
        lbl_pre.pack(side="left", padx=(4, 4))
    else:
        lbl_pre = None

    # Kalender-Button ZUERST rechts packen, damit ent_wrap(expand=True) ihn niemals verdraengt
    cal_btn = None
    if callable(on_calendar):
        ico_norm = get_calendar_icon_photo(fg="#475569", bg="#ffffff")
        ico_hover = get_calendar_icon_photo(fg="#2563eb", bg="#eff6ff")
        cal_btn = tk.Label(inner, image=ico_norm, bg="#ffffff", cursor="hand2", padx=4)
        cal_btn.image = ico_norm
        cal_btn._ico_norm = ico_norm
        cal_btn._ico_hover = ico_hover
        cal_btn._bg_norm = "#ffffff"
        cal_btn._bg_hover = "#eff6ff"
        cal_btn.pack(side="right", fill="y", padx=(2, 1))
        cal_btn.bind("<Enter>", lambda _e: cal_btn.config(bg=cal_btn._bg_hover, image=cal_btn._ico_hover))
        cal_btn.bind("<Leave>", lambda _e: cal_btn.config(bg=cal_btn._bg_norm, image=cal_btn._ico_norm))
        cal_btn.bind("<Button-1>", lambda _e: on_calendar(outer))

    ent_wrap = tk.Frame(inner, bg="#ffffff", bd=0, highlightthickness=0)
    ent_wrap.pack(side="left", fill="both", expand=True, padx=(5 if not prefix else 0, 2))

    entry = tk.Entry(
        ent_wrap,
        textvariable=textvariable,
        width=1,
        font=("Segoe UI", 9),
        bg="#ffffff",
        fg="#0f172a",
        bd=0,
        highlightthickness=0,
        relief="flat",
        insertbackground="#0f172a",
    )
    entry.pack(fill="both", expand=True)

    ph_label = tk.Label(
        ent_wrap,
        text=placeholder,
        font=("Segoe UI", 9),
        bg="#ffffff",
        fg="#94a3b8",
        anchor="w",
        cursor="xterm",
        bd=0,
    )

    if lbl_pre is not None:
        lbl_pre.bind("<Button-1>", lambda _e: entry.focus_set())
    ph_label.bind("<Button-1>", lambda _e: entry.focus_set())

    def sync_placeholder(*_args):
        try:
            focused = (entry.focus_get() is entry)
        except Exception:
            focused = False
        if not textvariable.get() and not focused:
            ph_label.place(relx=0.0, rely=0.5, anchor="w", relwidth=1.0)
        else:
            ph_label.place_forget()

    def on_focus_in(_e=None):
        outer.itemconfig(bg_item, image=outer._img_focus)
        sync_placeholder()

    def on_focus_out(_e=None):
        outer.itemconfig(bg_item, image=outer._img_norm)
        sync_placeholder()
        if callable(on_focus_save):
            on_focus_save()

    def refresh_entry_theme(mode=None):
        active = (mode or CURRENT_THEME_MODE or "light").lower()
        is_dark = (active == "dark")
        in_bg = "#0f172a" if is_dark else "#ffffff"
        brd = "#334155" if is_dark else "#cbd5e1"
        brd_foc = "#3b82f6" if is_dark else "#2563eb"
        fg_main = "#f8fafc" if is_dark else "#0f172a"
        fg_muted = "#94a3b8" if is_dark else "#64748b"
        fg_ph = "#64748b" if is_dark else "#94a3b8"
        try:
            outer.config(bg=parent.cget("bg"))
        except Exception:
            pass
        outer._img_norm = get_rounded_photo(width, height, 8, in_bg, brd)
        outer._img_focus = get_rounded_photo(width, height, 8, in_bg, brd_foc)
        try:
            focused = (entry.focus_get() is entry)
        except Exception:
            focused = False
        outer.itemconfig(bg_item, image=(outer._img_focus if focused else outer._img_norm))
        inner.config(bg=in_bg)
        ent_wrap.config(bg=in_bg)
        entry.config(bg=in_bg, fg=fg_main, insertbackground=fg_main)
        ph_label.config(bg=in_bg, fg=fg_ph)
        if lbl_pre is not None:
            lbl_pre.config(bg=in_bg, fg=fg_muted)
        if cal_btn is not None:
            ico_n = get_calendar_icon_photo(fg="#cbd5e1" if is_dark else "#475569", bg=in_bg)
            ico_h_bg = "#1e293b" if is_dark else "#eff6ff"
            ico_h = get_calendar_icon_photo(fg="#60a5fa" if is_dark else "#2563eb", bg=ico_h_bg)
            cal_btn._ico_norm = ico_n
            cal_btn._ico_hover = ico_h
            cal_btn._bg_norm = in_bg
            cal_btn._bg_hover = ico_h_bg
            cal_btn.image = ico_n
            cal_btn.config(bg=in_bg, image=ico_n)

    outer._handles_own_children_theme = True
    outer.refresh_theme = refresh_entry_theme

    entry.bind("<FocusIn>", on_focus_in)
    entry.bind("<FocusOut>", on_focus_out)
    if callable(on_commit):
        entry.bind("<Return>", lambda _e: on_commit())
    if callable(on_calendar):
        entry.bind("<F4>", lambda _e: on_calendar(outer))
    textvariable.trace_add("write", sync_placeholder)
    sync_placeholder()

    outer.inner = inner
    outer.entry = entry
    outer.placeholder_label = ph_label
    outer.calendar_button = cal_btn
    outer.sync_placeholder = sync_placeholder
    return outer


_APP_USER_MODEL_ID_SET = False


def draw_folder_app_icon_image(size=256):
    """Zeichnet das App-Ordner-Symbol (Fluent Gold-Ordner mit weissem Blatt & '7'-Badge)."""
    scale = 3 if size < 128 else 2
    s = size * scale
    u = s / 256.0
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Hintere Ordner-Lasche + Ruecken (Warmes Gold/Bernstein)
    d.rounded_rectangle(
        [18 * u, 38 * u, 114 * u, 110 * u],
        radius=max(2, round(18 * u)),
        fill="#d97706",
    )
    d.rounded_rectangle(
        [18 * u, 58 * u, 238 * u, 206 * u],
        radius=max(3, round(24 * u)),
        fill="#d97706",
        outline="#b45309",
        width=max(1, round(3 * u)),
    )

    # Hervorschauendes Dokument-Blatt fuer tiefe Dokumenten-Optik
    d.rounded_rectangle(
        [36 * u, 74 * u, 220 * u, 134 * u],
        radius=max(2, round(12 * u)),
        fill="#ffffff",
        outline="#e2e8f0",
        width=max(1, round(2 * u)),
    )

    # Vordere Ordner-Klappe (Leuchtendes Fluent-Gold)
    d.rounded_rectangle(
        [18 * u, 92 * u, 238 * u, 214 * u],
        radius=max(3, round(24 * u)),
        fill="#fbbf24",
        outline="#d97706",
        width=max(1, round(3 * u)),
    )
    # Dezenter Glanzstreifen oben auf der vorderen Ordner-Kante
    d.rounded_rectangle(
        [26 * u, 97 * u, 230 * u, 108 * u],
        radius=max(1, round(6 * u)),
        fill="#fde68a",
    )

    # Blaues "7"-Badge unten rechts als Wiedererkennungszeichen fuer 7-Ordner-Pro
    bx0, by0, bx1, by1 = 148 * u, 142 * u, 242 * u, 236 * u
    d.ellipse(
        [bx0, by0, bx1, by1],
        fill="#2563eb",
        outline="#ffffff",
        width=max(2, round(6 * u)),
    )
    # Klare Vektor-"7" im Badge
    lw = max(2, round(11 * u))
    d.line(
        [(177 * u, 169 * u), (213 * u, 169 * u), (188 * u, 211 * u)],
        fill="#ffffff",
        width=lw,
        joint="curve",
    )

    return img.resize((size, size), Image.Resampling.LANCZOS)


def ensure_app_ico_path() -> Path | None:
    """Liefert den Pfad zur app_icon.ico (Bundle/Repo oder automatisch erzeugt in DATA_DIR)."""
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "app_icon.ico")
    candidates.append(BASE_DIR / "app_icon.ico")
    candidates.append(DATA_DIR / "app_icon.ico")
    for cand in candidates:
        try:
            if cand.is_file() and cand.stat().st_size > 0:
                return cand
        except Exception:
            pass
    fallback = DATA_DIR / "app_icon.ico"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ico_img = draw_folder_app_icon_image(256)
        ico_img.save(
            str(fallback),
            format="ICO",
            sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
        )
        return fallback
    except Exception:
        return None


def get_app_badge_icon_photo(size=32):
    """Laedt das App-Icon (Diskette aus app_icon.ico) als gecachtes Tkinter-PhotoImage."""
    key = ("app_badge_icon_ico", size)
    if key in IMG_CACHE:
        return IMG_CACHE[key]
    img = None
    try:
        ico_path = ensure_app_ico_path()
        if ico_path and Path(ico_path).is_file():
            with Image.open(str(ico_path)) as raw:
                img = raw.convert("RGBA")
                if img.size != (size, size):
                    img = img.resize((size, size), Image.Resampling.LANCZOS)
    except Exception:
        img = None
    if img is None:
        img = draw_folder_app_icon_image(size)
    photo = ImageTk.PhotoImage(img)
    IMG_CACHE[key] = photo
    return photo


def apply_dialog_theme(win):
    """Wendet das aktuelle Hell/Dunkel-Theme auf oeffnende Toplevel-Dialoge an."""
    try:
        if not win or not win.winfo_exists():
            return
    except Exception:
        return
    mode = CURRENT_THEME_MODE or "light"
    apply_dwm_dark_titlebar(win, mode == "dark")
    if mode == "dark":
        apply_theme_to_widget_tree(win, mode)
        ct = getattr(win, "content_tree", None)
        if ct is not None:
            try:
                ct.tag_configure("tag_dir", foreground="#f8fafc")
                ct.tag_configure("tag_doc", foreground="#38bdf8")
                ct.tag_configure("tag_other", foreground="#94a3b8")
            except Exception:
                pass


def apply_window_folder_icon(win, is_root: bool = False):
    """Setzt Windows-AppUserModelID, Win32-.ico (ohne Tkinter-Feder) und Taskbar-/Titelleisten-Icon."""
    global _APP_USER_MODEL_ID_SET
    if sys.platform.startswith("win") and not _APP_USER_MODEL_ID_SET:
        # Nur im Python-Script-Modus eigene AppUserModelID setzen (sonst gruppiert Windows
        # unter pythonw.exe); bei kompilierter .exe wuerde eine abweichende AppID ein
        # zweites Taskleisten-Icon neben der angehefteten .exe-Verknuepfung erzeugen.
        if getattr(sys, "frozen", False):
            _APP_USER_MODEL_ID_SET = True
        else:
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SevenFolderPro.Organizer.1")
                _APP_USER_MODEL_ID_SET = True
            except Exception:
                pass
    ico_applied = False
    ico_path = None
    try:
        ico_path = ensure_app_ico_path()
        if ico_path:
            if is_root:
                win.iconbitmap(default=str(ico_path))
            else:
                win.iconbitmap(str(ico_path))
            ico_applied = True
    except Exception:
        pass
    if sys.platform.startswith("win") and ico_path:
        def _force_win32_taskbar_icon(w=win, p=str(ico_path)):
            try:
                if not w or not w.winfo_exists():
                    return
                import ctypes
                user32 = ctypes.windll.user32
                WM_SETICON = 0x0080
                ICON_SMALL = 0
                ICON_BIG = 1
                IMAGE_ICON = 1
                LR_LOADFROMFILE = 0x0010
                LR_DEFAULTSIZE = 0x0040
                hwnd = user32.GetParent(w.winfo_id()) or w.winfo_id()
                h_big = user32.LoadImageW(None, p, IMAGE_ICON, 48, 48, LR_LOADFROMFILE | LR_DEFAULTSIZE)
                h_small = user32.LoadImageW(None, p, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
                if h_big:
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_big)
                if h_small:
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_small)
                w._win32_hicons = (h_big, h_small)
            except Exception:
                pass
        try:
            win.after(60, _force_win32_taskbar_icon)
            if is_root:
                win.after(450, _force_win32_taskbar_icon)
        except Exception:
            pass
    if not (sys.platform.startswith("win") and ico_applied):
        try:
            ico64 = get_app_badge_icon_photo(64)
            ico32 = get_app_badge_icon_photo(32)
            win._win_icon_refs = (ico64, ico32)
            win.iconphoto(bool(is_root), ico64, ico32)
        except Exception:
            pass
    if not is_root:
        try:
            win.after(0, lambda w=win: apply_dialog_theme(w))
        except Exception:
            pass


def bring_window_to_front(win):
    """Holt das Fenster unter Windows zuverlaessig in den Vordergrund (auch ueber maximierte Apps)."""
    try:
        if not win or not win.winfo_exists():
            return
    except Exception:
        return
    try:
        if str(win.state()) == "iconic":
            win.deiconify()
    except Exception:
        try:
            win.deiconify()
        except Exception:
            pass
    try:
        win.lift()
    except Exception:
        pass
    if sys.platform.startswith("win"):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            win.update_idletasks()
            hwnd = int(win.winfo_id())
            try:
                root_hwnd = user32.GetAncestor(hwnd, 2)  # GA_ROOT = 2
                if root_hwnd:
                    hwnd = root_hwnd
            except Exception:
                pass
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            else:
                user32.ShowWindow(hwnd, 5)  # SW_SHOW
            fg_hwnd = user32.GetForegroundWindow()
            cur_tid = kernel32.GetCurrentThreadId()
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
            attached = False
            if fg_tid and fg_tid != cur_tid:
                attached = bool(user32.AttachThreadInput(fg_tid, cur_tid, True))
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(fg_tid, cur_tid, False)
        except Exception:
            pass
    try:
        win.attributes("-topmost", True)
        win.update_idletasks()

        def _clear_topmost():
            try:
                if win.winfo_exists():
                    win.attributes("-topmost", False)
            except Exception:
                pass

        win.after(180, _clear_topmost)
        win.focus_force()
    except Exception:
        pass


def get_notice_icon_photo(kind="info", bg="#ffffff", size=38):
    key = ("notice_fluent_icon", kind, bg, size)
    if key in IMG_CACHE:
        return IMG_CACHE[key]
    palette = {
        "info": ("#eff6ff", "#93c5fd", "#2563eb"),
        "question": ("#eff6ff", "#93c5fd", "#2563eb"),
        "success": ("#ecfdf5", "#6ee7b7", "#059669"),
        "warning": ("#fffbeb", "#fcd34d", "#d97706"),
        "error": ("#fef2f2", "#fca5a5", "#dc2626"),
    }
    fill_c, ring_c, fg_c = palette.get(kind, palette["info"])
    scale = 3
    s = size * scale
    img = Image.new("RGBA", (s, s), bg)
    d = ImageDraw.Draw(img)
    lw = max(2, round(1.8 * scale))
    d.ellipse([2 * scale, 2 * scale, s - 2 * scale, s - 2 * scale], fill=fill_c, outline=ring_c, width=lw)
    u = s / 18.0
    glw = max(2, round(1.7 * u))

    if kind == "question":
        d.arc([5.6 * u, 4.4 * u, 12.4 * u, 10.6 * u], start=195, end=45, fill=fg_c, width=glw)
        d.line([(11.7 * u, 8.7 * u), (9.0 * u, 10.7 * u), (9.0 * u, 11.7 * u)], fill=fg_c, width=glw)
        dr = max(2, round(1.1 * u))
        d.ellipse([9.0 * u - dr, 13.9 * u - dr, 9.0 * u + dr, 13.9 * u + dr], fill=fg_c)
    elif kind == "success":
        d.line([(5.0 * u, 9.3 * u), (8.0 * u, 12.3 * u), (13.3 * u, 6.2 * u)], fill=fg_c, width=max(3, round(1.9 * u)), joint="curve")
    elif kind == "warning":
        d.line([(9.0 * u, 4.7 * u), (9.0 * u, 10.7 * u)], fill=fg_c, width=max(3, round(1.9 * u)))
        dr = max(2, round(1.15 * u))
        d.ellipse([9.0 * u - dr, 13.4 * u - dr, 9.0 * u + dr, 13.4 * u + dr], fill=fg_c)
    elif kind == "error":
        d.line([(5.8 * u, 5.8 * u), (12.2 * u, 12.2 * u)], fill=fg_c, width=max(3, round(1.8 * u)))
        d.line([(12.2 * u, 5.8 * u), (5.8 * u, 12.2 * u)], fill=fg_c, width=max(3, round(1.8 * u)))
    else:
        dr = max(2, round(1.1 * u))
        d.ellipse([9.0 * u - dr, 5.2 * u - dr, 9.0 * u + dr, 5.2 * u + dr], fill=fg_c)
        d.line([(9.0 * u, 7.9 * u), (9.0 * u, 13.5 * u)], fill=fg_c, width=max(3, round(1.9 * u)))

    img = img.resize((size, size), Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    IMG_CACHE[key] = photo
    return photo


def get_confirm_icon_photo(bg="#ffffff", size=38):
    return get_notice_icon_photo(kind="question", bg=bg, size=size)


class ModernNoticeDialog(tk.Toplevel):
    """Einheitlicher Light-Fluent Dialog fuer Hinweise, Erfolg, Warnungen, Fehler & Bestaetigungen."""

    def __init__(self, parent, title: str, headline: str, subline: str = "",
                 kind: str = "info", confirm: bool = False,
                 confirm_text: str = "Bestätigen", cancel_text: str = "Abbrechen",
                 ok_text: str = "OK", auto_close_ms: int | None = None,
                 extra_button_text: str = "", extra_button_command=None):
        super().__init__(parent)
        self.result = False
        self.kind = kind
        self.headline_text = headline
        self.subline_text = subline
        self._auto_close_after_id = None
        if auto_close_ms is None:
            auto_close_ms = 3000 if (not confirm and not extra_button_text and kind in ("success", "info")) else 0
        self.auto_close_ms = max(0, int(auto_close_ms))

        self.title(title)
        self.configure(bg="#f8fafc")
        self.resizable(False, False)
        self.transient(parent)
        apply_window_folder_icon(self)

        outer = tk.Frame(self, bg="#f8fafc", padx=18, pady=16)
        outer.pack(fill="both", expand=True)

        card = tk.Frame(
            outer,
            bg="#ffffff",
            highlightbackground="#cbd5e1",
            highlightthickness=1,
            padx=18,
            pady=16,
        )
        card.pack(fill="both", expand=True)

        ico = get_notice_icon_photo(kind=kind)
        lbl_ico = tk.Label(card, image=ico, bg="#ffffff", bd=0)
        lbl_ico.image = ico
        lbl_ico.pack(side="left", anchor="n", padx=(0, 14), pady=(2, 0))

        txt_box = tk.Frame(card, bg="#ffffff")
        txt_box.pack(side="left", fill="both", expand=True)

        self.lbl_headline = tk.Label(
            txt_box,
            text=headline,
            font=("Segoe UI", 10 if subline and "\n" in headline else 11, "bold"),
            bg="#ffffff",
            fg="#0f172a",
            anchor="w",
            justify="left",
            wraplength=460,
        )
        self.lbl_headline.pack(fill="x", anchor="w", pady=(0, 4 if subline else 0))

        if subline:
            self.lbl_subline = tk.Label(
                txt_box,
                text=subline,
                font=("Segoe UI", 9),
                bg="#ffffff",
                fg="#475569",
                anchor="w",
                justify="left",
                wraplength=460,
            )
            self.lbl_subline.pack(fill="x", anchor="w")
        else:
            self.lbl_subline = None

        btn_bar = tk.Frame(outer, bg="#f8fafc")
        btn_bar.pack(fill="x", pady=(14, 0))

        primary_bg = "#059669" if kind == "success" else ("#dc2626" if kind == "error" else "#2563eb")
        primary_hover = "#047857" if kind == "success" else ("#b91c1c" if kind == "error" else "#1d4ed8")

        if confirm:
            self.btn_confirm = ModernRoundedButton(
                btn_bar,
                text=confirm_text,
                command=self._on_confirm,
                bg=primary_bg,
                fg="#ffffff",
                hover_bg=primary_hover,
                font=("Segoe UI", 9, "bold"),
                r=8,
                padx=16,
                pady=7,
            )
            self.btn_confirm.pack(side="right")

            self.btn_cancel = ModernRoundedButton(
                btn_bar,
                text=cancel_text,
                command=self._on_cancel,
                bg="#ffffff",
                fg="#1e293b",
                hover_bg="#f1f5f9",
                border_color="#94a3b8",
                font=("Segoe UI", 9, "bold"),
                r=8,
                padx=16,
                pady=7,
            )
            self.btn_cancel.pack(side="right", padx=(0, 10))
        else:
            self.btn_confirm = ModernRoundedButton(
                btn_bar,
                text=ok_text,
                command=self._on_confirm,
                bg=primary_bg,
                fg="#ffffff",
                hover_bg=primary_hover,
                font=("Segoe UI", 9, "bold"),
                r=8,
                padx=20,
                pady=7,
            )
            self.btn_confirm.pack(side="right")
            self.btn_cancel = None

            if extra_button_text and callable(extra_button_command):
                def _on_extra_action():
                    self._stop_auto_close()
                    try:
                        self.grab_release()
                    except Exception:
                        pass
                    self.destroy()
                    def _run_extra():
                        try:
                            extra_button_command()
                        except Exception as _e:
                            print(f"Fehler in Dialog-Zusatzbutton: {_e}")
                    try:
                        parent.after(120, _run_extra)
                    except Exception:
                        _run_extra()

                ModernRoundedButton(
                    btn_bar,
                    text=extra_button_text,
                    command=_on_extra_action,
                    bg="#ffffff",
                    fg="#2563eb",
                    hover_bg="#eff6ff",
                    border_color="#93c5fd",
                    font=("Segoe UI", 9),
                    r=8,
                    padx=14,
                    pady=7,
                ).pack(side="right", padx=(0, 10))

        self.lbl_auto_close = None
        if self.auto_close_ms > 0:
            secs = max(1, (self.auto_close_ms + 999) // 1000)
            self.lbl_auto_close = tk.Label(
                btn_bar,
                text=f"Schließt automatisch in {secs} s",
                font=("Segoe UI", 8),
                bg="#f8fafc",
                fg="#64748b",
                anchor="w",
            )
            self.lbl_auto_close.pack(side="left", pady=(4, 0))
            self._schedule_auto_close(self.auto_close_ms)

        self.bind("<Return>", lambda _e=None: self._on_confirm())
        self.bind("<KP_Enter>", lambda _e=None: self._on_confirm())
        self.bind("<Escape>", lambda _e=None: self._on_cancel() if confirm else self._on_confirm())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel if confirm else self._on_confirm)

        self.update_idletasks()
        w = max(380, self.winfo_reqwidth())
        h = self.winfo_reqheight()
        try:
            if parent.winfo_ismapped():
                px = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
                py = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
            else:
                px = (self.winfo_screenwidth() - w) // 2
                py = (self.winfo_screenheight() - h) // 2
        except Exception:
            px, py = 200, 200
        x = max(12, min(int(px), max(12, self.winfo_screenwidth() - w - 12)))
        y = max(12, min(int(py), max(12, self.winfo_screenheight() - h - 12)))
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.grab_set()
        self.focus_set()

    def _schedule_auto_close(self, remaining_ms: int):
        if remaining_ms <= 0:
            self._auto_close_after_id = self.after(10, self._on_confirm)
            return
        secs = max(1, (remaining_ms + 999) // 1000)
        try:
            if self.lbl_auto_close and self.lbl_auto_close.winfo_exists():
                self.lbl_auto_close.configure(text=f"Schließt automatisch in {secs} s")
        except Exception:
            pass
        step_ms = min(1000, remaining_ms)
        self._auto_close_after_id = self.after(
            step_ms,
            lambda next_ms=remaining_ms - step_ms: self._tick_auto_close(next_ms),
        )

    def _tick_auto_close(self, remaining_ms: int):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if remaining_ms <= 0:
            self._on_confirm()
        else:
            self._schedule_auto_close(remaining_ms)

    def _on_confirm(self):
        self.result = True
        self._close()

    def _on_cancel(self):
        self.result = False
        self._close()

    def _close(self):
        if getattr(self, "_auto_close_after_id", None) is not None:
            try:
                self.after_cancel(self._auto_close_after_id)
            except Exception:
                pass
            self._auto_close_after_id = None
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


class ModernConfirmDialog(ModernNoticeDialog):
    """Moderner Light-Fluent Bestaetigungsdialog ohne System-Altlasten."""

    def __init__(self, parent, title: str, headline: str, subline: str = "",
                 confirm_text: str = "Bestätigen", cancel_text: str = "Abbrechen"):
        super().__init__(
            parent,
            title=title,
            headline=headline,
            subline=subline,
            kind="question",
            confirm=True,
            confirm_text=confirm_text,
            cancel_text=cancel_text,
        )


class GitHubSyncDialog(tk.Toplevel):
    """Fluent-Dialog für GitHub-Sync, EXE-Direktdownload & sicheres Versions-Retour."""

    def __init__(self, parent_app):
        super().__init__(parent_app)
        self.app = parent_app
        self.git_info = {}
        self._busy = False
        self._dark = (getattr(parent_app, "theme_mode", "light") == "dark")

        self.title(f"GitHub Versions-Aktualisierung • {APP_VERSION}")
        apply_window_folder_icon(self)
        apply_dwm_dark_titlebar(self, self._dark)
        self.resizable(True, True)
        self.minsize(790, 620)
        self.transient(parent_app)

        if self._dark:
            self.p = {
                "bg": "#0f172a", "card": "#1e293b", "subcard": "#0b1220",
                "border": "#334155", "fg": "#f8fafc", "muted": "#94a3b8",
                "accent": "#3b82f6", "link": "#60a5fa",
                "usb_bg": "#064e3b", "usb_border": "#059669", "usb_fg": "#d1fae5",
                "step_bg": "#0f172a", "step_border": "#1e293b",
                "btn_bg": "#334155", "btn_hover": "#475569", "btn_fg": "#f8fafc",
                "input_bg": "#0f172a", "log_bg": "#020617", "log_fg": "#34d399",
            }
        else:
            self.p = {
                "bg": "#f8fafc", "card": "#ffffff", "subcard": "#f8fafc",
                "border": "#cbd5e1", "fg": "#0f172a", "muted": "#475569",
                "accent": "#2563eb", "link": "#2563eb",
                "usb_bg": "#ecfdf5", "usb_border": "#6ee7b7", "usb_fg": "#065f46",
                "step_bg": "#ffffff", "step_border": "#bbf7d0",
                "btn_bg": "#ffffff", "btn_hover": "#eff6ff", "btn_fg": "#1e293b",
                "input_bg": "#ffffff", "log_bg": "#0f172a", "log_fg": "#34d399",
            }

        self.configure(bg=self.p["bg"])
        self.bind("<Escape>", lambda e: self.destroy())
        self._build_ui()
        self.refresh_git_info({
            "sync_state": "checking",
            "app_version": APP_VERSION,
            "sync_title": f"Prüfe GitHub-Stand ({APP_VERSION})...",
            "sync_detail": "Online-Abgleich mit GitHub läuft im Hintergrund...",
            "branch": "main",
            "remote_url": github_sync.DEFAULT_REMOTE_URL,
            "has_remote_ref": True,
        })
        self._center_and_show()
        self.after(40, self._load_git_info_async)

    def _load_git_info_async(self):
        def _worker():
            data = github_sync.get_git_info(APP_VERSION)
            def _apply():
                try:
                    if self.winfo_exists():
                        self.refresh_git_info(data)
                        self.app.refresh_git_badge()
                except Exception:
                    pass
            try:
                self.after(0, _apply)
            except Exception:
                pass
        threading.Thread(target=_worker, daemon=True).start()

    def _fit_height(self, center=False):
        try:
            self.update_idletasks()
            w = max(810, self.winfo_reqwidth() + 8)
            scr_h = self.winfo_screenheight() or 900
            max_h = max(580, scr_h - 64)
            h = min(max_h, max(640, self.winfo_reqheight() + 18))
            if center:
                px = self.app.winfo_rootx()
                py = self.app.winfo_rooty()
                pw = self.app.winfo_width() or 1000
                ph = self.app.winfo_height() or 700
                x = max(16, px + (pw - w) // 2)
                y = max(12, min(scr_h - h - 42, py + (ph - h) // 2))
                self.geometry(f"{w}x{h}+{x}+{y}")
            else:
                self.geometry(f"{w}x{h}")
        except Exception:
            pass

    def _center_and_show(self):
        self._fit_height(center=True)
        self.grab_set()
        bring_window_to_front(self)

    def _build_ui(self):
        p = self.p
        root = tk.Frame(self, bg=p["bg"], padx=18, pady=14)
        root.pack(fill="both", expand=True)

        # Header
        hdr = tk.Frame(root, bg=p["bg"])
        hdr.pack(fill="x", pady=(0, 10))
        badge_bg = "#312e81" if self._dark else "#e0e7ff"
        badge_fg = "#c7d2fe" if self._dark else "#3730a3"
        icon_lbl = tk.Label(hdr, text="⬆", font=("Segoe UI", 13, "bold"), bg=badge_bg, fg=badge_fg, width=3, pady=4)
        icon_lbl.pack(side="left", padx=(0, 10))
        title_box = tk.Frame(hdr, bg=p["bg"])
        title_box.pack(side="left", fill="x", expand=True)
        top_t = tk.Frame(title_box, bg=p["bg"])
        top_t.pack(anchor="w")
        tk.Label(top_t, text="GitHub Versions-Aktualisierung", font=("Segoe UI", 13, "bold"), bg=p["bg"], fg=p["fg"]).pack(side="left")
        self.lbl_dialog_ver = tk.Label(top_t, text=f" {APP_VERSION} ", font=("Segoe UI", 9, "bold"), bg=badge_bg, fg=badge_fg, padx=6, pady=1)
        self.lbl_dialog_ver.pack(side="left", padx=(8, 0))
        tk.Label(title_box, text="Automatische Aktualisierung • Sync & Versions-Retour", font=("Segoe UI", 9), bg=p["bg"], fg=p["muted"]).pack(anchor="w")

        # 1. Sync Status Banner
        self.sync_banner = tk.Frame(root, bg=p["card"], highlightthickness=1, highlightbackground=p["border"], padx=12, pady=9)
        self.sync_banner.pack(fill="x", pady=(0, 9))
        banner_top = tk.Frame(self.sync_banner, bg=p["card"])
        banner_top.pack(fill="x")
        self.lbl_sync_title = tk.Label(banner_top, text="Prüfe GitHub-Status...", font=("Segoe UI", 10, "bold"), bg=p["card"], fg=p["fg"], anchor="w")
        self.lbl_sync_title.pack(side="left", fill="x", expand=True)
        self.lbl_sync_badge = tk.Label(banner_top, text="Lade...", font=("Segoe UI", 8, "bold"), bg=p["subcard"], fg=p["fg"], padx=8, pady=2)
        self.lbl_sync_badge.pack(side="right", padx=(8, 0))
        self.lbl_sync_detail = tk.Label(self.sync_banner, text="", font=("Segoe UI", 9), bg=p["card"], fg=p["muted"], anchor="w", justify="left", wraplength=720)
        self.lbl_sync_detail.pack(fill="x", pady=(2, 0))
        self._banner_inner = banner_top

        # 2. USB-Stick & Geschaefts-PC (.exe Update)
        usb_card = tk.Frame(root, bg=p["usb_bg"], highlightthickness=1, highlightbackground=p["usb_border"], padx=12, pady=10)
        usb_card.pack(fill="x", pady=(0, 9))
        usb_hdr = tk.Frame(usb_card, bg=p["usb_bg"])
        usb_hdr.pack(fill="x", pady=(0, 7))
        tk.Label(usb_hdr, text="💾 USB-Stick & Geschäfts-PC (.exe Update) – ohne Installation", font=("Segoe UI", 10, "bold"), bg=p["usb_bg"], fg=p["usb_fg"]).pack(side="left")
        pill_bg = "#065f46" if self._dark else "#d1fae5"
        pill_fg = "#a7f3d0" if self._dark else "#065f46"
        tk.Label(usb_hdr, text="Kein Python • Kein Git nötig", font=("Segoe UI", 8, "bold"), bg=pill_bg, fg=pill_fg, padx=8, pady=2).pack(side="right")

        steps_row = tk.Frame(usb_card, bg=p["usb_bg"])
        steps_row.pack(fill="x", pady=(0, 8))
        step_title_fg = "#34d399" if self._dark else "#047857"
        for idx, (st_head, st_body) in enumerate([
            ("1. Herunterladen", "Grünen Download-Knopf unten anklicken."),
            ("2. Ersetzen", "7-Ordner-Pro.exe auf USB-Stick überschreiben."),
            ("3. Fertig!", "Einstellungen (window_state.json) bleiben erhalten."),
        ]):
            sc = tk.Frame(steps_row, bg=p["step_bg"], highlightthickness=1, highlightbackground=p["step_border"], padx=9, pady=6)
            sc.pack(side="left", fill="both", expand=True, padx=(0 if idx == 0 else 6, 0))
            tk.Label(sc, text=st_head, font=("Segoe UI", 9, "bold"), bg=p["step_bg"], fg=step_title_fg, anchor="w").pack(anchor="w")
            tk.Label(sc, text=st_body, font=("Segoe UI", 8), bg=p["step_bg"], fg=p["muted"], anchor="w", justify="left", wraplength=195).pack(anchor="w")

        usb_btn_row = tk.Frame(usb_card, bg=p["usb_bg"])
        usb_btn_row.pack(fill="x")
        self.btn_exe_dl = ModernRoundedButton(
            usb_btn_row,
            text="⬇ 7-Ordner-Pro.exe laden",
            command=self._open_exe_download,
            bg="#059669",
            fg="#ffffff",
            hover_bg="#047857",
            border_color="#059669",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=7,
            min_width=355,
        )
        self.btn_exe_dl.pack(side="left", padx=(0, 8))
        self.btn_usb_deploy = ModernRoundedButton(
            usb_btn_row,
            text="💾 Auf USB-Stick ablegen",
            command=self._deploy_to_usb,
            bg="#0284c7",
            fg="#ffffff",
            hover_bg="#0369a1",
            border_color="#0284c7",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=7,
            min_width=355,
        )
        self.btn_usb_deploy.pack(side="right")

        # 3. Git Repo Status & URL
        repo_card = tk.Frame(root, bg=p["card"], highlightthickness=1, highlightbackground=p["border"], padx=12, pady=9)
        repo_card.pack(fill="x", pady=(0, 9))

        r1 = tk.Frame(repo_card, bg=p["card"])
        r1.pack(fill="x", pady=(0, 4))
        tk.Label(r1, text="App-Version (lokal):", font=("Segoe UI", 9), bg=p["card"], fg=p["muted"]).pack(side="left")
        self.lbl_local_ver = tk.Label(r1, text=APP_VERSION, font=("Consolas", 9, "bold"), bg=p["card"], fg="#10b981")
        self.lbl_local_ver.pack(side="left", padx=(8, 28))
        tk.Label(r1, text="Branch:", font=("Segoe UI", 9), bg=p["card"], fg=p["muted"]).pack(side="left")
        self.lbl_branch = tk.Label(r1, text="main", font=("Consolas", 9, "bold"), bg=p["card"], fg=p["fg"])
        self.lbl_branch.pack(side="left", padx=(8, 0))

        r2 = tk.Frame(repo_card, bg=p["card"])
        r2.pack(fill="x", pady=(0, 6))
        tk.Label(r2, text="Letzter Git-Commit:", font=("Segoe UI", 9), bg=p["card"], fg=p["muted"]).pack(side="left", anchor="n")
        self.lbl_commit = tk.Label(r2, text="-", font=("Consolas", 9, "bold"), bg=p["card"], fg=p["accent"], anchor="w", justify="left", wraplength=590)
        self.lbl_commit.pack(side="left", fill="x", expand=True, padx=(8, 0))

        tk.Frame(repo_card, bg=p["border"], height=1).pack(fill="x", pady=(2, 6))

        url_hdr = tk.Frame(repo_card, bg=p["card"])
        url_hdr.pack(fill="x", pady=(0, 5))
        tk.Label(url_hdr, text="GitHub Repository URL:", font=("Segoe UI", 9, "bold"), bg=p["card"], fg=p["muted"]).pack(side="left", pady=3)
        self.btn_new_repo = ModernRoundedButton(
            url_hdr,
            text="Auf GitHub anlegen ↗",
            command=self._open_new_repo,
            bg="#312e81" if self._dark else "#eef2ff",
            fg="#c7d2fe" if self._dark else "#3730a3",
            hover_bg="#3730a3" if self._dark else "#e0e7ff",
            border_color="#6366f1" if self._dark else "#818cf8",
            font=("Segoe UI", 8, "bold"),
            r=8,
            padx=10,
            pady=4,
            icon_kind="globe",
        )
        self.btn_new_repo.pack(side="right")

        url_row = tk.Frame(repo_card, bg=p["card"])
        url_row.pack(fill="x")
        self.var_remote_url = tk.StringVar(value=github_sync.DEFAULT_REMOTE_URL)
        ent_wrap = tk.Frame(url_row, bg=p["input_bg"], highlightthickness=1, highlightbackground=p["border"], padx=6, pady=4)
        ent_wrap.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.ent_remote = tk.Entry(ent_wrap, textvariable=self.var_remote_url, font=("Consolas", 9), bg=p["input_bg"], fg=p["fg"], insertbackground=p["fg"], relief="flat", bd=0)
        self.ent_remote.pack(fill="x")
        self.btn_save_remote = ModernRoundedButton(
            url_row,
            text="Speichern",
            command=self._save_remote,
            bg=p["btn_bg"],
            fg=p["btn_fg"],
            hover_bg=p["btn_hover"],
            border_color=p["border"],
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=5,
        )
        self.btn_save_remote.pack(side="right")

        # 4. Retour in den Versionen (mit Sicherheitsabfrage)
        rb_card = tk.Frame(root, bg=p["card"], highlightthickness=1, highlightbackground=p["border"], padx=12, pady=8)
        rb_card.pack(fill="x", pady=(0, 9))
        rb_hdr = tk.Frame(rb_card, bg=p["card"])
        rb_hdr.pack(fill="x", pady=(0, 5))
        tk.Label(rb_hdr, text="🕒 Frühere Version wiederherstellen (Retour)", font=("Segoe UI", 9, "bold"), bg=p["card"], fg=p["fg"]).pack(side="left")
        amber_fg = "#fbbf24" if self._dark else "#b45309"
        tk.Label(rb_hdr, text="mit Sicherheitsabfrage • window_state.json bleibt erhalten", font=("Segoe UI", 8), bg=p["card"], fg=amber_fg).pack(side="right")
        rb_row = tk.Frame(rb_card, bg=p["card"])
        rb_row.pack(fill="x")
        self.var_rollback = tk.StringVar()
        self.cmb_rollback = ttk.Combobox(rb_row, textvariable=self.var_rollback, state="readonly", font=("Consolas", 9))
        self.cmb_rollback.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._rollback_hashes = []
        self.btn_rollback = ModernRoundedButton(
            rb_row,
            text="↺ Zurücksetzen",
            command=self._run_rollback,
            bg="#78350f" if self._dark else "#fffbeb",
            fg="#fde68a" if self._dark else "#92400e",
            hover_bg="#92400e" if self._dark else "#fef3c7",
            border_color="#f59e0b",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=5,
        )
        self.btn_rollback.pack(side="right")

        # 6. Footer Action Buttons ZUERST am unteren Rand verankern (niemals abschneiden!)
        self._ftr_frame = tk.Frame(root, bg=p["bg"])
        ftr = self._ftr_frame
        ftr.pack(side="bottom", fill="x", pady=(10, 4))
        tk.Label(ftr, text="Ohne window_state.json", font=("Segoe UI", 8), bg=p["bg"], fg=p["muted"]).pack(side="left")

        btn_box = tk.Frame(ftr, bg=p["bg"])
        btn_box.pack(side="right")
        self.btn_close = ModernRoundedButton(
            btn_box,
            text="Schließen",
            command=self.destroy,
            bg=p["btn_bg"],
            fg=p["btn_fg"],
            hover_bg=p["btn_hover"],
            border_color=p["border"],
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=6,
        )
        self.btn_close.pack(side="left", padx=(0, 8))

        self.btn_push = ModernRoundedButton(
            btn_box,
            text="⬆ Stand nach GitHub hochladen",
            command=self._run_push,
            bg=p["btn_bg"],
            fg=p["btn_fg"],
            hover_bg=p["btn_hover"],
            border_color=p["border"],
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=6,
        )
        self.btn_push.pack(side="left", padx=(0, 8))

        self.btn_pull = ModernRoundedButton(
            btn_box,
            text="☁ Jetzt von GitHub aktualisieren",
            command=self._run_pull,
            bg="#4f46e5",
            fg="#ffffff",
            hover_bg="#4338ca",
            border_color="#4f46e5",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=6,
        )
        self.btn_pull.pack(side="left")

        # 5. Protokoll (erst sichtbar wenn Push/Pull/Rollback läuft)
        self.log_frame = tk.Frame(root, bg=p["bg"])
        self.txt_log = tk.Text(self.log_frame, height=5, font=("Consolas", 8), bg=p["log_bg"], fg=p["log_fg"], relief="flat", bd=0, padx=8, pady=6, wrap="word")
        self.txt_log.pack(fill="x")
        self.txt_log.configure(state="disabled")

    def _show_log(self, text: str):
        was_packed = bool(self.log_frame.winfo_manager())
        if not was_packed:
            self.log_frame.pack(side="bottom", fill="x", pady=(0, 8))
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.insert("1.0", text or "")
        self.txt_log.configure(state="disabled")
        if not was_packed:
            self._fit_height(center=False)

    def refresh_git_info(self, info=None):
        data = info or github_sync.get_git_info(APP_VERSION)
        self.git_info = data
        self.lbl_branch.configure(text=data.get("branch") or "main")
        self.lbl_commit.configure(text=data.get("last_commit") or "Noch kein Commit vorhanden")
        self.var_remote_url.set(data.get("remote_url") or github_sync.DEFAULT_REMOTE_URL)

        sz = data.get("exe_size_mb")
        dl_lbl = f"⬇ 7-Ordner-Pro.exe laden ({sz})" if sz else "⬇ 7-Ordner-Pro.exe laden"
        self.btn_exe_dl.update_style(text=dl_lbl)

        if data.get("has_remote_ref"):
            self.btn_new_repo.update_style(text="Auf GitHub öffnen ↗")
        else:
            self.btn_new_repo.update_style(text="Auf GitHub anlegen ↗")

        st = data.get("sync_state") or "no_git"
        if st in ("local_newer", "local_ahead"):
            b_bg = "#451a03" if self._dark else "#fffbeb"
            b_bd = "#f59e0b"
            t_fg = "#fde68a" if self._dark else "#92400e"
            d_fg = "#fcd34d" if self._dark else "#78350f"
            p_bg = "#78350f" if self._dark else "#fef3c7"
            badge_txt = f"Lokal {APP_VERSION} > GitHub {data.get('remote_version') or 'älter'}" if st == "local_newer" else "Ungesicherter Stand"
            self.btn_push.update_style(bg="#d97706", fg="#ffffff", hover_bg="#b45309", border_color="#d97706")
        elif st == "remote_newer":
            b_bg = "#0c4a6e" if self._dark else "#eff6ff"
            b_bd = "#3b82f6"
            t_fg = "#bae6fd" if self._dark else "#1e40af"
            d_fg = "#7dd3fc" if self._dark else "#1d4ed8"
            p_bg = "#0369a1" if self._dark else "#dbeafe"
            badge_txt = f"GitHub neuer ({data.get('remote_version') or '+' + str(data.get('behind', 1))})"
            self.btn_push.update_style(bg=self.p["btn_bg"], fg=self.p["btn_fg"], hover_bg=self.p["btn_hover"], border_color=self.p["border"])
        elif st == "in_sync":
            b_bg = "#064e3b" if self._dark else "#ecfdf5"
            b_bd = "#10b981"
            t_fg = "#a7f3d0" if self._dark else "#065f46"
            d_fg = "#6ee7b7" if self._dark else "#047857"
            p_bg = "#047857" if self._dark else "#d1fae5"
            badge_txt = f"Synchron ({APP_VERSION})"
            self.btn_push.update_style(bg=self.p["btn_bg"], fg=self.p["btn_fg"], hover_bg=self.p["btn_hover"], border_color=self.p["border"])
        elif st == "checking":
            b_bg, b_bd, t_fg, d_fg, p_bg = self.p["card"], self.p["border"], self.p["fg"], self.p["muted"], self.p["subcard"]
            badge_txt = "Prüfe..."
        elif st == "offline":
            b_bg, b_bd, t_fg, d_fg, p_bg = self.p["card"], self.p["border"], self.p["fg"], self.p["muted"], self.p["subcard"]
            badge_txt = "Offline"
        else:
            b_bg, b_bd, t_fg, d_fg, p_bg = self.p["card"], self.p["border"], self.p["fg"], self.p["muted"], self.p["subcard"]
            badge_txt = "Kein Git"

        self.sync_banner.configure(bg=b_bg, highlightbackground=b_bd)
        self._banner_inner.configure(bg=b_bg)
        self.lbl_sync_title.configure(text=data.get("sync_title") or "", bg=b_bg, fg=t_fg)
        self.lbl_sync_detail.configure(text=data.get("sync_detail") or "", bg=b_bg, fg=d_fg)
        self.lbl_sync_badge.configure(text=badge_txt, bg=p_bg, fg=t_fg)

        hist = data.get("history") or []
        opts = []
        self._rollback_hashes = []
        for idx, item in enumerate(hist):
            tag = "[Aktuell] " if idx == 0 else ""
            opts.append(f"{tag}{item['short']} — {item['subject']} ({item['date']})")
            self._rollback_hashes.append(item["hash"])
        self.cmb_rollback["values"] = opts
        if opts:
            self.cmb_rollback.current(1 if len(opts) > 1 else 0)
        else:
            self.var_rollback.set("Keine Commit-Historie verfügbar")
        self._fit_height(center=False)

    def _open_exe_download(self):
        url = (self.git_info.get("raw_exe_url") or "").strip()
        if url:
            webbrowser.open(url)

    def _deploy_to_usb(self):
        drives = github_sync.list_usb_drives()
        target_dir = ""
        if drives:
            d0 = drives[0]
            default_dir = d0["default_dir"]
            if len(drives) == 1:
                ok = self.app.confirm_action(
                    "7-Ordner-Pro.exe auf USB-Stick ablegen",
                    f"Erkannter USB-Stick: {d0['display']}",
                    f"Zielordner: {default_dir}\\7-Ordner-Pro.exe\n"
                    f"Der Ordner wird automatisch erstellt (falls noch nicht vorhanden). "
                    f"Vorhandene Einstellungen (window_state.json) bleiben erhalten.",
                    confirm_text="💾 Auf USB-Stick speichern",
                    cancel_text="Anderen Ordner wählen...",
                )
                if ok:
                    target_dir = default_dir
                else:
                    chosen = filedialog.askdirectory(parent=self, initialdir=d0["drive"], title="Zielordner für 7-Ordner-Pro.exe wählen")
                    if not chosen:
                        return
                    target_dir = chosen
            else:
                chosen = filedialog.askdirectory(parent=self, initialdir=d0["drive"], title="USB-Stick / Zielordner wählen (Ordner-Struktur wird erstellt)")
                if not chosen:
                    return
                target_dir = chosen
        else:
            chosen = filedialog.askdirectory(parent=self, title="Kein USB-Stick erkannt – Zielordner für 7-Ordner-Pro.exe wählen")
            if not chosen:
                return
            target_dir = chosen

        res = github_sync.deploy_exe_to_usb(target_dir)
        self._show_log(res.get("message") or res.get("error") or "")
        if res.get("success"):
            self.app.show_notice("USB-Stick bereit", res.get("message") or "EXE gespeichert!", kind="success", auto_close_ms=3500)
        else:
            self.app.show_notice("USB-Speichern fehlgeschlagen", res.get("error") or "Konnte EXE nicht ablegen.", kind="warning")

    def _open_new_repo(self):
        if self.git_info.get("has_remote_ref"):
            remote_url = (self.git_info.get("remote_url") or github_sync.DEFAULT_REMOTE_URL).strip()
            if remote_url.endswith(".git"):
                remote_url = remote_url[:-4]
            if remote_url:
                webbrowser.open(remote_url)
                return
        url = (self.git_info.get("new_repo_url") or f"https://github.com/new?name={github_sync.DEFAULT_REPO_NAME}").strip()
        webbrowser.open(url)

    def _save_remote(self):
        res = github_sync.git_set_remote(self.var_remote_url.get(), APP_VERSION)
        if res.get("success"):
            self.refresh_git_info(res.get("git_info"))
            self.app.show_notice("Gespeichert", "GitHub Repository URL gespeichert.", auto_close_ms=3000, kind="success")
        else:
            self.app.show_notice("Fehler", "Remote konnte nicht gespeichert werden.", res.get("error") or "", kind="error")

    def _run_push(self):
        if self._busy:
            return
        self._busy = True
        self.btn_push.update_style(text="⏳ Lade nach GitHub hoch...")
        self._show_log("Erstelle Commit (ohne window_state.json) und übertrage nach GitHub...")

        def _worker():
            res = github_sync.git_push_upload(APP_VERSION)
            def _done():
                global APP_VERSION
                self._busy = False
                self.btn_push.update_style(text="⬆ Stand nach GitHub hochladen")
                self._show_log(res.get("output") or res.get("message") or "")
                nv = res.get("new_version")
                if nv:
                    APP_VERSION = nv
                    self.title(f"GitHub Versions-Aktualisierung • {APP_VERSION}")
                    if hasattr(self, "lbl_dialog_ver"):
                        self.lbl_dialog_ver.configure(text=f" {APP_VERSION} ")
                    if hasattr(self, "lbl_local_ver"):
                        self.lbl_local_ver.configure(text=APP_VERSION)
                    try:
                        self.app.title(f"7-Ordner-System Organizer Pro  {APP_VERSION}")
                    except Exception:
                        pass
                if res.get("git_info"):
                    self.refresh_git_info(res["git_info"])
                self.app.refresh_git_badge()
                if res.get("success"):
                    self.app.show_notice("GitHub Upload", "Stand erfolgreich auf GitHub gesichert!", res.get("message") or "", kind="success", auto_close_ms=3000)
                else:
                    self.app.show_notice("Upload-Hinweis", "Hochladen nicht abgeschlossen.", res.get("message") or res.get("error") or "", kind="warning")
            self.after(0, _done)

        threading.Thread(target=_worker, daemon=True).start()

    def _run_pull(self):
        if self._busy:
            return
        if self.git_info.get("local_is_newer"):
            ok = self.app.confirm_action(
                "Achtung: Lokale Version ist neuer!",
                self.git_info.get("sync_title") or f"Lokale Version ({APP_VERSION}) ist neuer als GitHub.",
                "Wenn Sie jetzt von GitHub aktualisieren, statt hochzuladen, laden Sie eventuell einen älteren Stand. Wirklich von GitHub aktualisieren?",
                confirm_text="Trotzdem von GitHub laden",
                cancel_text="Abbrechen",
            )
            if not ok:
                return

        self._busy = True
        self.btn_pull.update_style(text="⏳ Aktualisiere...")
        self._show_log("Verbinde mit GitHub und führe git pull aus...")

        def _worker():
            res = github_sync.git_pull_update(APP_VERSION)
            def _done():
                self._busy = False
                self.btn_pull.update_style(text="☁ Jetzt von GitHub aktualisieren")
                self._show_log(res.get("output") or res.get("message") or "")
                if res.get("git_info"):
                    self.refresh_git_info(res["git_info"])
                self.app.refresh_git_badge()
                if res.get("success"):
                    out = res.get("output") or ""
                    if res.get("already_up_to_date") or "Already up to date" in out or "Already up-to-date" in out:
                        self.app.show_notice("GitHub Update", res.get("message") or "Bereits aktuell.", kind="info", auto_close_ms=3000)
                    elif res.get("self_updating"):
                        self.app.show_notice("Update wird installiert", res.get("message") or "Neue .exe geladen – App startet jetzt neu.", kind="success", auto_close_ms=2500)
                        self.after(600, lambda: self.app._restart_after_git_update(spawn_new=False))
                    else:
                        self.app.show_notice("Update eingespielt", "Neue Version geladen – App startet jetzt neu.", kind="success", auto_close_ms=2000)
                        self.after(500, self.app._restart_after_git_update)
                else:
                    self.app.show_notice("Update fehlgeschlagen", "GitHub-Update konnte nicht geladen werden.", res.get("message") or res.get("error") or "", kind="warning")
            self.after(0, _done)

        threading.Thread(target=_worker, daemon=True).start()

    def _run_rollback(self):
        if self._busy:
            return
        idx = self.cmb_rollback.current()
        if idx < 0 or idx >= len(self._rollback_hashes):
            self.app.show_notice("Hinweis", "Bitte zuerst eine frühere Version aus der Liste wählen.", kind="info")
            return
        target_hash = self._rollback_hashes[idx]
        target_label = self.var_rollback.get()

        ok = self.app.confirm_action(
            "Zurück zu einer früheren Version? (Retour)",
            f"Wirklich auf diesen Commit zurücksetzen?\n\n{target_label}",
            "Sicherheitsnetz: Lokale Änderungen werden vorher im Git-Stash gesichert, Ihre window_state.json bleibt erhalten, und mit „Jetzt von GitHub aktualisieren“ können Sie jederzeit wieder vorwärts wechseln.",
            confirm_text="Ja, Version wiederherstellen",
            cancel_text="Abbrechen",
        )
        if not ok:
            return

        self._busy = True
        self.btn_rollback.update_style(text="⏳ Setze zurück...")
        self._show_log(f"Setze sicher auf Commit {target_hash[:7]} zurück...")

        def _worker():
            res = github_sync.git_rollback_version(target_hash, APP_VERSION)
            def _done():
                self._busy = False
                self.btn_rollback.update_style(text="↺ Zurücksetzen")
                self._show_log(res.get("output") or res.get("message") or "")
                if res.get("git_info"):
                    self.refresh_git_info(res["git_info"])
                if res.get("success"):
                    self.app.show_notice("Version wiederhergestellt", res.get("message") or "App startet neu.", kind="success", auto_close_ms=2000)
                    self.after(500, self.app._restart_after_git_update)
                else:
                    self.app.show_notice("Fehler", "Zurücksetzen fehlgeschlagen.", res.get("error") or res.get("message") or "", kind="error")
            self.after(0, _done)

        threading.Thread(target=_worker, daemon=True).start()


def get_hourglass_icon_photo(frame_idx: int = 0, bg: str = "#ffffff", size: int = 44):
    """Erzeugt 3x-supersampled animierte Sanduhr-Frames im Light-Fluent-Stil."""
    f = int(frame_idx) % 8
    key = ("hourglass_v1", f, bg, size)
    if key in IMG_CACHE:
        return IMG_CACHE[key]

    s = size * 3
    u = s / 18.0
    base = Image.new("RGBA", (s, s), bg)
    bd = ImageDraw.Draw(base)

    # Weicher blauer Kreis-Badge
    pad = max(2, round(0.8 * u))
    bd.ellipse(
        [pad, pad, s - pad - 1, s - pad - 1],
        fill="#eff6ff",
        outline="#bfdbfe",
        width=max(2, round(0.85 * u)),
    )

    # Sanduhr auf transparentem Overlay zeichnen (fuer sanfte Drehung bei Frame 5..7)
    hg = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hg)

    top_sand_levels = [0.85, 0.62, 0.40, 0.18, 0.0, 0.0, 0.0, 0.0]
    bot_sand_levels = [0.05, 0.28, 0.52, 0.74, 0.90, 0.90, 0.90, 0.90]
    frac_t = top_sand_levels[f]
    frac_b = bot_sand_levels[f]

    # Oberer Sand (verengt sich nach unten zur Mitte y=8.7)
    if frac_t > 0.02:
        y_surf = 8.7 - (8.7 - 4.4) * frac_t
        half_w = max(0.7, (8.7 - y_surf) * (3.1 / 4.3))
        hd.polygon(
            [((9.0 - half_w) * u, y_surf * u), ((9.0 + half_w) * u, y_surf * u), (9.0 * u, 8.7 * u)],
            fill="#60a5fa",
        )

    # Rieseln durch die Engstelle bei aktiven Rieselframes
    if f in (1, 2, 3):
        stream_w = max(2, round(0.8 * u))
        hd.line([(9.0 * u, 9.0 * u), (9.0 * u, 11.7 * u)], fill="#2563eb", width=stream_w)
        hd.ellipse(
            [8.4 * u, 11.9 * u, 9.6 * u, 13.0 * u],
            fill="#3b82f6",
        )

    # Unterer Sand-Haufen
    if frac_b > 0.02:
        y_bot = 13.7
        peak_y = y_bot - 4.0 * frac_b
        half_w = max(1.0, 3.3 * (0.45 + 0.55 * frac_b))
        hd.polygon(
            [(9.0 * u, peak_y * u), ((9.0 - half_w) * u, y_bot * u), ((9.0 + half_w) * u, y_bot * u)],
            fill="#2563eb",
        )

    # Glas-Umriss (links & rechts X-Linien + obere/untere Kappen)
    stroke_w = max(3, round(1.15 * u))
    cap_w = max(4, round(1.45 * u))
    frame_col = "#1e40af"
    cap_col = "#2563eb"

    # Oberes & unteres Dreieck als feines Sanduhr-Glas
    hd.line([(5.6 * u, 4.1 * u), (9.0 * u, 9.0 * u), (12.4 * u, 4.1 * u)], fill=frame_col, width=stroke_w, joint="curve")
    hd.line([(5.6 * u, 13.9 * u), (9.0 * u, 9.0 * u), (12.4 * u, 13.9 * u)], fill=frame_col, width=stroke_w, joint="curve")
    # Kappen oben und unten
    hd.line([(4.9 * u, 4.0 * u), (13.1 * u, 4.0 * u)], fill=cap_col, width=cap_w)
    hd.line([(4.9 * u, 14.0 * u), (13.1 * u, 14.0 * u)], fill=cap_col, width=cap_w)

    # Sanftes Wenden in den letzten 3 Frames (45, 90, 135 Grad)
    if f >= 5:
        rot_angle = -(f - 4) * 45
        hg = hg.rotate(rot_angle, resample=Image.Resampling.BICUBIC)

    combined = Image.alpha_composite(base, hg).resize((size, size), Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(combined)
    IMG_CACHE[key] = photo
    return photo


class SearchBusyOverlay(tk.Frame):
    """Zentrierte Light-Fluent Such-Karte mit animierter Sanduhr und Laufzeit-Status."""

    def __init__(self, parent, on_cancel=None):
        super().__init__(
            parent,
            bg="#ffffff",
            highlightbackground="#cbd5e1",
            highlightthickness=1,
            padx=24,
            pady=18,
            cursor="wait",
        )
        self._frame_idx = 0
        self._bar_pos = 0.0
        self._on_cancel = on_cancel

        top_row = tk.Frame(self, bg="#ffffff", cursor="wait")
        top_row.pack(fill="x")

        ico = get_hourglass_icon_photo(0, bg="#ffffff", size=44)
        self.lbl_icon = tk.Label(top_row, image=ico, bg="#ffffff", bd=0, cursor="wait")
        self.lbl_icon.image = ico
        self.lbl_icon.pack(side="left", padx=(0, 14))

        txt_col = tk.Frame(top_row, bg="#ffffff", cursor="wait")
        txt_col.pack(side="left", fill="both", expand=True)

        self.lbl_title = tk.Label(
            txt_col,
            text="Ordner werden durchsucht…",
            font=("Segoe UI", 11, "bold"),
            bg="#ffffff",
            fg="#0f172a",
            anchor="w",
        )
        self.lbl_title.pack(fill="x", anchor="w")

        self.lbl_detail = tk.Label(
            txt_col,
            text="Suche läuft, bitte einen Moment Geduld.",
            font=("Segoe UI", 9),
            bg="#ffffff",
            fg="#475569",
            anchor="w",
            width=42,
        )
        self.lbl_detail.pack(fill="x", anchor="w", pady=(2, 0))

        self.bar_w = 340
        self.bar_h = 6
        self.progress_canvas = tk.Canvas(
            self,
            width=self.bar_w,
            height=self.bar_h,
            bg="#ffffff",
            highlightthickness=0,
            bd=0,
            cursor="wait",
        )
        self.progress_canvas.pack(fill="x", pady=(14, 10))
        self.progress_canvas.create_rectangle(0, 0, self.bar_w, self.bar_h, fill="#e2e8f0", outline="")
        self._bar_fill = self.progress_canvas.create_rectangle(0, 0, 90, self.bar_h, fill="#2563eb", outline="")

        foot = tk.Frame(self, bg="#ffffff")
        foot.pack(fill="x")

        self.lbl_hint = tk.Label(
            foot,
            text="Die Oberfläche bleibt aktiv – Sie können jederzeit abbrechen.",
            font=("Segoe UI", 8),
            bg="#ffffff",
            fg="#64748b",
            anchor="w",
        )
        self.lbl_hint.pack(side="left")

        self.btn_cancel = ModernRoundedButton(
            foot,
            text="Abbrechen",
            command=self._handle_cancel,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=4,
        )
        self.btn_cancel.pack(side="right", padx=(12, 0))

    def _handle_cancel(self):
        try:
            self.lbl_title.config(text="Suche wird abgebrochen…")
        except Exception:
            pass
        if callable(self._on_cancel):
            self._on_cancel()

    def step_animation(self, phase: str, found_count: int = 0, done_count: int = 0, total_count: int = 0):
        self._frame_idx = (self._frame_idx + 1) % 8
        ico = get_hourglass_icon_photo(self._frame_idx, bg="#ffffff", size=44)
        self.lbl_icon.configure(image=ico)
        self.lbl_icon.image = ico

        if phase == "analyze" and total_count > 0:
            self.lbl_title.config(text="Dateien & Duplikate werden geprüft…")
            self.lbl_detail.config(text=f"Analysiert: {done_count} von {total_count} gefundenen Elementen")
            ratio = max(0.05, min(1.0, done_count / float(total_count)))
            self.progress_canvas.coords(self._bar_fill, 0, 0, int(self.bar_w * ratio), self.bar_h)
        else:
            self.lbl_title.config(text="Ordner werden durchsucht…")
            if found_count == 1:
                self.lbl_detail.config(text="Bisher 1 passendes Element gefunden…")
            elif found_count > 1:
                self.lbl_detail.config(text=f"Bisher {found_count} passende Elemente gefunden…")
            else:
                self.lbl_detail.config(text="Durchsuche ausgewählten Quellordner…")
            self._bar_pos = (self._bar_pos + 16) % (self.bar_w + 110)
            x0 = max(0, int(self._bar_pos - 110))
            x1 = min(self.bar_w, int(self._bar_pos))
            if x1 <= x0:
                x0, x1 = 0, 90
            self.progress_canvas.coords(self._bar_fill, x0, 0, x1, self.bar_h)


def _copy_file_chunked(src: Path, dest: Path, cancel_ev: threading.Event | None = None,
                        byte_cb=None, chunk_size: int = 512 * 1024) -> bool:
    """Kopiert eine Datei in 512-KB-Blöcken mit stetigem Flush & Fsync für USB-Sticks. Bricht bei cancel_ev SOFORT ab."""
    try:
        fsize = src.stat().st_size
    except Exception:
        fsize = 0

    dest_tmp = dest.with_name(dest.name + f".part_{os.getpid()}")
    copied = 0
    chunk_counter = 0
    try:
        with open(src, "rb") as fin, open(dest_tmp, "wb") as fout:
            while True:
                if cancel_ev and cancel_ev.is_set():
                    try:
                        dest_tmp.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return False
                buf = fin.read(chunk_size)
                if not buf:
                    break
                fout.write(buf)
                n = len(buf)
                copied += n
                chunk_counter += 1
                if chunk_counter % 8 == 0:  # Alle 4 MB leeren, damit langsamer USB-Puffer nicht überläuft
                    fout.flush()
                if byte_cb:
                    try:
                        byte_cb(n, copied, fsize)
                    except Exception:
                        pass

            fout.flush()
            try:
                os.fsync(fout.fileno())
            except Exception:
                pass

        if cancel_ev and cancel_ev.is_set():
            try:
                dest_tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return False

        if dest.exists():
            try:
                if dest.is_dir():
                    shutil.rmtree(str(dest), ignore_errors=True)
                else:
                    dest.unlink(missing_ok=True)
            except Exception:
                pass

        # Auf USB-Sticks kurz warten und bis zu 5x erneut versuchen, falls Windows noch verzögert schreibt
        renamed = False
        for attempt in range(5):
            try:
                dest_tmp.rename(dest)
                renamed = True
                break
            except OSError:
                time.sleep(0.08 * (attempt + 1))
        if not renamed:
            shutil.move(str(dest_tmp), str(dest))

        try:
            shutil.copystat(str(src), str(dest))
        except Exception:
            pass
        return True
    except Exception as exc:
        try:
            dest_tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise exc


def _copy_directory_worker(src_dir: Path, dst_dir: Path, cancel_ev: threading.Event | None = None,
                           file_start_cb=None, byte_cb=None) -> bool:
    """Kopiert einen ganzen Ordner dateiweise im 1-MB-Takt mit sofortigem Abbruch & Byte-Fortschritt."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src_dir):
        if cancel_ev and cancel_ev.is_set():
            return False
        dirs[:] = [d for d in dirs if d.lower() not in IGNORED_DIRS and not d.startswith(".")]
        rel = Path(root).relative_to(src_dir)
        cur_dst = dst_dir / rel
        cur_dst.mkdir(parents=True, exist_ok=True)
        for fname in files:
            if cancel_ev and cancel_ev.is_set():
                return False
            if fname.startswith("~$"):
                continue
            s_file = Path(root) / fname
            d_file = cur_dst / fname
            if file_start_cb:
                try:
                    file_start_cb(fname, s_file.stat().st_size)
                except Exception:
                    pass
            ok = _copy_file_chunked(s_file, d_file, cancel_ev=cancel_ev, byte_cb=byte_cb)
            if not ok:
                return False
    try:
        shutil.copystat(str(src_dir), str(dst_dir))
    except Exception:
        pass
    return True


def _move_directory_worker(src_dir: Path, dst_dir: Path, cancel_ev: threading.Event | None = None,
                           file_start_cb=None, byte_cb=None) -> bool:
    """Verschiebt einen Ordner. Auf demselben Laufwerk atomar, laufwerksübergreifend dateiweise mit Chunk-Fortschritt."""
    try:
        os.rename(str(src_dir), str(dst_dir))
        return True
    except OSError:
        pass
    ok = _copy_directory_worker(src_dir, dst_dir, cancel_ev=cancel_ev, file_start_cb=file_start_cb, byte_cb=byte_cb)
    if ok and (cancel_ev is None or not cancel_ev.is_set()):
        try:
            shutil.rmtree(str(src_dir))
            return True
        except Exception:
            return True
    return False


class TransferBusyOverlay(tk.Frame):
    """Zentrierte Light-Fluent Transfer-Karte mit stetigem Live-Fortschritt, Geschwindigkeitsanzeige & sofortigem Abbruch."""

    def __init__(self, parent, is_copy: bool = True, total_items: int = 1, on_cancel=None):
        super().__init__(
            parent,
            bg="#ffffff",
            highlightbackground="#cbd5e1",
            highlightthickness=1,
            padx=24,
            pady=18,
            cursor="wait",
        )
        self.is_copy = is_copy
        self.total_items = max(1, total_items)
        self._on_cancel = on_cancel
        self._frame_idx = 0

        action_word = "kopiert" if is_copy else "verschoben"
        top_row = tk.Frame(self, bg="#ffffff", cursor="wait")
        top_row.pack(fill="x")

        ico = get_hourglass_icon_photo(0, bg="#ffffff", size=44)
        self.lbl_icon = tk.Label(top_row, image=ico, bg="#ffffff", bd=0, cursor="wait")
        self.lbl_icon.image = ico
        self.lbl_icon.pack(side="left", padx=(0, 14))

        txt_col = tk.Frame(top_row, bg="#ffffff", cursor="wait")
        txt_col.pack(side="left", fill="both", expand=True)

        self.lbl_title = tk.Label(
            txt_col,
            text=f"Elemente werden {action_word}…",
            font=("Segoe UI", 11, "bold"),
            bg="#ffffff",
            fg="#0f172a",
            anchor="w",
        )
        self.lbl_title.pack(fill="x", anchor="w")

        self.lbl_detail = tk.Label(
            txt_col,
            text="Vorbereitung läuft…",
            font=("Segoe UI", 9),
            bg="#ffffff",
            fg="#475569",
            anchor="w",
            width=74,
        )
        self.lbl_detail.pack(fill="x", anchor="w", pady=(2, 0))

        self.lbl_subfile = tk.Label(
            txt_col,
            text="",
            font=("Segoe UI", 8),
            bg="#ffffff",
            fg="#64748b",
            anchor="w",
            width=74,
        )
        self.lbl_subfile.pack(fill="x", anchor="w", pady=(1, 0))

        self.bar_w = 560
        self.bar_h = 8
        self.progress_canvas = tk.Canvas(
            self,
            width=self.bar_w,
            height=self.bar_h,
            bg="#ffffff",
            highlightthickness=0,
            bd=0,
            cursor="wait",
        )
        self.progress_canvas.pack(fill="x", pady=(14, 10))
        self.progress_canvas.create_rectangle(0, 0, self.bar_w, self.bar_h, fill="#e2e8f0", outline="")
        self._bar_fill = self.progress_canvas.create_rectangle(0, 0, 0, self.bar_h, fill="#2563eb", outline="")

        foot = tk.Frame(self, bg="#ffffff")
        foot.pack(fill="x")

        self.lbl_pct = tk.Label(
            foot,
            text="0 % • Die Oberfläche bleibt aktiv",
            font=("Segoe UI", 8),
            bg="#ffffff",
            fg="#64748b",
            anchor="w",
        )
        self.lbl_pct.pack(side="left")

        self.btn_cancel = ModernRoundedButton(
            foot,
            text="Abbrechen",
            command=self._handle_cancel,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=4,
        )
        self.btn_cancel.pack(side="right", padx=(12, 0))

    def _handle_cancel(self):
        try:
            self.lbl_title.config(text="Vorgang wird sofort abgebrochen…")
            self.lbl_detail.config(text="Breche Kopiervorgang ab und räume unvollständige Dateien auf…")
            self.btn_cancel.config(state="disabled")
        except Exception:
            pass
        if callable(self._on_cancel):
            self._on_cancel()

    def update_progress(self, item_idx: int, total_items: int,
                        current_item: str = "", current_subfile: str = "",
                        copied_bytes: int = 0, total_bytes: int = 0,
                        file_copied: int = 0, file_total: int = 0,
                        speed_mb_s: float = 0.0):
        self._frame_idx = (self._frame_idx + 1) % 8
        ico = get_hourglass_icon_photo(self._frame_idx, bg="#ffffff", size=44)
        self.lbl_icon.configure(image=ico)
        self.lbl_icon.image = ico

        total_items = max(1, total_items)
        item_num = min(item_idx + 1, total_items)
        action_verb = "Kopiere" if self.is_copy else "Verschiebe"

        if current_item:
            disp_item = (current_item[:48] + "…") if len(current_item) > 50 else current_item
            self.lbl_detail.config(text=f"{action_verb} {item_num} von {total_items}: {disp_item}")
        else:
            self.lbl_detail.config(text=f"{action_verb} {item_num} von {total_items} Elementen…")

        if current_subfile:
            disp_sub = (current_subfile[:34] + "…") if len(current_subfile) > 36 else current_subfile
            f_cur = format_file_size(file_copied)
            f_tot = format_file_size(file_total) if file_total > 0 else ""
            sub_extra = f" ({f_cur} / {f_tot})" if f_tot else (f" ({f_cur})" if file_copied > 0 else "")
            spd_txt = f" • {speed_mb_s:.1f} MB/s" if speed_mb_s >= 0.1 else ""
            self.lbl_subfile.config(text=f"↳ Datei: {disp_sub}{sub_extra}{spd_txt}")
        else:
            self.lbl_subfile.config(text="")

        if total_bytes > 0:
            pct = max(0, min(100, int((copied_bytes / float(total_bytes)) * 100)))
            fill_w = max(0, min(self.bar_w, int(self.bar_w * (copied_bytes / float(total_bytes)))))
            cur_str = format_file_size(copied_bytes)
            all_str = format_file_size(total_bytes)
            spd_info = f" • {speed_mb_s:.1f} MB/s" if speed_mb_s >= 0.1 else ""
            self.lbl_pct.config(text=f"{pct} % ({cur_str} von {all_str}){spd_info} • Die Oberfläche bleibt aktiv")
        elif file_total > 0:
            pct = max(0, min(100, int((file_copied / float(file_total)) * 100)))
            fill_w = max(0, min(self.bar_w, int(self.bar_w * (file_copied / float(file_total)))))
            cur_str = format_file_size(file_copied)
            all_str = format_file_size(file_total)
            spd_info = f" • {speed_mb_s:.1f} MB/s" if speed_mb_s >= 0.1 else ""
            self.lbl_pct.config(text=f"{pct} % ({cur_str} von {all_str}){spd_info} • Die Oberfläche bleibt aktiv")
        else:
            pct = int((item_idx / float(total_items)) * 100)
            fill_w = int(self.bar_w * (item_idx / float(total_items)))
            self.lbl_pct.config(text=f"{pct} % abgeschlossen • Die Oberfläche bleibt aktiv")

        self.progress_canvas.coords(self._bar_fill, 0, 0, fill_w, self.bar_h)


FLUENT_MENU_IMG_CACHE = {}

def get_fluent_menu_icon_photo(kind, size=16, is_active=False, is_danger=False):
    """Erzeugt ein gestochen scharfes 16x16 Vektor-Icon mit 3x Supersampling für Fluent-Menüs."""
    key = (kind, size, is_active, is_danger)
    if key in FLUENT_MENU_IMG_CACHE:
        return FLUENT_MENU_IMG_CACHE[key]
    scale = 3
    s = size * scale
    u = s / 16.0
    lw = max(2, round(1.3 * u))
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def px(x, y):
        return (round(x * u), round(y * u))

    k = (kind or "").strip()
    norm_map = {
        "👁": "preview", "📂": "folder", "✏️": "rename", "✏": "rename",
        "⚡": "zap", "▶": "play", "📄": "doc", "⛔": "skip", "🗑": "trash",
        "📋": "copy", "🔍": "compare", "⇄": "swap", "✂️": "scissors",
        "✂": "scissors", "✓": "check", "✕": "cross", "↺": "refresh",
        "⬅": "arrow_left", "➔": "arrow_right", "⏮": "arrow_first",
        "⏭": "arrow_last", "📌": "pin", "🕒": "clock", "ℹ": "info",
        "🔤": "text", "📏": "ruler", "📖": "book", "📚": "books"
    }
    k = norm_map.get(k, k)

    if k in ("preview", "eye"):
        pts = [px(1.5, 8.0), px(4.5, 4.0), px(8.0, 3.2), px(11.5, 4.0), px(14.5, 8.0),
               px(11.5, 12.0), px(8.0, 12.8), px(4.5, 12.0)]
        d.polygon(pts, fill="#eff6ff", outline="#2563eb", width=lw)
        d.ellipse([px(5.5, 5.5), px(10.5, 10.5)], fill="#2563eb")
        d.ellipse([px(8.3, 6.7), px(9.7, 8.1)], fill="#ffffff")

    elif k in ("folder", "folder_target"):
        d.polygon([px(1.5, 4.5), px(1.5, 2.5), px(6.0, 2.5), px(7.5, 4.5)], fill="#d97706")
        d.rounded_rectangle([px(1.5, 4.2), px(14.5, 13.5)], radius=max(2, round(1.2 * u)), fill="#fef3c7", outline="#d97706", width=lw)

    elif k in ("rename", "pencil", "edit"):
        d.polygon([px(3.5, 12.5), px(4.0, 10.0), px(11.5, 2.5), px(13.5, 4.5), px(6.0, 12.0)], fill="#0d9488", outline="#0f766e", width=lw)
        d.polygon([px(2.0, 14.0), px(4.0, 10.0), px(6.0, 12.0)], fill="#fde047", outline="#0f766e", width=lw)
        d.polygon([px(2.0, 14.0), px(2.8, 12.5), px(3.5, 13.2)], fill="#0f172a")
        d.polygon([px(11.5, 2.5), px(13.5, 4.5), px(14.5, 3.5), px(12.5, 1.5)], fill="#fda4af", outline="#e11d48", width=max(1, round(0.8 * u)))

    elif k in ("zap", "flash", "bolt"):
        pts = [px(9.0, 1.2), px(4.2, 8.2), px(8.2, 8.2), px(7.0, 14.8), px(12.8, 6.8), px(8.8, 6.8)]
        d.polygon(pts, fill="#f59e0b", outline="#d97706", width=lw)

    elif k in ("play", "execute"):
        pts = [px(4.0, 2.5), px(13.5, 8.0), px(4.0, 13.5)]
        d.polygon(pts, fill="#16a34a", outline="#15803d", width=lw)

    elif k in ("doc", "app", "file"):
        d.polygon([px(3.0, 1.5), px(10.0, 1.5), px(13.0, 4.5), px(13.0, 14.5), px(3.0, 14.5)], fill="#eff6ff", outline="#0284c7", width=lw)
        d.polygon([px(10.0, 1.5), px(13.0, 4.5), px(10.0, 4.5)], fill="#bae6fd", outline="#0284c7", width=lw)
        d.line([px(5.2, 7.5), px(10.8, 7.5)], fill="#334155", width=max(1, round(1.1 * u)))
        d.line([px(5.2, 10.0), px(10.8, 10.0)], fill="#334155", width=max(1, round(1.1 * u)))

    elif k in ("explorer", "window"):
        d.rounded_rectangle([px(1.5, 2.5), px(14.5, 13.5)], radius=max(2, round(1.2 * u)), fill="#f8fafc", outline="#475569", width=lw)
        d.line([px(1.5, 5.5), px(14.5, 5.5)], fill="#475569", width=lw)
        d.line([px(6.0, 5.5), px(6.0, 13.5)], fill="#475569", width=max(1, round(1.0 * u)))

    elif k in ("skip", "minus", "pause"):
        d.ellipse([px(2.0, 2.0), px(14.0, 14.0)], fill="#fff7ed", outline="#ea580c", width=lw)
        d.line([px(5.0, 8.0), px(11.0, 8.0)], fill="#ea580c", width=max(2, round(1.8 * u)))

    elif k in ("trash", "delete"):
        d.line([px(2.5, 4.0), px(13.5, 4.0)], fill="#dc2626", width=lw)
        d.line([px(6.0, 2.2), px(10.0, 2.2)], fill="#dc2626", width=lw)
        d.polygon([px(4.0, 4.5), px(12.0, 4.5), px(11.0, 14.0), px(5.0, 14.0)], fill="#fef2f2", outline="#dc2626", width=lw)
        d.line([px(6.5, 6.5), px(6.5, 11.5)], fill="#dc2626", width=max(1, round(1.0 * u)))
        d.line([px(9.5, 6.5), px(9.5, 11.5)], fill="#dc2626", width=max(1, round(1.0 * u)))

    elif k in ("copy", "clipboard"):
        d.rounded_rectangle([px(3.0, 3.0), px(13.0, 14.5)], radius=max(1, round(1.0 * u)), fill="#ecfdf5", outline="#059669", width=lw)
        d.rounded_rectangle([px(5.5, 1.5), px(10.5, 4.5)], radius=max(1, round(0.8 * u)), fill="#059669")
        d.line([px(5.2, 7.5), px(10.8, 7.5)], fill="#059669", width=max(1, round(1.1 * u)))
        d.line([px(5.2, 10.0), px(9.5, 10.0)], fill="#059669", width=max(1, round(1.1 * u)))

    elif k in ("compare", "search"):
        d.ellipse([px(2.5, 2.5), px(11.0, 11.0)], fill="#eff6ff", outline="#2563eb", width=lw)
        d.line([px(9.5, 9.5), px(14.0, 14.0)], fill="#2563eb", width=max(2, round(2.0 * u)))

    elif k in ("swap", "arrows"):
        d.line([px(3.0, 5.5), px(12.5, 5.5)], fill="#475569", width=lw)
        d.polygon([px(10.5, 3.2), px(13.5, 5.5), px(10.5, 7.8)], fill="#475569")
        d.line([px(13.0, 10.5), px(3.5, 10.5)], fill="#475569", width=lw)
        d.polygon([px(5.5, 8.2), px(2.5, 10.5), px(5.5, 12.8)], fill="#475569")

    elif k in ("scissors", "cut"):
        d.ellipse([px(2.0, 10.0), px(6.5, 14.5)], outline="#7c3aed", width=lw)
        d.ellipse([px(9.5, 10.0), px(14.0, 14.5)], outline="#7c3aed", width=lw)
        d.line([px(5.5, 11.0), px(12.5, 2.5)], fill="#7c3aed", width=lw)
        d.line([px(10.5, 11.0), px(3.5, 2.5)], fill="#7c3aed", width=lw)

    elif k in ("check",):
        pts = [px(2.5, 8.5), px(6.5, 12.5), px(13.5, 3.5)]
        d.line(pts, fill="#2563eb", width=max(2, round(2.2 * u)), joint="curve")

    elif k in ("cross",):
        d.line([px(3.5, 3.5), px(12.5, 12.5)], fill="#64748b", width=lw)
        d.line([px(12.5, 3.5), px(3.5, 12.5)], fill="#64748b", width=lw)

    elif k in ("refresh", "undo"):
        d.arc([px(2.5, 2.5), px(13.5, 13.5)], start=40, end=320, fill="#475569", width=lw)
        d.polygon([px(11.0, 1.5), px(14.5, 4.5), px(10.0, 6.0)], fill="#475569")

    elif k in ("arrow_left",):
        d.line([px(13.0, 8.0), px(3.5, 8.0)], fill="#475569", width=lw)
        d.polygon([px(6.5, 4.5), px(2.5, 8.0), px(6.5, 11.5)], fill="#475569")

    elif k in ("arrow_right",):
        d.line([px(3.0, 8.0), px(12.5, 8.0)], fill="#475569", width=lw)
        d.polygon([px(9.5, 4.5), px(13.5, 8.0), px(9.5, 11.5)], fill="#475569")

    elif k in ("arrow_first",):
        d.line([px(3.0, 3.5), px(3.0, 12.5)], fill="#475569", width=lw)
        d.line([px(13.0, 8.0), px(5.5, 8.0)], fill="#475569", width=lw)
        d.polygon([px(8.5, 4.5), px(4.5, 8.0), px(8.5, 11.5)], fill="#475569")

    elif k in ("arrow_last",):
        d.line([px(13.0, 3.5), px(13.0, 12.5)], fill="#475569", width=lw)
        d.line([px(3.0, 8.0), px(10.5, 8.0)], fill="#475569", width=lw)
        d.polygon([px(7.5, 4.5), px(11.5, 8.0), px(7.5, 11.5)], fill="#475569")

    elif k in ("pin",):
        d.polygon([px(4.0, 4.0), px(12.0, 4.0), px(10.0, 8.5), px(6.0, 8.5)], fill="#f59e0b", outline="#ea580c", width=lw)
        d.line([px(8.0, 8.5), px(8.0, 14.5)], fill="#64748b", width=lw)

    elif k in ("clock",):
        d.ellipse([px(2.0, 2.0), px(14.0, 14.0)], fill="#f8fafc", outline="#64748b", width=lw)
        d.line([px(8.0, 8.0), px(8.0, 4.5)], fill="#475569", width=lw)
        d.line([px(8.0, 8.0), px(11.0, 8.0)], fill="#475569", width=lw)

    elif k in ("info",):
        d.ellipse([px(2.0, 2.0), px(14.0, 14.0)], fill="#eff6ff", outline="#0284c7", width=lw)
        d.ellipse([px(7.0, 4.0), px(9.0, 6.0)], fill="#0284c7")
        d.line([px(8.0, 7.5), px(8.0, 11.5)], fill="#0284c7", width=lw)

    elif k in ("text",):
        d.text((round(2 * u), round(1 * u)), "Aa", fill="#0284c7")

    elif k in ("ruler",):
        d.rounded_rectangle([px(2.0, 4.0), px(14.0, 12.0)], radius=max(1, round(0.8 * u)), fill="#fef3c7", outline="#d97706", width=lw)
        d.line([px(5.0, 4.0), px(5.0, 7.0)], fill="#d97706", width=max(1, round(1.0 * u)))
        d.line([px(8.0, 4.0), px(8.0, 8.5)], fill="#d97706", width=max(1, round(1.0 * u)))
        d.line([px(11.0, 4.0), px(11.0, 7.0)], fill="#d97706", width=max(1, round(1.0 * u)))

    elif k in ("book",):
        d.rounded_rectangle([px(2.5, 2.5), px(13.5, 13.5)], radius=round(1.2 * u), fill="#eff6ff", outline="#0284c7", width=lw)
        d.line([px(5.0, 2.5), px(5.0, 13.5)], fill="#0284c7", width=lw)
        d.line([px(7.0, 5.5), px(11.5, 5.5)], fill="#334155", width=max(1, round(1.0 * u)))
        d.line([px(7.0, 8.5), px(11.5, 8.5)], fill="#334155", width=max(1, round(1.0 * u)))

    elif k in ("books",):
        d.rounded_rectangle([px(2.0, 7.5), px(14.0, 13.5)], radius=round(1.0 * u), fill="#ecfdf5", outline="#059669", width=lw)
        d.rounded_rectangle([px(3.5, 2.5), px(12.5, 7.5)], radius=round(1.0 * u), fill="#eff6ff", outline="#0284c7", width=lw)

    elif k in ("pin_outline",):
        d.polygon([px(4.2, 4.0), px(11.8, 4.0), px(10.0, 8.4), px(6.0, 8.4)], fill="#ffffff", outline="#94a3b8", width=lw)
        d.line([px(8.0, 8.4), px(8.0, 14.2)], fill="#94a3b8", width=lw)

    elif k in ("plus",):
        c_pl = "#2563eb" if not is_danger else "#dc2626"
        d.ellipse([px(1.6, 1.6), px(14.4, 14.4)], fill="#eff6ff", outline="#93c5fd", width=max(1, round(1.1 * u)))
        d.line([px(8.0, 4.6), px(8.0, 11.4)], fill=c_pl, width=max(2, round(1.7 * u)))
        d.line([px(4.6, 8.0), px(11.4, 8.0)], fill=c_pl, width=max(2, round(1.7 * u)))

    elif k in ("usb", "drive"):
        d.rounded_rectangle([px(5.0, 1.4), px(11.0, 5.0)], radius=max(1, round(0.8 * u)), fill="#dbeafe", outline="#1d4ed8", width=max(1, round(1.0 * u)))
        d.rectangle([px(6.4, 2.4), px(7.4, 3.8)], fill="#2563eb")
        d.rectangle([px(8.6, 2.4), px(9.6, 3.8)], fill="#2563eb")
        d.rounded_rectangle([px(3.6, 4.8), px(12.4, 14.6)], radius=max(2, round(1.3 * u)), fill="#2563eb", outline="#1d4ed8", width=lw)
        d.ellipse([px(6.8, 8.5), px(9.2, 10.9)], fill="#ffffff")

    elif k in ("cross_danger",):
        d.line([px(3.8, 3.8), px(12.2, 12.2)], fill="#dc2626", width=lw)
        d.line([px(12.2, 3.8), px(3.8, 12.2)], fill="#dc2626", width=lw)

    im = im.resize((size, size), Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(im)
    FLUENT_MENU_IMG_CACHE[key] = photo
    return photo


class FluentPopupMenu(tk.Toplevel):
    """Modernes, helles Fluent-Kontextmenü mit gestochen scharfen 3x-Vektor-Icons und Windows 11 Fluent-Design."""

    def __init__(self, parent, items: list[dict], x: int, y: int):
        super().__init__(parent)
        self.withdraw()
        self.overrideredirect(True)
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass
        self.configure(bg="#cbd5e1", padx=1, pady=1)

        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id()) or self.winfo_id()
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUNDSMALL = 3
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(ctypes.c_int(DWMWCP_ROUNDSMALL)),
                ctypes.sizeof(ctypes.c_int),
            )
        except Exception:
            pass

        self.parent = parent
        self.items_spec = items
        self.row_widgets = []
        self._closed = False
        self._outside_bind_id = None

        self.card = tk.Frame(self, bg="#ffffff", padx=4, pady=4)
        self.card.pack(fill="both", expand=True)

        for entry in items:
            kind = entry.get("kind", "cmd")
            if kind == "sep" or entry.get("separator"):
                sep = tk.Frame(self.card, bg="#e2e8f0", height=1)
                sep.pack(fill="x", padx=8, pady=4)
                continue
            if kind == "header":
                hdr = tk.Label(
                    self.card,
                    text=entry.get("label", ""),
                    font=("Segoe UI", 8, "bold"),
                    bg="#ffffff",
                    fg="#64748b",
                    anchor="w",
                    padx=10,
                    pady=4,
                )
                hdr.pack(fill="x")
                continue

            icon = entry.get("icon", "")
            label = entry.get("label", "")
            cmd = entry.get("command")
            shortcut = entry.get("shortcut", "")
            danger = bool(entry.get("danger", False))
            accent = bool(entry.get("accent", False))

            # Shortcut automatisch aus Label extrahieren, falls vorhanden (z. B. "(F2)", "(Entf)", "(Strg+Z)")
            if not shortcut and "(" in label and label.endswith(")"):
                pos = label.rfind("(")
                cand = label[pos + 1 : -1].strip()
                if cand in ("F2", "Entf", "Leertaste", "Enter", "Strg+Z", "Strg+F", "Esc") or "Taste" in cand:
                    shortcut = cand
                    label = label[:pos].strip()

            base_bg = "#ffffff"
            hover_bg = "#fef2f2" if danger else ("#eff6ff" if accent else "#f1f5f9")
            fg_norm = "#dc2626" if danger else ("#2563eb" if accent else "#0f172a")
            font_spec = ("Segoe UI", 9, "bold" if accent else "normal")

            row = tk.Frame(self.card, bg=base_bg, cursor="hand2")
            row.pack(fill="x", padx=2, pady=1)

            photo = get_fluent_menu_icon_photo(icon, size=16, is_active=accent, is_danger=danger)
            lbl_ico = tk.Label(row, image=photo, bg=base_bg, width=22, height=22, anchor="center")
            lbl_ico.image = photo
            lbl_ico.pack(side="left", padx=(6, 8), pady=3)

            lbl_txt = tk.Label(row, text=label, font=font_spec, bg=base_bg, fg=fg_norm, anchor="w")
            lbl_txt.pack(side="left", fill="x", expand=True, pady=3)

            lbl_sc = None
            if shortcut:
                lbl_sc = tk.Label(row, text=shortcut, font=("Segoe UI", 8), bg=base_bg, fg="#64748b", anchor="e")
                lbl_sc.pack(side="right", padx=(8, 8), pady=3)

            def _make_handlers(r=row, li=lbl_ico, lt=lbl_txt, ls=lbl_sc, hb=hover_bg, bb=base_bg, fn_cmd=cmd):
                widgets = [w for w in (r, li, lt, ls) if w is not None]

                def on_enter(_e=None):
                    for w in widgets:
                        w.config(bg=hb)

                def on_leave(_e=None):
                    for w in widgets:
                        w.config(bg=bb)

                def on_click(_e=None):
                    self.close()
                    if callable(fn_cmd):
                        self.parent.after(10, fn_cmd)

                for w in widgets:
                    w.bind("<Enter>", on_enter)
                    w.bind("<Leave>", on_leave)
                    w.bind("<Button-1>", on_click)

                return {"frame": r, "icon": li, "label": lt, "invoke": on_click}

            self.row_widgets.append(_make_handlers())

        self.update_idletasks()
        mw = max(260, self.winfo_reqwidth() + 16)
        mh = self.winfo_reqheight()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()

        px = x if (x + mw) <= (sw - 8) else max(8, x - mw)
        py = y if (y + mh) <= (sh - 36) else max(8, y - mh)
        self.geometry(f"{mw}x{mh}+{px}+{py}")
        self.deiconify()
        self.lift()
        self.focus_set()

        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<FocusOut>", self._on_focus_out)
        try:
            self._outside_bind_id = self.parent.bind("<Button-1>", self._on_parent_click, add="+")
        except Exception:
            self._outside_bind_id = None

    def _on_focus_out(self, _e=None):
        if self._closed:
            return
        try:
            foc = self.focus_get()
            if foc is None or foc.winfo_toplevel() is not self:
                self.after(30, self.close)
        except Exception:
            self.close()

    def _on_parent_click(self, event=None):
        if self._closed:
            return
        try:
            if event is not None:
                rx, ry = event.x_root, event.y_root
                wx, wy = self.winfo_rootx(), self.winfo_rooty()
                ww, wh = self.winfo_width(), self.winfo_height()
                if wx <= rx <= wx + ww and wy <= ry <= wy + wh:
                    return
        except Exception:
            pass
        self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._outside_bind_id is not None:
            try:
                self.parent.unbind("<Button-1>", self._outside_bind_id)
            except Exception:
                pass
            self._outside_bind_id = None
        try:
            self.destroy()
        except Exception:
            pass


class FilePreviewPane(tk.Frame):
    """Integrierte Light-Fluent Dateivorschau rechts in Schritt 2 (ohne externe Standard-App)."""

    def __init__(self, parent, on_close=None, on_open_app=None, on_open_folder=None):
        super().__init__(
            parent,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            width=340,
            padx=14,
            pady=12,
        )
        self.pack_propagate(False)
        self._on_close = on_close
        self._on_open_app = on_open_app
        self._on_open_folder = on_open_folder
        self._current_item = None
        self._preview_photo = None
        self._last_stage_size = (310, 260)
        self._resize_after_id = None

        # Kopfzeile
        head = tk.Frame(self, bg="#ffffff")
        head.pack(fill="x", pady=(0, 8))

        tk.Label(
            head,
            text="👁 Dateivorschau",
            font=("Segoe UI", 10, "bold"),
            bg="#ffffff",
            fg="#0f172a",
        ).pack(side="left")

        self.lbl_badge = tk.Label(
            head,
            text="",
            font=("Segoe UI", 8, "bold"),
            bg="#eff6ff",
            fg="#1d4ed8",
            padx=7,
            pady=2,
        )

        self.btn_close = tk.Label(
            head,
            text="✕",
            font=("Segoe UI", 10, "bold"),
            bg="#ffffff",
            fg="#64748b",
            cursor="hand2",
            padx=6,
            pady=1,
        )
        self.btn_close.pack(side="right")
        self.btn_close.bind("<Enter>", lambda _e: self.btn_close.config(
            bg=theme_color("#f1f5f9", "bg"), fg=theme_color("#0f172a", "fg")
        ))
        self.btn_close.bind("<Leave>", lambda _e: self.btn_close.config(
            bg=theme_color("#ffffff", "bg"), fg=theme_color("#64748b", "fg")
        ))
        self.btn_close.bind("<Button-1>", lambda _e: self._on_close() if callable(self._on_close) else None)

        # Untere Elemente zuerst von unten nach oben packen, damit stage frei mitwächst
        act_bar = tk.Frame(self, bg="#ffffff")
        act_bar.pack(fill="x", side="bottom")

        self.btn_open_app = ModernRoundedButton(
            act_bar,
            text="📄 Standard-App",
            command=lambda: self._on_open_app(self._current_item) if (self._current_item and callable(self._on_open_app)) else None,
            bg="#e0f2fe",
            fg="#0369a1",
            hover_bg="#bae6fd",
            border_color="#7dd3fc",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=5,
        )
        self.btn_open_app.pack(side="left", padx=(0, 6))

        self.btn_open_dir = ModernRoundedButton(
            act_bar,
            text="📂 Im Explorer",
            command=lambda: self._on_open_folder(self._current_item) if (self._current_item and callable(self._on_open_folder)) else None,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=5,
        )
        self.btn_open_dir.pack(side="left")

        self.meta_box = tk.Frame(self, bg="#ffffff")
        self.meta_box.pack(fill="x", side="bottom", pady=(0, 10))
        self._meta_labels = {}
        for key, title in (
            ("type_size", "Typ & Größe:"),
            ("date", "Geändert am:"),
            ("category", "Kategorie:"),
            ("target", "Zielordner:"),
            ("path", "Quellpfad:"),
        ):
            r = tk.Frame(self.meta_box, bg="#ffffff")
            r.pack(fill="x", pady=1)
            tk.Label(r, text=title, font=("Segoe UI", 8, "bold"), bg="#ffffff", fg="#64748b", width=11, anchor="nw").pack(side="left")
            val_lbl = tk.Label(r, text="—", font=("Segoe UI", 8), bg="#ffffff", fg="#334155", anchor="w", wraplength=215, justify="left")
            val_lbl.pack(side="left", fill="x", expand=True)
            self._meta_labels[key] = val_lbl

        self.lbl_name = tk.Label(
            self,
            text="Keine Datei ausgewählt",
            font=("Segoe UI", 10, "bold"),
            bg="#ffffff",
            fg="#0f172a",
            anchor="w",
            wraplength=308,
            justify="left",
        )
        self.lbl_name.pack(fill="x", side="bottom", pady=(0, 6))

        # Vorschau-Bühne (wächst beim Aufziehen des Trenners automatisch mit)
        self.stage = tk.Frame(
            self,
            bg="#f8fafc",
            highlightthickness=1,
            highlightbackground="#e2e8f0",
            height=260,
        )
        self.stage.pack(fill="both", expand=True, pady=(0, 10))
        self.stage.pack_propagate(False)

        self.lbl_image = tk.Label(
            self.stage,
            bg="#f8fafc",
            fg="#475569",
            font=("Segoe UI", 9),
            wraplength=280,
            justify="center",
        )

        self.txt_wrap = tk.Frame(self.stage, bg="#f8fafc")
        self.txt_preview = tk.Text(
            self.txt_wrap,
            bg="#f8fafc",
            fg="#1e293b",
            font=("Segoe UI", 9),
            relief="flat",
            bd=0,
            highlightthickness=0,
            wrap="word",
            padx=8,
            pady=8,
        )
        self.txt_scroll = ttk.Scrollbar(self.txt_wrap, orient="vertical", command=self.txt_preview.yview)
        self.txt_preview.configure(yscrollcommand=self.txt_scroll.set)
        self.txt_preview.pack(side="left", fill="both", expand=True)
        self.txt_scroll.pack(side="right", fill="y")

        self.cloud_box = tk.Frame(self.stage, bg="#f0f9ff", padx=12, pady=12)
        tk.Label(
            self.cloud_box,
            text="☁️ Nur in OneDrive gespeichert",
            font=("Segoe UI", 10, "bold"),
            bg="#f0f9ff",
            fg="#0369a1",
        ).pack(pady=(18, 6))
        tk.Label(
            self.cloud_box,
            text="Um Datenvolumen und Downloadzeit zu sparen, lädt die Vorschau Cloud-Dateien nicht automatisch herunter.",
            font=("Segoe UI", 8),
            bg="#f0f9ff",
            fg="#334155",
            wraplength=260,
            justify="center",
        ).pack(pady=(0, 12))
        self.btn_cloud_load = ModernRoundedButton(
            self.cloud_box,
            text="☁️ Jetzt laden & anzeigen",
            command=self._load_cloud_now,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=6,
        )
        self.btn_cloud_load.pack()
        self.bind("<Configure>", self._on_pane_configure)
        self.stage.bind("<Configure>", self._on_pane_configure)

    def _on_pane_configure(self, _event=None):
        try:
            pane_w = max(260, self.winfo_width())
            stage_w = max(220, self.stage.winfo_width())
            stage_h = max(200, self.stage.winfo_height())
            self.lbl_image.config(wraplength=max(180, stage_w - 28))
            self.lbl_name.config(wraplength=max(200, pane_w - 32))
            for lbl in self._meta_labels.values():
                lbl.config(wraplength=max(140, pane_w - 115))
            old_w, old_h = self._last_stage_size
            if (
                self._current_item
                and self._preview_photo is not None
                and (abs(stage_w - old_w) >= 32 or abs(stage_h - old_h) >= 32)
            ):
                if self._resize_after_id is not None:
                    try:
                        self.after_cancel(self._resize_after_id)
                    except Exception:
                        pass
                self._resize_after_id = self.after(140, self._refresh_scaled_image)
        except Exception:
            pass

    def _refresh_scaled_image(self):
        self._resize_after_id = None
        if self._current_item and self._preview_photo is not None:
            self.show_item(self._current_item)

    def _clear_stage(self):
        self.lbl_image.pack_forget()
        self.txt_wrap.pack_forget()
        self.cloud_box.pack_forget()
        self.lbl_badge.pack_forget()
        self._preview_photo = None

    def _load_cloud_now(self):
        if self._current_item:
            self.show_item(self._current_item, allow_cloud_download=True)

    def show_empty(self, message: str = "Klicken Sie eine Zeile in der Tabelle an,\num eine direkte Vorschau zu sehen."):
        self._current_item = None
        self._clear_stage()
        self.lbl_image.config(image="", text=message, fg="#64748b")
        self.lbl_image.pack(fill="both", expand=True, padx=12, pady=12)
        self.lbl_name.config(text="Keine Datei ausgewählt")
        for lbl in self._meta_labels.values():
            lbl.config(text="—")

    def show_item(self, item: dict | None, allow_cloud_download: bool = False):
        if not item:
            self.show_empty()
            return

        self._current_item = item
        self._clear_stage()

        src_path = Path(item["src_path"])
        is_dir = bool(item.get("is_dir"))
        self.lbl_name.config(text=f"{'📁 ' if is_dir else ''}{item.get('name', src_path.name)}")
        if is_dir:
            cnt_str = item.get("items_count_str", "")
            sz_str = item.get("size_str", "—")
            combo = f"{sz_str} ({cnt_str})" if cnt_str and cnt_str != "—" else sz_str
            self._meta_labels["type_size"].config(text=f"ORDNER  ·  {combo}")
        else:
            self._meta_labels["type_size"].config(text=f"{item.get('ext', '—')}  ·  {item.get('size_str', '—')}")
        self._meta_labels["date"].config(text=item.get("date", "—"))
        self._meta_labels["category"].config(text=item.get("label", "—"))
        self._meta_labels["target"].config(text=item.get("target_sub", "—").replace("/", " \\ "))
        self._meta_labels["path"].config(text=item.get("source_path", str(src_path.parent)))

        if item.get("is_cloud_only") and not allow_cloud_download:
            self.lbl_badge.config(text="☁️ Cloud", bg="#f0f9ff", fg="#0369a1")
            self.lbl_badge.pack(side="left", padx=(8, 0))
            self.cloud_box.pack(fill="both", expand=True)
            return

        try:
            sw = max(280, int(self.stage.winfo_width()) - 14)
            sh = max(240, int(self.stage.winfo_height()) - 14)
        except Exception:
            sw, sh = 310, 260
        if sw <= 280 or sh <= 240:
            sw, sh = 320, 270
        self._last_stage_size = (sw + 14, sh + 14)
        self.lbl_image.config(wraplength=max(180, sw - 16))
        data = extract_file_preview_data(src_path, is_dir=is_dir, max_img_size=(sw, sh))
        badge = data.get("badge", "")
        if badge:
            self.lbl_badge.config(text=badge, bg="#eff6ff", fg="#1d4ed8")
            self.lbl_badge.pack(side="left", padx=(8, 0))

        kind = data.get("kind")
        if kind == "image" and data.get("image") is not None:
            photo = ImageTk.PhotoImage(data["image"])
            self._preview_photo = photo
            self.lbl_image.config(image=photo, text="")
            self.lbl_image.image = photo
            self.lbl_image.pack(fill="both", expand=True, padx=6, pady=6)
        elif kind in ("text", "dir"):
            self.txt_preview.config(state="normal")
            self.txt_preview.delete("1.0", "end")
            self.txt_preview.insert("1.0", data.get("text", ""))
            self.txt_preview.config(state="disabled")
            self.txt_wrap.pack(fill="both", expand=True)
        else:
            self.lbl_image.config(image="", text=data.get("text", "Keine Vorschau verfügbar."), fg="#475569")
            self.lbl_image.pack(fill="both", expand=True, padx=14, pady=14)


APP_INFO_SECTIONS = [
    (
        "Schritt 1 – Auswahl, Unterordner & Filter",
        "• Quelle, Ziel & Modus: Quellordner (oder Laufwerk), Zielordner und Aktion (Kopieren oder Verschieben) wählen. Vor Schritt 3 wird noch nichts verändert.\n"
        "• Unterordner – Inhalte einzeln: Durchsucht alle Unterordner und sortiert jede Datei einzeln nach Kategorie.\n"
        "• Unterordner – Als Ganzes: Hält jeden Unterordner komplett zusammen (ideal für Buch-, Foto- oder Projektordner).\n"
        "• Unterordner – Ignorieren: Sortiert nur lose Dateien im Hauptordner; alle Unterordner bleiben unberührt.\n"
        "• Filter: Dateitypen (Alle, Bilder, Texte & Docs, Office, Videos & Audio, Programme), Zeitraum, Mindestgröße und Suchfilter kombinierbar.",
    ),
    (
        "Schritt 2 – Vorschau, Anpassen & Aufräumen",
        "• Zielordner ändern: Eine oder mehrere Zeilen markieren → „Zielordner ändern“ oder „⚡ Zuletzt“ weist einen neuen Ordner zu – optional als Stichwort-Regel („⚙ Regeln“) für künftige Scans merken.\n"
        "• Dateinamen bearbeiten: Mit „F2“ direkt umbenennen, über „Namen kürzen“ automatisch straffen oder unten mittig per „⬅ Zurück zur Original-Vorschau“ alles zurücksetzen.\n"
        "• Live-Filter & Vorschau: „Strg+F“ und die Schnellfilter-Chips filtern sofort ohne Neuscan; „Leertaste“ oder „Vorschau“ zeigt Bilder, PDFs, Office-, ZIP- und Ordnerinhalte rechts an.\n"
        "• Schnelles Löschen: „Entf“ oder Rechtsklick verschiebt markierte Dateien direkt in den Windows-Papierkorb.",
    ),
    (
        "Schritt 3 – Sortieren & Rückgängig",
        "• Auswählen & Anwenden: Gewünschte Zeilen markieren oder per „✓ Alle wählen“ die ganze Liste auswählen – erst dann wird der blaue Button aktiv („Auf X markierte anwenden“ bzw. „Auf alle X anwenden“). Ohne Auswahl passiert nichts.\n"
        "• Sicher rückgängig machen: Nach dem Sortieren lässt sich der Vorgang per „Rückgängig“ komplett wiederherstellen.",
    ),
    (
        "Duplikate – Erkennung & Möglichkeiten",
        "• Echte Inhaltsprüfung: Zwei Dateien sind nur dann Duplikate, wenn ihr Byte-Inhalt (MD5) zu 100 % identisch ist – unabhängig vom Dateinamen.\n"
        "• Original-Wahl: Standardmäßig bleibt der sauberste Name (ohne „(1)“ oder „_kopie“); per „Original-Wahl“ umstellbar auf neueste, älteste oder pfadkürzeste Datei.\n"
        "• Duplikat-Aktionen: „Alle überspringen“, „Alle mitkopieren“, „Ausgewählte/Alle löschen“ oder per „Vergleichen…“ nebeneinander prüfen und Rollen tauschen.",
    ),
    (
        "Sortierlogik, Sicherheit & Offline-Betrieb",
        "• 7-Ordner-System: Ordnet automatisch nach Dateityp, Name und Textinhalt zu; Unbekanntes landet sicher in „01_Eingang_Inbox“. Ein Suchfilter legt bei Bildern automatisch einen passenden Unterordner an.\n"
        "• OneDrive-Schutz: Cloud-Platzhalter werden weder heruntergeladen noch gehasht.\n"
        "• Systemschutz & NetzwerkSchutz: Überspringt Windows-/Entwicklerordner und verhindert versehentliches Löschen oder Verschieben auf Netzlaufwerken (Ausschalten mit IT-Passwort, Standard: 1234).\n"
        "• 100 % offline: Keine Daten verlassen den PC; bei schreibgeschützter Installation liegen Einstellungen automatisch in %APPDATA%\\7-Ordner-Pro.",
    ),
]


class AdminPasswordDialog(tk.Toplevel):
    """Light-Fluent-Passwortabfrage für NetzwerkSchutz & Systemschutz (IT-Freigabe)."""

    def __init__(self, parent, guard_label: str = "Schutzfunktion"):
        super().__init__(parent)
        self.withdraw()
        self.title("IT-Freigabe erforderlich")
        self.configure(bg="#f8fafc")
        self.transient(parent)
        self.grab_set()
        self.result = False

        dw, dh = 470, 275
        try:
            parent.update_idletasks()
            px = parent.winfo_rootx() + max(0, (parent.winfo_width() - dw) // 2)
            py = parent.winfo_rooty() + max(0, (parent.winfo_height() - dh) // 2)
        except Exception:
            px, py = 180, 140
        self.geometry(f"{dw}x{dh}+{px}+{py}")
        self.resizable(False, False)

        card = tk.Frame(self, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1", padx=18, pady=16)
        card.pack(fill="both", expand=True, padx=14, pady=14)

        tk.Label(
            card,
            text=f"🔒 „{guard_label}“ deaktivieren",
            font=("Segoe UI", 11, "bold"),
            bg="#ffffff",
            fg="#0f172a",
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            card,
            text="Diese Sicherheitsfunktion ist passwortgeschützt, damit nur berechtigte\nPersonen (z. B. IT) sie abschalten können.",
            font=("Segoe UI", 9),
            bg="#ffffff",
            fg="#475569",
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(4, 12))

        tk.Label(card, text="IT-Passwort eingeben:", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155", anchor="w").pack(fill="x", pady=(0, 4))
        self.pw_var = tk.StringVar(value="")
        self.ent_pw = tk.Entry(card, textvariable=self.pw_var, show="•", font=("Segoe UI", 10), bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        self.ent_pw.pack(fill="x", ipady=4)

        self.lbl_err = tk.Label(card, text="", font=("Segoe UI", 9), bg="#ffffff", fg="#dc2626", anchor="w")
        self.lbl_err.pack(fill="x", pady=(6, 8))

        btn_row = tk.Frame(card, bg="#ffffff")
        btn_row.pack(fill="x", pady=(4, 0))
        ModernRoundedButton(
            btn_row,
            "🔑 Passwort ändern…",
            command=self._open_change_pw,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=5,
        ).pack(side="left")

        ModernRoundedButton(
            btn_row,
            "Freischalten",
            command=self._submit,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=5,
        ).pack(side="right")
        ModernRoundedButton(
            btn_row,
            "Abbrechen",
            command=self._cancel,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=5,
        ).pack(side="right", padx=(0, 8))

        self.ent_pw.bind("<Return>", lambda _e: self._submit())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.deiconify()
        self.ent_pw.focus_set()

    def _open_change_pw(self):
        dlg = ChangeAdminPasswordDialog(self)
        self.wait_window(dlg)
        self.pw_var.set("")
        self.lbl_err.config(text="")
        self.ent_pw.focus_set()

    def _submit(self):
        if verify_admin_password(self.pw_var.get()):
            self.result = True
            self.destroy()
        else:
            self.lbl_err.config(text="Falsches Passwort – Schutz bleibt aktiviert.")
            self.pw_var.set("")
            self.ent_pw.focus_set()

    def _cancel(self):
        self.result = False
        self.destroy()


class ChangeAdminPasswordDialog(tk.Toplevel):
    """Vorbereiteter IT-Dialog zum sicheren Ändern des Standard-/IT-Passworts."""

    def __init__(self, parent):
        super().__init__(parent)
        self.withdraw()
        self.title("IT-Passwort ändern")
        self.configure(bg="#f8fafc")
        self.transient(parent)
        self.grab_set()
        self.result = False

        is_def = is_default_admin_password()
        dw, dh = 480, 355 if is_def else 325
        try:
            parent.update_idletasks()
            px = parent.winfo_rootx() + max(0, (parent.winfo_width() - dw) // 2)
            py = parent.winfo_rooty() + max(0, (parent.winfo_height() - dh) // 2)
        except Exception:
            px, py = 180, 140
        self.geometry(f"{dw}x{dh}+{px}+{py}")
        self.resizable(False, False)

        card = tk.Frame(self, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1", padx=18, pady=16)
        card.pack(fill="both", expand=True, padx=14, pady=14)

        tk.Label(card, text="🔑 IT-Passwort verwalten / ändern", font=("Segoe UI", 11, "bold"), bg="#ffffff", fg="#0f172a", anchor="w").pack(fill="x")
        tk.Label(
            card,
            text="Schützt „NetzwerkSchutz“ und „Systemschutz“ vor unbefugtem Ausschalten.",
            font=("Segoe UI", 9),
            bg="#ffffff",
            fg="#475569",
            anchor="w",
        ).pack(fill="x", pady=(2, 8))

        if is_def:
            tk.Label(
                card,
                text="ℹ️ Ersteinrichtung: Standardpasswort ab Werk ist 1234 (bitte für die Firma ändern).",
                font=("Segoe UI", 8, "bold"),
                bg="#f0f9ff",
                fg="#0369a1",
                padx=8,
                pady=5,
                anchor="w",
            ).pack(fill="x", pady=(0, 8))

        self.cur_var = tk.StringVar(value="")
        self.new1_var = tk.StringVar(value="")
        self.new2_var = tk.StringVar(value="")

        tk.Label(card, text="Aktuelles Passwort:", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155", anchor="w").pack(fill="x")
        self.ent_cur = tk.Entry(card, textvariable=self.cur_var, show="•", font=("Segoe UI", 10), bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        self.ent_cur.pack(fill="x", ipady=3, pady=(2, 8))

        tk.Label(card, text="Neues Passwort (mindestens 4 Zeichen):", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155", anchor="w").pack(fill="x")
        self.ent_new1 = tk.Entry(card, textvariable=self.new1_var, show="•", font=("Segoe UI", 10), bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        self.ent_new1.pack(fill="x", ipady=3, pady=(2, 8))

        tk.Label(card, text="Neues Passwort bestätigen:", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155", anchor="w").pack(fill="x")
        self.ent_new2 = tk.Entry(card, textvariable=self.new2_var, show="•", font=("Segoe UI", 10), bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        self.ent_new2.pack(fill="x", ipady=3, pady=(2, 6))

        self.lbl_status = tk.Label(card, text="", font=("Segoe UI", 9), bg="#ffffff", fg="#dc2626", anchor="w")
        self.lbl_status.pack(fill="x", pady=(2, 8))

        btn_row = tk.Frame(card, bg="#ffffff")
        btn_row.pack(fill="x")
        ModernRoundedButton(
            btn_row,
            "Passwort speichern",
            command=self._save,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=5,
        ).pack(side="right")
        ModernRoundedButton(
            btn_row,
            "Abbrechen",
            command=self._cancel,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=5,
        ).pack(side="right", padx=(0, 8))

        self.ent_new2.bind("<Return>", lambda _e: self._save())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.deiconify()
        self.ent_cur.focus_set()

    def _save(self):
        cur = self.cur_var.get()
        n1 = self.new1_var.get()
        n2 = self.new2_var.get()
        if not verify_admin_password(cur):
            self.lbl_status.config(text="Das aktuelle Passwort stimmt nicht.", fg="#dc2626")
            self.cur_var.set("")
            self.ent_cur.focus_set()
            return
        if len(n1) < 4:
            self.lbl_status.config(text="Das neue Passwort muss mindestens 4 Zeichen lang sein.", fg="#dc2626")
            self.ent_new1.focus_set()
            return
        if n1 != n2:
            self.lbl_status.config(text="Die beiden neuen Passwörter stimmen nicht überein.", fg="#dc2626")
            self.new2_var.set("")
            self.ent_new2.focus_set()
            return
        try:
            save_admin_password(n1)
            self.result = True
            self.destroy()
        except Exception as exc:
            self.lbl_status.config(text=f"Speichern fehlgeschlagen: {exc}", fg="#dc2626")

    def _cancel(self):
        self.result = False
        self.destroy()


class AppInfoDialog(tk.Toplevel):
    """Helles Light-Fluent-Erklärungsfenster zur Funktionsweise (analog Portfolio Manager InfoHelp)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.withdraw()
        self.title("Info: Funktionsweise von 7-Ordner-Pro")
        self.configure(bg="#f8fafc")
        self.transient(parent)
        self.grab_set()

        dw, dh = 700, 620
        try:
            parent.update_idletasks()
            px = parent.winfo_rootx() + max(0, (parent.winfo_width() - dw) // 2)
            py = parent.winfo_rooty() + max(0, (parent.winfo_height() - dh) // 2)
        except Exception:
            px, py = 120, 90
        self.geometry(f"{dw}x{dh}+{px}+{py}")
        self.minsize(560, 460)
        apply_window_folder_icon(self)

        outer = tk.Frame(self, bg="#f8fafc", padx=18, pady=16)
        outer.pack(fill="both", expand=True)

        # Kopfzeile wie InfoHelp (Titel + Schliessen)
        hdr = tk.Frame(outer, bg="#f8fafc")
        hdr.pack(fill="x", pady=(0, 12))

        badge = get_notice_icon_photo("info", bg="#f8fafc", size=32)
        lbl_ico = tk.Label(hdr, image=badge, bg="#f8fafc", bd=0)
        lbl_ico.image = badge
        lbl_ico.pack(side="left", padx=(0, 10))

        title_col = tk.Frame(hdr, bg="#f8fafc")
        title_col.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_col,
            text=f"Funktionsweise: 7-Ordner-Pro {APP_VERSION}",
            font=("Segoe UI", 12, "bold"),
            bg="#f8fafc",
            fg="#0f172a",
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            title_col,
            text="Schritt-für-Schritt-Ablauf & alle Möglichkeiten kompakt erklärt",
            font=("Segoe UI", 9),
            bg="#f8fafc",
            fg="#64748b",
            anchor="w",
        ).pack(fill="x")

        self.btn_close = ModernRoundedButton(
            hdr,
            text="Schließen",
            command=self._on_close,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=6,
        )
        self.btn_close.pack(side="right")

        # Scrollbare weisse Fluent-Karte
        card = tk.Frame(outer, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1")
        card.pack(fill="both", expand=True)

        canvas = tk.Canvas(card, bg="#ffffff", highlightthickness=0, bd=0)
        v_scroll = ttk.Scrollbar(card, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=v_scroll.set)
        v_scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body = tk.Frame(canvas, bg="#ffffff", padx=18, pady=14)
        win_id = canvas.create_window((0, 0), window=body, anchor="nw")
        self._section_labels = []

        for idx, (term, desc) in enumerate(APP_INFO_SECTIONS):
            if idx > 0:
                tk.Frame(body, bg="#e2e8f0", height=1).pack(fill="x", pady=(10, 10))
            tk.Label(
                body,
                text=term,
                font=("Segoe UI", 10, "bold"),
                bg="#ffffff",
                fg="#0f172a",
                anchor="w",
                justify="left",
                wraplength=600,
            ).pack(fill="x", anchor="w", pady=(0, 4))
            lbl_desc = tk.Label(
                body,
                text=desc,
                font=("Segoe UI", 9),
                bg="#ffffff",
                fg="#334155",
                anchor="w",
                justify="left",
                wraplength=600,
            )
            lbl_desc.pack(fill="x", anchor="w")
            self._section_labels.append(lbl_desc)

        def _sync_scroll(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            cw = canvas.winfo_width()
            if cw > 80:
                canvas.itemconfigure(win_id, width=cw)
                wrap_w = max(320, cw - 40)
                for lb in self._section_labels:
                    lb.config(wraplength=wrap_w)

        body.bind("<Configure>", _sync_scroll)
        canvas.bind("<Configure>", _sync_scroll)

        def _on_wheel(ev):
            try:
                canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units")
            except Exception:
                pass

        self.bind("<MouseWheel>", _on_wheel)
        self.bind("<Escape>", lambda _e: self._on_close())
        self.bind("<Return>", lambda _e: self._on_close())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.update_idletasks()
        self.deiconify()
        self.lift()
        self.focus_set()

    def _on_close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class CustomRulesDialog(tk.Toplevel):
    """Light-Fluent-Verwaltung fuer gelernte Stichwort-Regeln (custom_rules.json)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.withdraw()
        self.title("Gespeicherte Stichwort-Regeln")
        self.configure(bg="#f8fafc")
        self.transient(parent)
        self.grab_set()
        self._main_app = getattr(parent, "_parent_app", parent)

        dw, dh = 760, 560
        try:
            parent.update_idletasks()
            px = parent.winfo_rootx() + max(0, (parent.winfo_width() - dw) // 2)
            py = parent.winfo_rooty() + max(0, (parent.winfo_height() - dh) // 2)
        except Exception:
            px, py = 140, 100
        self.geometry(f"{dw}x{dh}+{px}+{py}")
        self.minsize(620, 440)

        outer = tk.Frame(self, bg="#f8fafc", padx=16, pady=14)
        outer.pack(fill="both", expand=True)

        tk.Label(outer, text="Eigene Stichwort-Regeln (für künftige & aktuelle Scans)", font=("Segoe UI", 10, "bold"), bg="#f8fafc", fg="#0f172a").pack(anchor="w")
        tk.Label(outer, text="Dateien, deren Name oder Pfad ein Stichwort enthält, werden automatisch dem gewählten Zielordner zugeordnet (vor Standard-Regeln).", font=("Segoe UI", 9), bg="#f8fafc", fg="#64748b").pack(anchor="w", pady=(2, 10))

        # Karte: Neue Regel anlegen / ausgewaehlte bearbeiten
        edit_card = tk.Frame(outer, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1", padx=12, pady=10)
        edit_card.pack(fill="x", pady=(0, 10))

        tk.Label(edit_card, text="Regel anlegen oder bearbeiten (Klick auf Tabellenzeile übernimmt Werte):", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#0f172a").pack(anchor="w", pady=(0, 6))

        target_opts = get_available_target_options()
        self._target_label_map = {t.replace("/", " \\ "): l for t, l in target_opts}
        self._target_raw_map = {t.replace("/", " \\ "): t for t, _l in target_opts}
        combo_vals = [t.replace("/", " \\ ") for t, _l in target_opts]

        self.kw_var = tk.StringVar()
        self.tgt_var = tk.StringVar(value=combo_vals[0] if combo_vals else "01_Eingang_Inbox")
        self.lbl_var = tk.StringVar(value=self._target_label_map.get(self.tgt_var.get(), ""))

        row1 = tk.Frame(edit_card, bg="#ffffff")
        row1.pack(fill="x", pady=(0, 6))
        tk.Label(row1, text="Stichwort:", width=10, anchor="w", font=("Segoe UI", 9), bg="#ffffff", fg="#334155").pack(side="left")
        self.ent_kw = tk.Entry(row1, textvariable=self.kw_var, width=22, font=("Segoe UI", 9), bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        self.ent_kw.pack(side="left", padx=(0, 12), ipady=3)

        tk.Label(row1, text="Zielordner:", width=9, anchor="w", font=("Segoe UI", 9), bg="#ffffff", fg="#334155").pack(side="left")
        self.cmb_tgt = ttk.Combobox(row1, textvariable=self.tgt_var, values=combo_vals, font=("Segoe UI", 9))
        self.cmb_tgt.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=2)
        self.cmb_tgt.bind("<<ComboboxSelected>>", self._on_target_combo_selected)

        ModernRoundedButton(
            row1,
            text="Ordner…",
            command=self._browse_rule_folder,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#cbd5e1",
            font=("Segoe UI", 9),
            r=8,
            padx=10,
            pady=4,
            icon_kind="folder_adjacent",
        ).pack(side="left")

        row2 = tk.Frame(edit_card, bg="#ffffff")
        row2.pack(fill="x")
        tk.Label(row2, text="Kategorie:", width=10, anchor="w", font=("Segoe UI", 9), bg="#ffffff", fg="#334155").pack(side="left")
        self.ent_lbl = tk.Entry(row2, textvariable=self.lbl_var, width=26, font=("Segoe UI", 9), bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        self.ent_lbl.pack(side="left", fill="x", expand=True, padx=(0, 12), ipady=3)

        ModernRoundedButton(
            row2,
            text="+ Regel speichern",
            command=self._save_rule_from_form,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=5,
        ).pack(side="right")

        self.ent_kw.bind("<Return>", lambda _e: self._save_rule_from_form())
        self.ent_lbl.bind("<Return>", lambda _e: self._save_rule_from_form())

        # Fusszeile ZUERST am unteren Rand packen, damit Buttons niemals abgeschnitten werden
        foot = tk.Frame(outer, bg="#f8fafc")
        foot.pack(side="bottom", fill="x", pady=(10, 2))

        ModernRoundedButton(
            foot,
            text="🗑 Ausgewählte löschen",
            command=self._delete_selected,
            bg="#ffffff",
            fg="#dc2626",
            hover_bg="#fee2e2",
            border_color="#fca5a5",
            font=("Segoe UI", 9),
            r=8,
            padx=12,
            pady=6,
        ).pack(side="left", padx=(0, 8))

        ModernRoundedButton(
            foot,
            text="▶ Auf aktuelle Tabelle anwenden",
            command=self._apply_rules_to_current_table,
            bg="#ffffff",
            fg="#1d4ed8",
            hover_bg="#eff6ff",
            border_color="#93c5fd",
            font=("Segoe UI", 9),
            r=8,
            padx=12,
            pady=6,
        ).pack(side="left")

        ModernRoundedButton(
            foot,
            text="Schließen",
            command=self._on_close,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            font=("Segoe UI", 9),
            r=8,
            padx=16,
            pady=6,
        ).pack(side="right")

        self.lbl_status = tk.Label(outer, text="", font=("Segoe UI", 9), bg="#f8fafc", fg="#1d4ed8", anchor="w")
        self.lbl_status.pack(side="bottom", fill="x", pady=(6, 0))

        card = tk.Frame(outer, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1")
        card.pack(fill="both", expand=True)

        tree_style = ttk.Style(self)
        try:
            if not tree_style.layout("Picker.Treeview"):
                tree_style.layout("Picker.Treeview", tree_style.layout("Treeview"))
            if not tree_style.layout("Picker.Treeview.Heading"):
                tree_style.layout("Picker.Treeview.Heading", tree_style.layout("Treeview.Heading"))
        except Exception:
            pass
        tree_style.configure("Picker.Treeview", background="#ffffff", fieldbackground="#ffffff", foreground="#0f172a", rowheight=26, font=("Segoe UI", 9), borderwidth=0, relief="flat")
        tree_style.configure("Picker.Treeview.Heading", background="#f8fafc", foreground="#475569", font=("Segoe UI", 9), bordercolor="#e2e8f0", lightcolor="#e2e8f0", darkcolor="#e2e8f0", borderwidth=1, relief="solid", padding=(6, 5))

        self.tree = ttk.Treeview(card, columns=("keyword", "target", "label"), show="headings", selectmode="extended", height=8, style="Picker.Treeview")
        self.tree.heading("keyword", text="Stichwort")
        self.tree.heading("target", text="Zielordner")
        self.tree.heading("label", text="Kategorie")
        self.tree.column("keyword", width=160, anchor="w")
        self.tree.column("target", width=330, anchor="w")
        self.tree.column("label", width=190, anchor="w")
        vs = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        self._reload()

        self.bind("<Escape>", lambda _e: self._on_close())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.update_idletasks()
        self.deiconify()
        self.lift()
        self.ent_kw.focus_set()

    def _set_status(self, msg: str, color: str = "#1d4ed8"):
        try:
            self.lbl_status.configure(text=msg, fg=color)
        except Exception:
            pass

    def _on_target_combo_selected(self, _ev=None):
        disp = self.tgt_var.get().strip()
        if disp in self._target_label_map:
            self.lbl_var.set(self._target_label_map[disp])

    def _browse_rule_folder(self):
        chosen = filedialog.askdirectory(parent=self, title="Zielordner für Regel wählen")
        if chosen:
            norm = os.path.normpath(chosen)
            self.tgt_var.set(norm)
            if not self.lbl_var.get().strip():
                self.lbl_var.set(os.path.basename(norm) or norm)

    def _on_tree_select(self, _ev=None):
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")
        if vals and len(vals) >= 3:
            self.kw_var.set(str(vals[0]))
            self.tgt_var.set(str(vals[1]))
            self.lbl_var.set(str(vals[2]))

    def _save_rule_from_form(self):
        kw = self.kw_var.get().strip().lower()
        raw_tgt = self.tgt_var.get().strip()
        if len(kw) < 2:
            self._set_status("Bitte ein Stichwort mit mindestens 2 Zeichen eingeben (z. B. ffmpeg, haus, ricardo).", "#dc2626")
            self.ent_kw.focus_set()
            return
        if not raw_tgt:
            self._set_status("Bitte einen Zielordner auswählen oder eingeben.", "#dc2626")
            return
        target = self._target_raw_map.get(raw_tgt) or (os.path.normpath(raw_tgt) if os.path.isabs(raw_tgt) else raw_tgt.replace(" \\ ", "/").replace("\\", "/"))
        lbl = self.lbl_var.get().strip() or (os.path.basename(target.rstrip("/\\")) or target)
        save_custom_rule(kw, target, lbl)
        self._reload()
        self._set_status(f"Regel „{kw}“ → {target.replace('/', ' \\ ')} gespeichert.", "#15803d")
        self.kw_var.set("")
        self.ent_kw.focus_set()

    def _apply_rules_to_current_table(self):
        app = self._main_app
        if not app or not getattr(app, "preview_items", None):
            self._set_status("Aktuell sind keine gescannten Dateien in Schritt 2 geladen.", "#64748b")
            return
        rules = load_custom_rules()
        if not rules:
            self._set_status("Es sind noch keine Regeln gespeichert – bitte oben mindestens eine Regel speichern.", "#dc2626")
            return
        total_updated = 0
        all_matched_rids = []
        seen_rids = set()
        for rule in reversed(rules):
            matching_rids = []
            for rid, item in list(app.preview_items.items()):
                if item.get("action") == "Gelöscht" or item.get("is_duplicate"):
                    continue
                hay = f"{item.get('name', '')} {item.get('source_path', '')}".lower()
                if _rule_matches_text(rule["keyword"], hay):
                    matching_rids.append(rid)
                    if rid not in seen_rids:
                        seen_rids.add(rid)
                        all_matched_rids.append(rid)
            if matching_rids:
                total_updated += app._apply_target_assignment(matching_rids, rule["target"], rule["label"], save_rule=False)
        if total_updated > 0:
            visible_matched = [r for r in all_matched_rids if app.tree.exists(r)]
            if visible_matched:
                try:
                    app.tree.selection_set(visible_matched)
                    app.tree.see(visible_matched[0])
                except Exception:
                    pass
            if hasattr(app, "_update_execute_scope_button"):
                app._update_execute_scope_button()
            sample_names = [app.preview_items[r].get("name", "") for r in all_matched_rids[:3] if r in app.preview_items]
            sample_txt = ", ".join(n for n in sample_names if n)
            more_txt = "…" if len(all_matched_rids) > 3 else ""
            preview_hint = f" ({sample_txt}{more_txt})" if sample_txt else ""
            self._set_status(
                f"{total_updated} Einträge in der Tabelle neu zugeordnet & markiert{preview_hint} – nach „Schließen“ direkt ausführbar.",
                "#15803d",
            )
        else:
            self._set_status("Keine offenen Einträge in der aktuellen Tabelle passen auf die gespeicherten Regeln.", "#64748b")

    def _reload(self):
        for rid in self.tree.get_children(""):
            self.tree.delete(rid)
        self._rules = load_custom_rules()
        for r in self._rules:
            self.tree.insert("", "end", values=(r["keyword"], r["target"].replace("/", " \\ "), r["label"]))

    def _delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            self._set_status("Bitte zuerst eine Regel in der Liste auswählen.", "#64748b")
            return
        for rid in sel:
            vals = self.tree.item(rid, "values")
            if vals:
                delete_custom_rule(vals[0])
        self._reload()
        self._set_status(f"{len(sel)} Regel(n) gelöscht.", "#1d4ed8")

    def _on_close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class TargetFolderDialog(tk.Toplevel):
    """Modernes Light-Fluent-Fenster zum Umstellen des Zielordners inkl. 5-Slot-Schnellauswahl & Automatik-Regel."""

    def __init__(self, parent, items: list[dict], total_active_count: int | None = None):
        super().__init__(parent)
        self.withdraw()
        self.title("Zielordner wählen & Schnellauswahl")
        self.configure(bg="#f8fafc")
        self.transient(parent)
        self.grab_set()
        self.result = None

        dw, dh = 920, 710
        try:
            parent.update_idletasks()
            px = parent.winfo_rootx() + max(0, (parent.winfo_width() - dw) // 2)
            py = parent.winfo_rooty() + max(0, (parent.winfo_height() - dh) // 2)
        except Exception:
            px, py = 100, 60
        self.geometry(f"{dw}x{dh}+{px}+{py}")
        self.minsize(800, 580)

        self._parent_app = parent
        self._items = items or []
        first = self._items[0] if self._items else {}
        cur_target = str(first.get("target_sub") or "01_Eingang_Inbox")
        cur_label = str(first.get("label") or "")

        self._total_cnt = total_active_count if total_active_count is not None else len(self._items)
        self._sel_cnt = len(self._items)
        is_all_default = (self._sel_cnt == self._total_cnt or self._sel_cnt == 0)
        self.apply_all_rows_var = tk.BooleanVar(value=is_all_default)

        self.target_var = tk.StringVar(value=cur_target)
        self.label_var = tk.StringVar(value=cur_label)
        self.search_var = tk.StringVar(value="")
        self.save_rule_var = tk.BooleanVar(value=False)
        self.keyword_var = tk.StringVar(value=suggest_rule_keyword(first.get("name", "")) if first else "")
        self.apply_preview_var = tk.BooleanVar(value=True)

        self._all_target_options = list(get_available_target_options())
        self._target_to_label = {t: l for t, l in self._all_target_options}
        self._row_map: dict[str, tuple[str, str]] = {}
        self._slot_widgets: list[dict] = []
        self._updating_target_internally = False

        outer = tk.Frame(self, bg="#f8fafc", padx=18, pady=14)
        outer.pack(fill="both", expand=True)

        # ── 1. HEADER mit klarem 2-Wege-Umschalter (Auswahl vs. Alle) ──
        head_row = tk.Frame(outer, bg="#f8fafc")
        head_row.pack(fill="x", pady=(0, 10))

        head_left = tk.Frame(head_row, bg="#f8fafc")
        head_left.pack(side="left", fill="x", expand=True)

        tk.Label(
            head_left,
            text="Zielordner wählen",
            font=("Segoe UI", 13, "bold"),
            bg="#f8fafc",
            fg="#0f172a",
            anchor="w",
        ).pack(anchor="w")

        cur_disp = os.path.normpath(cur_target) if os.path.isabs(cur_target) else cur_target.replace("/", " \\ ")
        self.lbl_sub = tk.Label(
            head_left,
            text=f"Aktuell: {cur_disp}",
            font=("Segoe UI", 9),
            bg="#f8fafc",
            fg="#64748b",
            anchor="w",
        )
        self.lbl_sub.pack(anchor="w", pady=(1, 0))

        # Segmented Scope Toggle rechts im Header
        scope_wrap = tk.Frame(head_row, bg="#f8fafc")
        scope_wrap.pack(side="right", anchor="e")

        self.btn_scope_sel = None
        self.btn_scope_all = None
        if 0 < self._sel_cnt < self._total_cnt:
            tk.Label(
                scope_wrap,
                text="Gilt für:",
                font=("Segoe UI", 8, "bold"),
                bg="#f8fafc",
                fg="#64748b",
            ).pack(side="left", padx=(0, 8))

            self.btn_scope_sel = ModernRoundedButton(
                scope_wrap,
                text=f"Nur Auswahl · {self._sel_cnt}",
                command=lambda: self._set_scope_all(False),
                bg="#2563eb",
                fg="#ffffff",
                hover_bg="#1d4ed8",
                border_color=None,
                font=("Segoe UI", 9, "bold"),
                r=8,
                padx=12,
                pady=5,
            )
            self.btn_scope_sel.pack(side="left", padx=(0, 6))

            self.btn_scope_all = ModernRoundedButton(
                scope_wrap,
                text=f"Alle Einträge · {self._total_cnt}",
                command=lambda: self._set_scope_all(True),
                bg="#ffffff",
                fg="#1e293b",
                hover_bg="#f1f5f9",
                border_color="#94a3b8",
                font=("Segoe UI", 9, "bold"),
                r=8,
                padx=12,
                pady=5,
            )
            self.btn_scope_all.pack(side="left")
            self._update_scope_buttons()
        else:
            pill_lbl = tk.Label(
                scope_wrap,
                text=f"Alle {self._total_cnt} Einträge",
                font=("Segoe UI", 9, "bold"),
                bg="#dbeafe",
                fg="#1d4ed8",
                padx=10,
                pady=4,
            )
            pill_lbl.pack(side="right")

        # ── UNTEN ZUERST PACKEN: Fußzeile & optionale Regel-Karte (nie abgeschnitten!) ──
        foot = tk.Frame(outer, bg="#f8fafc")
        foot.pack(side="bottom", fill="x", pady=(12, 0))

        ModernRoundedButton(
            foot,
            text="⚙ Regeln verwalten",
            command=lambda: CustomRulesDialog(self),
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=6,
        ).pack(side="left")

        # Primär-Aktion ganz rechts: Sicher in Vorschau übernehmen!
        ModernRoundedButton(
            foot,
            text="✓ Ziel übernehmen",
            command=self._on_confirm,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            border_color=None,
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=16,
            pady=6,
        ).pack(side="right")

        ModernRoundedButton(
            foot,
            text="➔ Sofort verschieben",
            command=lambda: self._on_confirm(execute_mode="move"),
            bg="#fff7ed",
            fg="#c2410c",
            hover_bg="#ffedd5",
            border_color="#fdba74",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=6,
        ).pack(side="right", padx=(0, 8))

        ModernRoundedButton(
            foot,
            text="📋 Sofort kopieren",
            command=lambda: self._on_confirm(execute_mode="copy"),
            bg="#f0fdf4",
            fg="#15803d",
            hover_bg="#dcfce7",
            border_color="#86efac",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=6,
        ).pack(side="right", padx=(0, 8))

        ModernRoundedButton(
            foot,
            text="Abbrechen",
            command=self._on_close,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=6,
        ).pack(side="right", padx=(0, 8))

        # ── KARTE 3: Optionale Automatik-Regel (verständlich & kompakt) ──
        rule_card = tk.Frame(outer, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1", padx=14, pady=9)
        rule_card.pack(side="bottom", fill="x", pady=(10, 0))

        rule_top = tk.Frame(rule_card, bg="#ffffff")
        rule_top.pack(fill="x")

        tk.Checkbutton(
            rule_top,
            text="Für künftige Scans automatisch merken",
            variable=self.save_rule_var,
            command=self._update_rule_state,
            font=("Segoe UI", 9, "bold"),
            bg="#ffffff",
            fg="#0f172a",
            activebackground="#ffffff",
            cursor="hand2",
        ).pack(side="left")

        tk.Label(
            rule_top,
            text="Passende Dateien landen künftig von selbst in diesem Zielordner",
            font=("Segoe UI", 8),
            bg="#ffffff",
            fg="#64748b",
        ).pack(side="right")

        self.rule_detail = tk.Frame(rule_card, bg="#ffffff")
        self.rule_detail.pack(fill="x", pady=(6, 0))

        self.lbl_kw = tk.Label(self.rule_detail, text="Wenn Name enthält:", font=("Segoe UI", 9), bg="#ffffff", fg="#334155")
        self.lbl_kw.pack(side="left", padx=(20, 8))

        kw_box = create_filter_box(
            self.rule_detail,
            self.keyword_var,
            width=220,
            height=30,
            placeholder="z. B. Rechnung, Buch…",
        )
        kw_box.pack(side="left")
        self.ent_keyword = kw_box.entry

        self.chk_apply_preview = tk.Checkbutton(
            self.rule_detail,
            text="Sofort auch auf passende Zeilen in dieser Liste anwenden",
            variable=self.apply_preview_var,
            font=("Segoe UI", 9),
            bg="#ffffff",
            fg="#334155",
            activebackground="#ffffff",
            cursor="hand2",
        )
        self.chk_apply_preview.pack(side="left", padx=(14, 0))
        self._update_rule_state()

        # ── KARTE 1: Schnellauswahl (5 gleich breite Fluent-Karten) ──
        self.recent_card = tk.Frame(outer, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1", padx=14, pady=10)
        self.recent_card.pack(fill="x", pady=(0, 10))

        rc_head = tk.Frame(self.recent_card, bg="#ffffff")
        rc_head.pack(fill="x", pady=(0, 8))

        ico_zap = get_fluent_menu_icon_photo("zap", size=16)
        lbl_zap = tk.Label(rc_head, image=ico_zap, bg="#ffffff")
        lbl_zap.image = ico_zap
        lbl_zap.pack(side="left", padx=(0, 6))

        tk.Label(
            rc_head,
            text="Schnellauswahl",
            font=("Segoe UI", 10, "bold"),
            bg="#ffffff",
            fg="#0f172a",
        ).pack(side="left")

        tk.Label(
            rc_head,
            text="1× Klick = Auswählen  ·  2× Klick = Sofort übernehmen  ·  Pin = Dauerhaft behalten",
            font=("Segoe UI", 8),
            bg="#ffffff",
            fg="#64748b",
        ).pack(side="right")

        self.slots_frame = tk.Frame(self.recent_card, bg="#ffffff")
        self.slots_frame.pack(fill="x")
        for c_idx in range(5):
            self.slots_frame.columnconfigure(c_idx, weight=1, uniform="slot")

        # ── KARTE 2: Zielordner wählen (7-Ordner-System ODER eigener Ordner auf PC/USB) ──
        list_card = tk.Frame(outer, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1", padx=14, pady=10)
        list_card.pack(fill="both", expand=True)

        # Obere Werkzeugzeile in Karte 2: Suche links + 3 schnelle Ordner-Buttons rechts (alle 32px hoch!)
        bar_row = tk.Frame(list_card, bg="#ffffff")
        bar_row.pack(fill="x", pady=(0, 8))

        search_box = create_filter_box(
            bar_row,
            self.search_var,
            width=260,
            height=32,
            placeholder="Kategorie oder Ordner suchen…",
        )
        search_box.pack(side="left")
        self.ent_search = search_box.entry

        ModernRoundedButton(
            bar_row,
            text="Hauptziel",
            command=self._pick_main_target_folder,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=11,
            pady=5,
            icon_kind="folder_same",
        ).pack(side="right")

        ModernRoundedButton(
            bar_row,
            text="USB-Stick",
            command=self._pick_usb_stick_folder,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=11,
            pady=5,
            icon_kind="usb_stick",
        ).pack(side="right", padx=(0, 6))

        ModernRoundedButton(
            bar_row,
            text="Ordner auf PC wählen…",
            command=self._browse_custom_folder,
            bg="#eff6ff",
            fg="#1d4ed8",
            hover_bg="#dbeafe",
            border_color="#93c5fd",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=5,
            icon_kind="folder_adjacent",
        ).pack(side="right", padx=(0, 6))

        # Unten in Karte 2 ZUERST packen: Klares gewähltes Ziel-Feld + Kategorie-Badge + Anpinnen
        sel_bar = tk.Frame(list_card, bg="#f8fafc", highlightthickness=1, highlightbackground="#e2e8f0", padx=10, pady=7)
        sel_bar.pack(side="bottom", fill="x", pady=(8, 0))

        self._ico_folder_bar = get_fluent_menu_icon_photo("folder", size=16)
        self._ico_usb_bar = get_fluent_menu_icon_photo("usb", size=16)
        self.lbl_target_ico = tk.Label(sel_bar, image=self._ico_folder_bar, bg="#f8fafc")
        self.lbl_target_ico.pack(side="left", padx=(0, 6))

        tk.Label(
            sel_bar,
            text="Gewähltes Ziel:",
            font=("Segoe UI", 9, "bold"),
            bg="#f8fafc",
            fg="#334155",
        ).pack(side="left", padx=(0, 8))

        self.btn_pin_current = ModernRoundedButton(
            sel_bar,
            text="📌 Anpinnen",
            command=self._pin_current_target,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#eff6ff",
            border_color="#94a3b8",
            font=("Segoe UI", 8, "bold"),
            r=8,
            padx=10,
            pady=4,
        )
        self.btn_pin_current.pack(side="right", padx=(8, 0))
        attach_tooltip(self.btn_pin_current, "Aktuelles Ziel dauerhaft oben in der Schnellauswahl fixieren")

        self.lbl_cat_badge = tk.Label(
            sel_bar,
            text="",
            font=("Segoe UI", 8, "bold"),
            bg="#eff6ff",
            fg="#1d4ed8",
            padx=8,
            pady=3,
        )
        self.lbl_cat_badge.pack(side="right", padx=(8, 0))

        self.ent_target_wrap = tk.Frame(
            sel_bar,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            padx=8,
            pady=4,
        )
        self.ent_target_wrap.pack(side="left", fill="x", expand=True)

        self.ent_target = tk.Entry(
            self.ent_target_wrap,
            textvariable=self.target_var,
            font=("Segoe UI", 9),
            bg="#ffffff",
            fg="#0f172a",
            relief="flat",
            bd=0,
            highlightthickness=0,
        )
        self.ent_target.pack(fill="x", expand=True)
        self.ent_target.bind("<FocusIn>", lambda _e: self.ent_target_wrap.config(highlightbackground="#2563eb", highlightthickness=2))
        self.ent_target.bind("<FocusOut>", lambda _e: self.ent_target_wrap.config(highlightbackground="#cbd5e1", highlightthickness=1))

        # Mitte in Karte 2: Übersichtliche 7-Ordner-Kategorieliste (Kategorie LINKS für jedermann!)
        tv_wrap = tk.Frame(list_card, bg="#ffffff", highlightthickness=1, highlightbackground="#e2e8f0")
        tv_wrap.pack(fill="both", expand=True)

        tree_style = ttk.Style(self)
        try:
            if not tree_style.layout("Picker.Treeview"):
                tree_style.layout("Picker.Treeview", tree_style.layout("Treeview"))
            if not tree_style.layout("Picker.Treeview.Heading"):
                tree_style.layout("Picker.Treeview.Heading", tree_style.layout("Treeview.Heading"))
        except Exception:
            pass
        tree_style.configure(
            "Picker.Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground="#0f172a",
            rowheight=26,
            font=("Segoe UI", 9),
            borderwidth=0,
            relief="flat",
        )
        tree_style.map(
            "Picker.Treeview",
            background=[("selected", "#dbeafe")],
            foreground=[("selected", "#0f172a")],
        )
        tree_style.configure(
            "Picker.Treeview.Heading",
            background="#f8fafc",
            foreground="#475569",
            font=("Segoe UI", 9, "bold"),
            bordercolor="#e2e8f0",
            lightcolor="#e2e8f0",
            darkcolor="#e2e8f0",
            borderwidth=1,
            relief="solid",
            padding=(8, 5),
        )

        self.tree = ttk.Treeview(
            tv_wrap,
            columns=("label", "target"),
            show="headings",
            selectmode="browse",
            height=7,
            style="Picker.Treeview",
        )
        self.tree.heading("label", text="Kategorie")
        self.tree.heading("target", text="Ordner im 7-Ordner-System")
        self.tree.column("label", width=260, anchor="w")
        self.tree.column("target", width=440, anchor="w")
        vs = ttk.Scrollbar(tv_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

        self._populate_category_tree()
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda _e: self._on_confirm())

        self.search_var.trace_add("write", lambda *_: self._populate_category_tree())
        self.target_var.trace_add("write", lambda *_: self._sync_target_preview_ui())

        self.bind("<Escape>", lambda _e: self._on_close())
        self.bind("<Return>", self._on_return_key)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._render_recent_slots()
        self._sync_target_preview_ui()

        self.update_idletasks()
        self.deiconify()
        self.lift()
        self.focus_set()

    def _on_return_key(self, _e=None):
        if self.focus_get() is self.ent_search:
            children = self.tree.get_children()
            if children:
                self.tree.selection_set(children[0])
                self.tree.focus(children[0])
                self._on_tree_select()
            return
        self._on_confirm()

    def _set_scope_all(self, apply_all: bool):
        self.apply_all_rows_var.set(bool(apply_all))
        self._update_scope_buttons()

    def _update_scope_buttons(self):
        if self.btn_scope_sel is None or self.btn_scope_all is None:
            return
        is_all = bool(self.apply_all_rows_var.get())
        active_kw = dict(bg="#2563eb", fg="#ffffff", hover_bg="#1d4ed8", border_color=None, font=("Segoe UI", 9, "bold"))
        neutral_kw = dict(bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"))
        self.btn_scope_all.update_style(**(active_kw if is_all else neutral_kw))
        self.btn_scope_sel.update_style(**(neutral_kw if is_all else active_kw))

    def _update_rule_state(self):
        active = bool(self.save_rule_var.get())
        self.lbl_kw.config(fg="#0f172a" if active else "#94a3b8")
        self.ent_keyword.config(
            state="normal" if active else "disabled",
            fg="#0f172a" if active else "#94a3b8",
            disabledbackground="#ffffff",
            disabledforeground="#94a3b8",
        )
        self.chk_apply_preview.config(state="normal" if active else "disabled", fg="#334155" if active else "#94a3b8")

    def _populate_category_tree(self):
        q = self.search_var.get().strip().lower()
        for rid in self.tree.get_children():
            self.tree.delete(rid)
        self._row_map.clear()

        cur_tgt = self.target_var.get().strip().lower()
        select_rid = None
        for tgt, lbl in self._all_target_options:
            if q and (q not in lbl.lower() and q not in tgt.lower()):
                continue
            disp_tgt = os.path.normpath(tgt) if os.path.isabs(tgt) else tgt.replace("/", " \\ ")
            rid = self.tree.insert("", "end", values=(lbl, disp_tgt))
            self._row_map[rid] = (tgt, lbl)
            if tgt.lower() == cur_tgt or disp_tgt.lower() == cur_tgt:
                select_rid = rid

        if select_rid:
            self.tree.selection_set(select_rid)
            self.tree.see(select_rid)

    def _on_tree_select(self, _e=None):
        sel = self.tree.selection()
        if sel and sel[0] in self._row_map:
            t_val, l_val = self._row_map[sel[0]]
            self._updating_target_internally = True
            self.target_var.set(t_val)
            self.label_var.set(l_val)
            self._updating_target_internally = False
            self._sync_target_preview_ui()

    def _is_drive_or_usb(self, path: str) -> bool:
        p = str(path or "").strip()
        if ":\\" in p:
            drive = p[0].upper()
            if drive in "EFGHIJKLMNOPQRSTUVWXYZ" or "usb" in p.lower():
                return True
        return False

    def _sync_target_preview_ui(self):
        raw = self.target_var.get().strip()
        if not self._updating_target_internally:
            # Automatisch passende Kategorie ableiten
            norm_rel = raw.replace("\\", "/")
            if norm_rel in self._target_to_label:
                self.label_var.set(self._target_to_label[norm_rel])
            elif raw in self._target_to_label:
                self.label_var.set(self._target_to_label[raw])
            elif raw:
                base = os.path.basename(raw.rstrip("\\/")) or raw
                self.label_var.set(base)

        lbl = self.label_var.get().strip() or "Eigener Ordner"
        short_lbl = lbl if len(lbl) <= 22 else lbl[:20] + "…"
        self.lbl_cat_badge.config(text=short_lbl)

        ico = self._ico_usb_bar if self._is_drive_or_usb(raw) else self._ico_folder_bar
        self.lbl_target_ico.config(image=ico)
        self._highlight_matching_slot(raw)

    def _highlight_matching_slot(self, raw_target: str):
        cur_norm = os.path.normpath(raw_target).lower() if raw_target else ""
        for sw in self._slot_widgets:
            sp_norm = os.path.normpath(sw["path"]).lower()
            is_sel = bool(cur_norm and cur_norm == sp_norm)
            sw["set_selected"](is_sel)

    def _render_recent_slots(self):
        for w in self.slots_frame.winfo_children():
            w.destroy()
        self._slot_widgets.clear()

        recent = getattr(self._parent_app, "recent_target_folders", [])
        for idx in range(5):
            slot_data = recent[idx] if idx < len(recent) else None
            self._build_slot_card(idx, slot_data)
        self._highlight_matching_slot(self.target_var.get().strip())

    def _build_slot_card(self, idx: int, slot: dict | None):
        pad_l = 0 if idx == 0 else 8

        if not slot:
            # Einladender, klickbarer freier Slot zum direkten Favoriten-Fixieren!
            box = tk.Frame(
                self.slots_frame,
                bg="#f8fafc",
                highlightthickness=1,
                highlightbackground="#e2e8f0",
                padx=8,
                pady=8,
                cursor="hand2",
            )
            box.grid(row=0, column=idx, sticky="nsew", padx=(pad_l, 0))

            ico_plus = get_fluent_menu_icon_photo("plus", size=18)
            lbl_p = tk.Label(box, image=ico_plus, bg="#f8fafc", cursor="hand2")
            lbl_p.image = ico_plus
            lbl_p.pack(pady=(2, 3))

            lbl_t = tk.Label(box, text="Ordner fixieren", font=("Segoe UI", 8, "bold"), bg="#f8fafc", fg="#2563eb", cursor="hand2")
            lbl_t.pack()

            lbl_s = tk.Label(box, text="oder automatisch", font=("Segoe UI", 7), bg="#f8fafc", fg="#94a3b8", cursor="hand2")
            lbl_s.pack(pady=(1, 0))

            def _on_enter(_e=None, b=box, ws=(box, lbl_p, lbl_t, lbl_s)):
                b.config(bg="#eff6ff", highlightbackground="#93c5fd")
                for cw in ws:
                    cw.config(bg="#eff6ff")

            def _on_leave(_e=None, b=box, ws=(box, lbl_p, lbl_t, lbl_s)):
                b.config(bg="#f8fafc", highlightbackground="#e2e8f0")
                for cw in ws:
                    cw.config(bg="#f8fafc")

            for w in (box, lbl_p, lbl_t, lbl_s):
                w.bind("<Enter>", _on_enter)
                w.bind("<Leave>", _on_leave)
                w.bind("<Button-1>", lambda _e: self._add_and_pin_new_slot())
            attach_tooltip(box, "Klicken, um einen Wunschordner dauerhaft hier zu fixieren.\nSonst wird dieser Platz automatisch mit zuletzt genutzten Ordnern belegt.")
            return

        p = slot["path"]
        lbl = slot.get("label") or os.path.basename(p.rstrip("\\/")) or p
        pinned = bool(slot.get("pinned", False))
        is_last = (idx == 0)

        box = tk.Frame(
            self.slots_frame,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            padx=9,
            pady=7,
            cursor="hand2",
        )
        box.grid(row=0, column=idx, sticky="nsew", padx=(pad_l, 0))

        # Obere Zeile: Status-Badge links + Pin & Entfernen rechts
        top_row = tk.Frame(box, bg="#ffffff")
        top_row.pack(fill="x")

        if is_last:
            badge_bg, badge_fg, badge_txt = "#dbeafe", "#1d4ed8", "Zuletzt"
        elif pinned:
            badge_bg, badge_fg, badge_txt = "#fef3c7", "#b45309", "Fixiert"
        else:
            badge_bg, badge_fg, badge_txt = "#f1f5f9", "#64748b", "Verlauf"

        lbl_badge = tk.Label(
            top_row,
            text=badge_txt,
            font=("Segoe UI", 7, "bold"),
            bg=badge_bg,
            fg=badge_fg,
            padx=5,
            pady=1,
        )
        lbl_badge.pack(side="left")

        ico_rm_norm = get_fluent_menu_icon_photo("cross", size=12)
        ico_rm_hov = get_fluent_menu_icon_photo("cross_danger", size=12)
        btn_rm = tk.Label(top_row, image=ico_rm_norm, bg="#ffffff", cursor="hand2", padx=2)
        btn_rm.image = ico_rm_norm
        btn_rm.pack(side="right", padx=(2, 0))
        attach_tooltip(btn_rm, "Aus Schnellauswahl entfernen")
        btn_rm.bind("<Enter>", lambda _e, b=btn_rm, im=ico_rm_hov: b.config(image=im))
        btn_rm.bind("<Leave>", lambda _e, b=btn_rm, im=ico_rm_norm: b.config(image=im))

        def _do_remove(_e=None, sp=p):
            self._on_remove_slot(sp)
            return "break"

        btn_rm.bind("<Button-1>", _do_remove)

        ico_pin = get_fluent_menu_icon_photo("pin" if pinned else "pin_outline", size=13)
        pin_tip = "Fixiert – bleibt dauerhaft erhalten.\nKlicken zum Lösen" if pinned else "Nicht fixiert – aktualisiert sich automatisch.\nKlicken zum dauerhaften Fixieren"
        btn_pin = tk.Label(top_row, image=ico_pin, bg="#ffffff", cursor="hand2", padx=2)
        btn_pin.image = ico_pin
        btn_pin.pack(side="right")
        attach_tooltip(btn_pin, pin_tip)

        def _do_pin(_e=None, sp=p):
            self._on_toggle_pin(sp)
            return "break"

        btn_pin.bind("<Button-1>", _do_pin)

        # Mittlere Zeile: Vektor-Icon + Ordnername
        mid_row = tk.Frame(box, bg="#ffffff")
        mid_row.pack(fill="x", pady=(6, 2))

        ico_main = get_fluent_menu_icon_photo("usb" if self._is_drive_or_usb(p) else "folder", size=15)
        lbl_ico = tk.Label(mid_row, image=ico_main, bg="#ffffff")
        lbl_ico.image = ico_main
        lbl_ico.pack(side="left", padx=(0, 5))

        short_name = lbl if len(lbl) <= 15 else lbl[:13] + "…"
        lbl_name = tk.Label(
            mid_row,
            text=short_name,
            font=("Segoe UI", 9, "bold"),
            bg="#ffffff",
            fg="#0f172a",
            anchor="w",
        )
        lbl_name.pack(side="left", fill="x", expand=True)

        # Untere Zeile: Gekürzter Pfad
        disp_p = p if len(p) <= 22 else p[:8] + "…" + p[-12:]
        lbl_path = tk.Label(
            box,
            text=disp_p,
            font=("Segoe UI", 8),
            bg="#ffffff",
            fg="#64748b",
            anchor="w",
        )
        lbl_path.pack(fill="x")
        attach_tooltip(lbl_path, p)
        attach_tooltip(lbl_name, f"{lbl}\n{p}")

        bg_widgets = (box, top_row, btn_pin, btn_rm, mid_row, lbl_ico, lbl_name, lbl_path)
        state = {"selected": False}

        def _apply_bg(col: str, brd: str, th: int):
            box.config(bg=col, highlightbackground=brd, highlightthickness=th)
            for cw in bg_widgets[1:]:
                cw.config(bg=col)

        def _set_selected(sel: bool):
            state["selected"] = sel
            if sel:
                _apply_bg("#eff6ff", "#2563eb", 2)
            else:
                _apply_bg("#ffffff", "#cbd5e1", 1)

        def _on_enter(_e=None):
            if not state["selected"]:
                _apply_bg("#f8fafc", "#94a3b8", 1)

        def _on_leave(_e=None):
            if not state["selected"]:
                _apply_bg("#ffffff", "#cbd5e1", 1)

        def _select(_e=None):
            self._updating_target_internally = True
            self.target_var.set(os.path.normpath(p) if os.path.isabs(p) else p)
            self.label_var.set(lbl)
            self._updating_target_internally = False
            self._sync_target_preview_ui()

        def _confirm_now(_e=None):
            _select()
            self._on_confirm()

        for w in (box, top_row, lbl_badge, mid_row, lbl_ico, lbl_name, lbl_path):
            w.bind("<Enter>", _on_enter)
            w.bind("<Leave>", _on_leave)
            w.bind("<Button-1>", _select)
            w.bind("<Double-Button-1>", _confirm_now)

        self._slot_widgets.append({"path": p, "set_selected": _set_selected})

    def _add_and_pin_new_slot(self):
        init_dir = ""
        if hasattr(self._parent_app, "target_dir"):
            td = self._parent_app.target_dir.get().strip()
            if td and os.path.isdir(td):
                init_dir = td
        chosen = filedialog.askdirectory(
            parent=self,
            initialdir=init_dir or os.path.expanduser("~"),
            title="Ordner für Schnellauswahl wählen & dauerhaft fixieren",
        )
        if not chosen:
            return
        norm = os.path.normpath(chosen)
        folder_name = os.path.basename(norm.rstrip("\\/")) or norm
        self._parent_app.add_recent_target_folder(norm, pinned=True, label=folder_name, keep_slot1=True)
        self._updating_target_internally = True
        self.target_var.set(norm)
        self.label_var.set(folder_name)
        self._updating_target_internally = False
        self._render_recent_slots()
        self._sync_target_preview_ui()

    def _pin_current_target(self):
        raw_tgt = self.target_var.get().strip()
        if not raw_tgt:
            return
        tgt = os.path.normpath(raw_tgt) if os.path.isabs(raw_tgt) else raw_tgt.replace("\\", "/")
        lbl = self.label_var.get().strip() or os.path.basename(tgt.rstrip("/\\")) or tgt
        self._parent_app.add_recent_target_folder(tgt, pinned=True, label=lbl, keep_slot1=True)
        self._render_recent_slots()
        self._sync_target_preview_ui()
        try:
            self.btn_pin_current.update_style(text="✓ Angepinnt", bg="#eff6ff", fg="#1d4ed8", border_color="#93c5fd")
            self.after(
                1200,
                lambda: self.btn_pin_current.winfo_exists()
                and self.btn_pin_current.update_style(text="📌 Anpinnen", bg="#ffffff", fg="#1e293b", border_color="#94a3b8"),
            )
        except Exception:
            pass

    def _on_toggle_pin(self, path: str):
        self._parent_app.toggle_pin_recent_target(path)
        self._render_recent_slots()

    def _on_remove_slot(self, path: str):
        if hasattr(self._parent_app, "remove_recent_target_folder"):
            self._parent_app.remove_recent_target_folder(path)
        self._render_recent_slots()

    def _pick_main_target_folder(self):
        main_t = self._parent_app.target_dir.get().strip() if hasattr(self._parent_app, "target_dir") else ""
        if not main_t:
            return
        norm = os.path.normpath(main_t)
        folder_name = os.path.basename(norm.rstrip("\\/")) or norm
        self._updating_target_internally = True
        self.target_var.set(norm)
        self.label_var.set(folder_name)
        self._updating_target_internally = False
        self._sync_target_preview_ui()

    def _browse_custom_folder(self):
        cur = self.target_var.get().strip()
        init_dir = ""
        if cur and os.path.isabs(cur) and os.path.isdir(cur):
            init_dir = cur
        elif hasattr(self._parent_app, "target_dir"):
            td = self._parent_app.target_dir.get().strip()
            if td and os.path.isdir(td):
                init_dir = td
        chosen = filedialog.askdirectory(
            parent=self,
            initialdir=init_dir or os.path.expanduser("~"),
            title="Speicher-Ordner für ausgewählte Dateien wählen",
        )
        if not chosen:
            return
        norm = os.path.normpath(chosen)
        folder_name = os.path.basename(norm.rstrip("\\/")) or norm
        self._updating_target_internally = True
        self.target_var.set(norm)
        self.label_var.set(folder_name)
        self._updating_target_internally = False
        self._sync_target_preview_ui()

    def _pick_usb_stick_folder(self):
        usb_dir = github_sync.find_usb_stick_data_dir()
        if not usb_dir:
            messagebox.showwarning(
                "USB-Stick nicht gefunden",
                "Kein eingesteckter USB-Stick mit Ordner 'USB_Stick' gefunden (E:, F:, G: ...).",
                parent=self,
            )
            return
        norm = os.path.normpath(usb_dir)
        self._updating_target_internally = True
        self.target_var.set(norm)
        self.label_var.set("USB_Stick")
        self._updating_target_internally = False
        self._sync_target_preview_ui()

    def _on_confirm(self, execute_mode: str | None = None):
        raw_tgt = self.target_var.get().strip()
        if not raw_tgt:
            return
        tgt = os.path.normpath(raw_tgt) if os.path.isabs(raw_tgt) else raw_tgt.replace("\\", "/")
        lbl = self.label_var.get().strip() or os.path.basename(tgt.rstrip("/\\")) or tgt
        self.result = {
            "target_sub": tgt,
            "label": lbl,
            "save_rule": bool(self.save_rule_var.get()),
            "keyword": self.keyword_var.get().strip(),
            "apply_to_preview": bool(self.apply_preview_var.get()),
            "apply_to_all": bool(self.apply_all_rows_var.get()),
            "execute_mode": execute_mode,
        }
        self._parent_app.add_recent_target_folder(tgt, label=lbl)
        self._on_close()

    def _on_close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def make_flat_btn(parent, text, command=None, bg="#f1f5f9", fg="#0f172a", hover_bg="#e2e8f0",
                  font=("Segoe UI", 9), padx=12, pady=6):
    btn = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=hover_bg,
        activeforeground=fg,
        font=font,
        relief="flat",
        bd=0,
        padx=padx,
        pady=pady,
        cursor="hand2",
        highlightthickness=0
    )
    btn._default_bg = bg
    btn._hover_bg = hover_bg
    btn._default_fg = fg

    def on_enter(e):
        btn.config(bg=btn._hover_bg)

    def on_leave(e):
        btn.config(bg=btn._default_bg)

    btn.bind("<Enter>", on_enter)
    btn.bind("<Leave>", on_leave)
    return btn


def _get_windows_drives() -> list[Path]:
    """Ermittelt verfuegbare Laufwerke schnell ueber die Win32 API."""
    if os.name == "nt":
        try:
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            drives = []
            for idx in range(26):
                if mask & (1 << idx):
                    letter = chr(65 + idx)
                    drives.append(Path(f"{letter}:\\"))
            if drives:
                return drives
        except Exception:
            pass
    return [Path(os.path.abspath(os.sep))]


def _format_file_size(num_bytes: int) -> str:
    """Formatiert Dateigroessen kompakt fuer die Ordnerauswahl."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{max(1, num_bytes // 1024)} KB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB".replace(".", ",")
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB".replace(".", ",")


class FolderPickerDialog(tk.Toplevel):
    """Moderner Ordner-Auswahldialog, der neben Unterordnern auch enthaltene Dateien anzeigt."""

    def __init__(self, parent, title: str, initialdir: str):
        super().__init__(parent)
        self.title(title)
        self.configure(bg="#f8fafc")
        self.transient(parent)
        apply_window_folder_icon(self)
        self.grab_set()
        self.minsize(740, 480)

        self.result: str | None = None
        start_path = Path(initialdir) if initialdir else Path.home()
        if not start_path.exists() or not start_path.is_dir():
            start_path = Path.home()
        self.current_dir = start_path.resolve()

        self._tree_paths: dict[str, Path] = {}
        self._tree_loaded: set[str] = set()
        self._right_entries: dict[str, tuple[str, Path]] = {}
        self._ignore_tree_select = False

        self.path_var = tk.StringVar(value=str(self.current_dir))
        self.selected_var = tk.StringVar(value=str(self.current_dir))
        self._onedrive_accounts = get_onedrive_accounts()

        self._build_ui()
        self._center_over_parent(parent, 960, 640)
        self._populate_left_tree()
        self._navigate_to(self.current_dir, sync_tree=True)

        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _center_over_parent(self, parent, width: int, height: int):
        self.update_idletasks()
        sw = max(800, self.winfo_screenwidth())
        sh = max(600, self.winfo_screenheight())
        w = min(width, sw - 60)
        h = min(height, sh - 80)
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - w) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 2)
        px = max(20, min(px, sw - w - 20))
        py = max(20, min(py, sh - h - 40))
        self.geometry(f"{w}x{h}+{px}+{py}")

    def _configure_picker_styles(self):
        picker_style = ttk.Style(self)
        try:
            if not picker_style.layout("Picker.Treeview"):
                picker_style.layout("Picker.Treeview", picker_style.layout("Treeview"))
            if not picker_style.layout("Picker.Treeview.Heading"):
                picker_style.layout("Picker.Treeview.Heading", picker_style.layout("Treeview.Heading"))
        except Exception:
            pass
        picker_style.configure(
            "Picker.Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground="#0f172a",
            rowheight=26,
            font=("Segoe UI", 9),
            borderwidth=0,
            relief="flat",
        )
        picker_style.configure(
            "Picker.Treeview.Heading",
            background="#f8fafc",
            foreground="#475569",
            font=("Segoe UI", 9),
            bordercolor="#e2e8f0",
            lightcolor="#e2e8f0",
            darkcolor="#e2e8f0",
            borderwidth=1,
            relief="solid",
            padding=(6, 5),
        )
        picker_style.map(
            "Picker.Treeview.Heading",
            background=[("active", "#f1f5f9"), ("pressed", "#e2e8f0")],
        )

    def _build_ui(self):
        self._configure_picker_styles()
        btn_kw = dict(
            bg="#ffffff",
            fg="#334155",
            hover_bg="#f1f5f9",
            border_color="#cbd5e1",
            font=("Segoe UI", 9),
            r=6,
            padx=9,
            pady=4,
        )

        # 1. Schnellzugriff-Leiste (Benutzerordner & Laufwerke)
        quick_bar = tk.Frame(self, bg="#f8fafc", padx=14, pady=0)
        quick_bar.pack(fill="x", pady=(10, 4))

        tk.Label(
            quick_bar,
            text="Schnellzugriff:",
            font=("Segoe UI", 9),
            width=12,
            anchor="w",
            bg="#f8fafc",
            fg="#475569",
        ).pack(side="left", padx=(0, 8))

        home = Path.home()
        shortcuts = [
            ("Desktop", home / "Desktop"),
            ("Dokumente", home / "Documents"),
            ("Bilder", home / "Pictures"),
            ("Downloads", home / "Downloads"),
            ("Benutzer", home),
        ]
        for label, p in shortcuts:
            if p.exists() and p.is_dir():
                ModernRoundedButton(
                    quick_bar,
                    text=label,
                    command=lambda target=p: self._navigate_to(target, sync_tree=True),
                    **btn_kw,
                ).pack(side="left", padx=(0, 4))

        for drv in _get_windows_drives():
            is_net, _ = get_drive_display_info(drv)
            drv_txt = f"🌐 {str(drv).rstrip(os.sep)}" if is_net else str(drv).rstrip("\\")
            ModernRoundedButton(
                quick_bar,
                text=drv_txt,
                command=lambda target=drv: self._navigate_to(target, sync_tree=True),
                **btn_kw,
            ).pack(side="left", padx=(0, 4))

        # 2. Direktwahl-Leiste (Netzwerk, OneDrive Privat / Firma, Einloggen)
        direct_bar = tk.Frame(self, bg="#f8fafc", padx=14, pady=0)
        direct_bar.pack(fill="x", pady=(0, 8))

        tk.Label(
            direct_bar,
            text="Direktwahl:",
            font=("Segoe UI", 9),
            width=12,
            anchor="w",
            bg="#f8fafc",
            fg="#475569",
        ).pack(side="left", padx=(0, 8))

        self.btn_pick_network = ModernRoundedButton(
            direct_bar,
            text="Netzwerkpfad…",
            command=self._prompt_network_path,
            icon_kind="network_globe",
            **btn_kw,
        )
        self.btn_pick_network.pack(side="left", padx=(0, 4))
        attach_tooltip(self.btn_pick_network, "Firmen- oder Serverordner (z. B. \\\\Server\\Freigabe) direkt öffnen")

        self.btn_pick_onedrive_personal = ModernRoundedButton(
            direct_bar,
            text="OneDrive Privat",
            command=lambda: self._open_onedrive_kind("personal"),
            icon_kind="cloud_onedrive",
            **btn_kw,
        )
        self.btn_pick_onedrive_personal.pack(side="left", padx=(0, 4))

        self.btn_pick_onedrive_business = ModernRoundedButton(
            direct_bar,
            text="OneDrive Firma",
            command=lambda: self._open_onedrive_kind("business"),
            icon_kind="cloud_onedrive_biz",
            **btn_kw,
        )
        self.btn_pick_onedrive_business.pack(side="left", padx=(0, 4))

        self.btn_pick_login = ModernRoundedButton(
            direct_bar,
            text="Einloggen",
            command=self._open_login_dialog,
            icon_kind="key_login",
            **btn_kw,
        )
        self.btn_pick_login.pack(side="left", padx=(0, 0))
        attach_tooltip(self.btn_pick_login, "Bei Microsoft OneDrive (Privat / Firma) oder einem Netzlaufwerk anmelden")
        self._refresh_onedrive_button_hints()

        # 3. Pfad-Leiste
        nav_bar = tk.Frame(self, bg="#f8fafc")
        nav_bar.pack(fill="x", padx=14, pady=(0, 8))

        ModernRoundedButton(
            nav_bar,
            text="⬆ Hoch",
            command=self._go_up,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=6,
            padx=10,
            pady=4,
        ).pack(side="left", padx=(0, 6))

        self.path_entry = tk.Entry(
            nav_bar,
            textvariable=self.path_var,
            font=("Segoe UI", 10),
            bg="#ffffff",
            fg="#0f172a",
            relief="solid",
            bd=1,
        )
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=4)
        self.path_entry.bind("<Return>", lambda e: self._open_typed_path())

        ModernRoundedButton(
            nav_bar,
            text="Öffnen",
            command=self._open_typed_path,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=6,
            padx=10,
            pady=4,
        ).pack(side="left", padx=(0, 6))

        ModernRoundedButton(
            nav_bar,
            text="+ Neuer Ordner",
            command=self._create_new_folder,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=6,
            padx=10,
            pady=4,
        ).pack(side="left")

        # 4. Fusszeile ZUERST am unteren Rand packen (verhindert Abschneiden bei Skalierung)
        footer = tk.Frame(self, bg="#f8fafc")
        footer.pack(side="bottom", fill="x", padx=14, pady=(4, 12))

        tk.Label(footer, text="Ausgewählter Ordner:", font=("Segoe UI", 9), bg="#f8fafc", fg="#475569").pack(side="left", padx=(0, 6))
        tk.Label(
            footer,
            textvariable=self.selected_var,
            font=("Segoe UI", 9),
            bg="#ffffff",
            fg="#1d4ed8",
            anchor="w",
            padx=8,
            pady=5,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
        ).pack(side="left", fill="x", expand=True, padx=(0, 12))

        ModernRoundedButton(
            footer,
            text="✓ Diesen Ordner auswählen",
            command=self._confirm,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            font=("Segoe UI", 9),
            r=8,
            padx=14,
            pady=6,
        ).pack(side="right")

        ModernRoundedButton(
            footer,
            text="Abbrechen",
            command=self._cancel,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=6,
        ).pack(side="right", padx=(0, 8))

        # 5. Split-Bereich: Links Ordnerbaum, Rechts Ordner + Dateien
        paned = tk.PanedWindow(self, orient="horizontal", bg="#e2e8f0", sashwidth=5, bd=0)
        paned.pack(fill="both", expand=True, padx=14, pady=(0, 6))

        left_wrap = tk.Frame(paned, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1")
        self.dir_tree = ttk.Treeview(left_wrap, show="tree", selectmode="browse", style="Picker.Treeview")
        left_scroll = ttk.Scrollbar(left_wrap, orient="vertical", command=self.dir_tree.yview)
        self.dir_tree.configure(yscrollcommand=left_scroll.set)
        self.dir_tree.pack(side="left", fill="both", expand=True)
        left_scroll.pack(side="right", fill="y")
        self.dir_tree.bind("<<TreeviewOpen>>", self._on_left_tree_open)
        self.dir_tree.bind("<<TreeviewSelect>>", self._on_left_tree_select)
        paned.add(left_wrap, minsize=210, width=250)

        right_wrap = tk.Frame(paned, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1")
        info_strip = tk.Frame(right_wrap, bg="#f1f5f9", padx=10, pady=6)
        info_strip.pack(fill="x")
        self.lbl_dir_stats = tk.Label(info_strip, text="", font=("Segoe UI", 9), bg="#f1f5f9", fg="#334155")
        self.lbl_dir_stats.pack(side="left")
        tk.Label(
            info_strip,
            text="Dateien dienen zur Orientierung  •  Doppelklick öffnet Ordner",
            font=("Segoe UI", 8),
            bg="#f1f5f9",
            fg="#64748b",
        ).pack(side="right")

        cols = ("name", "type", "size", "date")
        self.content_tree = ttk.Treeview(right_wrap, columns=cols, show="headings", selectmode="browse", style="Picker.Treeview")
        self.content_tree.heading("name", text="Ordner / Datei")
        self.content_tree.heading("type", text="Typ")
        self.content_tree.heading("size", text="Größe")
        self.content_tree.heading("date", text="Änderungsdatum")
        self.content_tree.column("name", width=360, minwidth=180, anchor="w")
        self.content_tree.column("type", width=90, minwidth=60, anchor="center")
        self.content_tree.column("size", width=85, minwidth=60, anchor="e")
        self.content_tree.column("date", width=130, minwidth=100, anchor="center")

        self.content_tree.tag_configure("tag_dir", foreground="#0f172a", font=("Segoe UI", 9))
        self.content_tree.tag_configure("tag_doc", foreground="#0369a1", font=("Segoe UI", 9))
        self.content_tree.tag_configure("tag_other", foreground="#64748b", font=("Segoe UI", 9))

        right_scroll = ttk.Scrollbar(right_wrap, orient="vertical", command=self.content_tree.yview)
        self.content_tree.configure(yscrollcommand=right_scroll.set)
        self.content_tree.pack(side="left", fill="both", expand=True)
        right_scroll.pack(side="right", fill="y")

        self.content_tree.bind("<<TreeviewSelect>>", self._on_right_select)
        self.content_tree.bind("<Double-1>", self._on_right_double_click)
        self.content_tree.bind("<Return>", lambda e: self._on_right_double_click(e))
        paned.add(right_wrap, minsize=440)

    def _refresh_onedrive_button_hints(self):
        self._onedrive_accounts = get_onedrive_accounts()
        pers = self._onedrive_accounts.get("personal", [])
        biz = self._onedrive_accounts.get("business", [])
        if hasattr(self, "btn_pick_onedrive_personal"):
            if pers:
                attach_tooltip(self.btn_pick_onedrive_personal, f"OneDrive Privat öffnen:\n{pers[0][1]}")
            else:
                attach_tooltip(self.btn_pick_onedrive_personal, "OneDrive Privat ist noch nicht verbunden – Klicken zum Einrichten")
        if hasattr(self, "btn_pick_onedrive_business"):
            if biz:
                paths_txt = "\n".join(f"{lbl}: {p}" for lbl, p in biz[:3])
                attach_tooltip(self.btn_pick_onedrive_business, f"OneDrive Firma öffnen:\n{paths_txt}")
            else:
                attach_tooltip(self.btn_pick_onedrive_business, "OneDrive Firma (Microsoft 365) noch nicht verbunden – Klicken zum Einrichten")

    def _populate_left_tree(self):
        for iid in self.dir_tree.get_children():
            self.dir_tree.delete(iid)
        self._tree_paths.clear()
        self._tree_loaded.clear()
        for drv in _get_windows_drives():
            is_net, disp = get_drive_display_info(drv)
            icon = "🌐" if is_net else "💾"
            nid = self.dir_tree.insert("", "end", text=f"{icon} {disp}")
            self._tree_paths[nid] = drv
            self._add_dummy_child_if_has_dirs(nid, drv)

        self._onedrive_accounts = get_onedrive_accounts()
        seen_paths = {os.path.normpath(str(p)).lower() for p in self._tree_paths.values()}
        for lbl, p_pers in self._onedrive_accounts.get("personal", []):
            nid = self.dir_tree.insert("", "end", text=f"☁ {lbl}")
            self._tree_paths[nid] = p_pers
            seen_paths.add(os.path.normpath(str(p_pers)).lower())
            self._add_dummy_child_if_has_dirs(nid, p_pers)
        for lbl, p_biz in self._onedrive_accounts.get("business", []):
            nid = self.dir_tree.insert("", "end", text=f"🏢 {lbl}")
            self._tree_paths[nid] = p_biz
            seen_paths.add(os.path.normpath(str(p_biz)).lower())
            self._add_dummy_child_if_has_dirs(nid, p_biz)

        sm_saved, sm_net = load_suchmaschine_favorites()
        for lbl, p_fav in sm_saved:
            np = os.path.normpath(p_fav)
            if np.lower() in seen_paths or not os.path.isdir(np):
                continue
            seen_paths.add(np.lower())
            nid = self.dir_tree.insert("", "end", text=f"★ {lbl}")
            self._tree_paths[nid] = Path(np)
            self._add_dummy_child_if_has_dirs(nid, Path(np))
        for lbl, p_net in sm_net:
            np = os.path.normpath(p_net)
            if np.lower() in seen_paths or not os.path.isdir(np):
                continue
            seen_paths.add(np.lower())
            nid = self.dir_tree.insert("", "end", text=f"🌐 {lbl}")
            self._tree_paths[nid] = Path(np)
            self._add_dummy_child_if_has_dirs(nid, Path(np))

    def _open_onedrive_kind(self, kind: str):
        self._refresh_onedrive_button_hints()
        accounts = self._onedrive_accounts.get(kind, [])
        kind_title = "OneDrive Privat" if kind == "personal" else "OneDrive Firma"
        if not accounts:
            dlg = ModernConfirmDialog(
                self,
                title=f"{kind_title} nicht gefunden",
                headline=f"Es wurde noch kein lokaler „{kind_title}“-Ordner gefunden.",
                body=(
                    f"Möchten Sie sich jetzt bei {kind_title} anmelden, damit der Ordner hier unter „Direktwahl“ erscheint?"
                    if kind == "personal"
                    else "Wenn Ihre Firma Microsoft 365 / SharePoint nutzt, können Sie sich jetzt über das OneDrive-Symbol einloggen."
                ),
                yes_text="🔑 Jetzt einloggen",
                no_text="Abbrechen",
            )
            self.wait_window(dlg)
            if dlg.result:
                self._open_login_dialog(focus_kind=kind)
            return

        if len(accounts) == 1:
            self._navigate_to(accounts[0][1], sync_tree=True)
            return

        # Mehrere Firmen-/SharePoint-Konten: sauberes Auswahlfenster
        dlg = tk.Toplevel(self)
        dlg.title(f"{kind_title} auswählen")
        dlg.configure(bg="#f8fafc")
        dlg.transient(self)
        dlg.grab_set()
        self.update_idletasks()
        dw, dh = 480, 260
        px = self.winfo_rootx() + max(0, (self.winfo_width() - dw) // 2)
        py = self.winfo_rooty() + max(0, (self.winfo_height() - dh) // 2)
        dlg.geometry(f"{dw}x{dh}+{px}+{py}")

        card = tk.Frame(dlg, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1", padx=16, pady=14)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(card, text=f"{kind_title} – Konto / Ordner wählen", font=("Segoe UI", 10), bg="#ffffff", fg="#0f172a").pack(anchor="w", pady=(0, 8))

        for lbl, p in accounts:
            ModernRoundedButton(
                card,
                text=f"{lbl}  ({p})",
                command=lambda target=p, win=dlg: (win.destroy(), self._navigate_to(target, sync_tree=True)),
                bg="#ffffff",
                fg="#334155",
                hover_bg="#f1f5f9",
                border_color="#cbd5e1",
                font=("Segoe UI", 9),
                r=6,
                padx=10,
                pady=6,
            ).pack(fill="x", pady=(0, 6))

        ModernRoundedButton(
            card,
            text="Abbrechen",
            command=dlg.destroy,
            bg="#ffffff",
            fg="#475569",
            hover_bg="#f1f5f9",
            border_color="#cbd5e1",
            font=("Segoe UI", 9),
            r=6,
            padx=12,
            pady=4,
        ).pack(side="right", pady=(6, 0))

    def _open_login_dialog(self, focus_kind: str = ""):
        """Light-Fluent-Dialog fuer OneDrive-Anmeldung (Privat & Firma) sowie Firmen-Netzlaufwerke."""
        dlg = tk.Toplevel(self)
        dlg.title("Cloud & Netzwerk – Einloggen")
        dlg.configure(bg="#f8fafc")
        dlg.transient(self)
        dlg.grab_set()
        self.update_idletasks()
        dw, dh = 560, 440
        px = self.winfo_rootx() + max(0, (self.winfo_width() - dw) // 2)
        py = self.winfo_rooty() + max(0, (self.winfo_height() - dh) // 2)
        dlg.geometry(f"{dw}x{dh}+{px}+{py}")
        dlg.minsize(500, 390)

        outer = tk.Frame(dlg, bg="#f8fafc", padx=14, pady=14)
        outer.pack(fill="both", expand=True)

        # Karte 1: Microsoft OneDrive Privat & Firma
        od_card = tk.Frame(outer, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1", padx=14, pady=12)
        od_card.pack(fill="x", pady=(0, 10))

        tk.Label(od_card, text="☁ Microsoft OneDrive (Privat & Firma)", font=("Segoe UI", 10), bg="#ffffff", fg="#0f172a").pack(anchor="w")
        tk.Label(
            od_card,
            text="Hier sehen Sie Ihre verbundenen OneDrive-Ordner oder können sich direkt anmelden:",
            font=("Segoe UI", 9),
            bg="#ffffff",
            fg="#475569",
        ).pack(anchor="w", pady=(2, 8))

        status_frame = tk.Frame(od_card, bg="#f8fafc", highlightthickness=1, highlightbackground="#e2e8f0", padx=10, pady=8)
        status_frame.pack(fill="x", pady=(0, 10))

        def _render_status():
            for ch in status_frame.winfo_children():
                ch.destroy()
            self._refresh_onedrive_button_hints()
            pers = self._onedrive_accounts.get("personal", [])
            biz = self._onedrive_accounts.get("business", [])

            row_p = tk.Frame(status_frame, bg="#f8fafc")
            row_p.pack(fill="x", pady=(0, 4))
            p_txt = f"☁ Privat: ✓ {pers[0][1]}" if pers else "☁ Privat: noch nicht verbunden"
            tk.Label(row_p, text=p_txt, font=("Segoe UI", 9), bg="#f8fafc", fg="#0f172a" if pers else "#64748b", anchor="w").pack(side="left", fill="x", expand=True)
            if pers:
                ModernRoundedButton(
                    row_p,
                    text="Öffnen",
                    command=lambda target=pers[0][1]: (dlg.destroy(), self._navigate_to(target, sync_tree=True)),
                    bg="#ffffff",
                    fg="#2563eb",
                    hover_bg="#eff6ff",
                    border_color="#bfdbfe",
                    font=("Segoe UI", 8),
                    r=6,
                    padx=8,
                    pady=2,
                ).pack(side="right")

            row_b = tk.Frame(status_frame, bg="#f8fafc")
            row_b.pack(fill="x")
            b_txt = f"🏢 Firma: ✓ {biz[0][0]} ({biz[0][1]})" if biz else "🏢 Firma: noch nicht verbunden"
            tk.Label(row_b, text=b_txt, font=("Segoe UI", 9), bg="#f8fafc", fg="#0f172a" if biz else "#64748b", anchor="w").pack(side="left", fill="x", expand=True)
            if biz:
                ModernRoundedButton(
                    row_b,
                    text="Öffnen",
                    command=lambda target=biz[0][1]: (dlg.destroy(), self._navigate_to(target, sync_tree=True)),
                    bg="#ffffff",
                    fg="#2563eb",
                    hover_bg="#eff6ff",
                    border_color="#bfdbfe",
                    font=("Segoe UI", 8),
                    r=6,
                    padx=8,
                    pady=2,
                ).pack(side="right")

        _render_status()

        od_msg_var = tk.StringVar(
            value="Klicken Sie unten auf „Bei OneDrive anmelden“, um ein Privat- oder Firmenkonto hinzuzufügen."
            if not focus_kind
            else f"Melden Sie sich mit Ihrem {'Privat-' if focus_kind == 'personal' else 'Firmen-'}Konto in Windows OneDrive an."
        )
        tk.Label(od_card, textvariable=od_msg_var, font=("Segoe UI", 8), bg="#ffffff", fg="#64748b", anchor="w", justify="left", wraplength=500).pack(fill="x", pady=(0, 8))

        od_btns = tk.Frame(od_card, bg="#ffffff")
        od_btns.pack(fill="x")

        def _on_launch_od():
            ok, msg = launch_onedrive_signin()
            od_msg_var.set(msg)
            if not ok:
                notice = ModernNoticeDialog(dlg, "OneDrive", "Hinweis zu OneDrive", msg, kind="warning")
                dlg.wait_window(notice)

        ModernRoundedButton(
            od_btns,
            text="🔑 Bei OneDrive anmelden / Konto hinzufügen",
            command=_on_launch_od,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            font=("Segoe UI", 9),
            r=8,
            padx=12,
            pady=5,
        ).pack(side="left")

        ModernRoundedButton(
            od_btns,
            text="↻ Status aktualisieren",
            command=lambda: (_render_status(), self._populate_left_tree()),
            bg="#ffffff",
            fg="#334155",
            hover_bg="#f1f5f9",
            border_color="#cbd5e1",
            font=("Segoe UI", 9),
            r=8,
            padx=10,
            pady=5,
        ).pack(side="right")

        # Karte 2: Firmen-Netzlaufwerk / Server verbinden
        net_card = tk.Frame(outer, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1", padx=14, pady=12)
        net_card.pack(fill="x")

        tk.Label(net_card, text="🌐 Firmen-Netzlaufwerk / Server mit Benutzerdaten verbinden", font=("Segoe UI", 10), bg="#ffffff", fg="#0f172a").pack(anchor="w", pady=(0, 6))

        cur_val = self.path_var.get().strip()
        net_path_var = tk.StringVar(value=cur_val if is_network_path(cur_val) else "\\\\")
        net_user_var = tk.StringVar(value="")
        net_pass_var = tk.StringVar(value="")
        net_err_var = tk.StringVar(value="")

        g1 = tk.Frame(net_card, bg="#ffffff")
        g1.pack(fill="x", pady=(0, 6))
        tk.Label(g1, text="Netzwerkpfad:", width=13, anchor="w", font=("Segoe UI", 9), bg="#ffffff", fg="#475569").pack(side="left")
        ent_unc = tk.Entry(g1, textvariable=net_path_var, font=("Segoe UI", 9), bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        ent_unc.pack(side="left", fill="x", expand=True, ipady=3)

        g2 = tk.Frame(net_card, bg="#ffffff")
        g2.pack(fill="x", pady=(0, 6))
        tk.Label(g2, text="Benutzername:", width=13, anchor="w", font=("Segoe UI", 9), bg="#ffffff", fg="#475569").pack(side="left")
        tk.Entry(g2, textvariable=net_user_var, font=("Segoe UI", 9), bg="#ffffff", fg="#0f172a", relief="solid", bd=1).pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=3)
        tk.Label(g2, text="Passwort:", font=("Segoe UI", 9), bg="#ffffff", fg="#475569").pack(side="left", padx=(0, 6))
        tk.Entry(g2, textvariable=net_pass_var, show="•", width=18, font=("Segoe UI", 9), bg="#ffffff", fg="#0f172a", relief="solid", bd=1).pack(side="left", ipady=3)

        tk.Label(net_card, textvariable=net_err_var, font=("Segoe UI", 8), bg="#ffffff", fg="#dc2626", anchor="w").pack(fill="x", pady=(0, 6))

        net_btns = tk.Frame(net_card, bg="#ffffff")
        net_btns.pack(fill="x")

        def _on_connect_net(_ev=None):
            ok, msg = connect_network_share(net_path_var.get(), net_user_var.get(), net_pass_var.get())
            if not ok:
                net_err_var.set(msg)
                return
            dlg.destroy()
            self._navigate_to(Path(msg), sync_tree=False)

        ent_unc.bind("<Return>", _on_connect_net)
        ModernRoundedButton(
            net_btns,
            text="🌐 Verbinden & Ordner öffnen",
            command=_on_connect_net,
            bg="#ffffff",
            fg="#334155",
            hover_bg="#f1f5f9",
            border_color="#cbd5e1",
            font=("Segoe UI", 9),
            r=8,
            padx=12,
            pady=5,
        ).pack(side="left")

        ModernRoundedButton(
            net_btns,
            text="Schließen",
            command=dlg.destroy,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=5,
        ).pack(side="right")

        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def _prompt_network_path(self):
        """Einfacher Eingabe-Dialog für Firmen-/Server-Freigaben (z. B. \\\\Server\\Freigabe)."""
        dlg = tk.Toplevel(self)
        dlg.title("Netzlaufwerk oder Serverordner öffnen")
        dlg.configure(bg="#f8fafc")
        dlg.transient(self)
        dlg.grab_set()
        self.update_idletasks()
        dw, dh = 520, 220
        px = self.winfo_rootx() + max(0, (self.winfo_width() - dw) // 2)
        py = self.winfo_rooty() + max(0, (self.winfo_height() - dh) // 2)
        dlg.geometry(f"{dw}x{dh}+{px}+{py}")
        dlg.resizable(False, False)

        card = tk.Frame(dlg, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1", padx=16, pady=14)
        card.pack(fill="both", expand=True, padx=14, pady=14)

        tk.Label(card, text="🌐 Firmen- oder Serverordner öffnen", font=("Segoe UI", 10), bg="#ffffff", fg="#0f172a").pack(anchor="w")
        tk.Label(
            card,
            text="Geben Sie den Netzwerkpfad wie im Windows Explorer ein\n(z. B. \\\\Server\\Freigabe oder Z:\\Team):",
            font=("Segoe UI", 9),
            bg="#ffffff",
            fg="#475569",
            justify="left",
        ).pack(anchor="w", pady=(4, 10))

        cur_val = self.path_var.get().strip()
        net_var = tk.StringVar(value=cur_val if is_network_path(cur_val) else "\\\\")
        ent = tk.Entry(card, textvariable=net_var, font=("Segoe UI", 10), bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        ent.pack(fill="x", ipady=4, pady=(0, 12))
        ent.focus_set()
        ent.icursor("end")

        btn_row = tk.Frame(card, bg="#ffffff")
        btn_row.pack(fill="x")

        def on_open(_ev=None):
            raw = net_var.get().strip()
            dlg.destroy()
            if raw and raw != "\\\\":
                self._navigate_to(Path(raw), sync_tree=False)

        ent.bind("<Return>", on_open)
        ModernRoundedButton(btn_row, "Ordner öffnen", command=on_open, bg="#2563eb", fg="#ffffff", hover_bg="#1d4ed8", font=("Segoe UI", 9, "bold"), r=8, padx=14, pady=5).pack(side="right")
        ModernRoundedButton(btn_row, "Abbrechen", command=dlg.destroy, bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"), r=8, padx=12, pady=5).pack(side="right", padx=(0, 8))

    def _add_dummy_child_if_has_dirs(self, node_id: str, path: Path):
        if node_id in self._tree_loaded:
            return
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("$"):
                        self.dir_tree.insert(node_id, "end", text="__placeholder__")
                        return
        except Exception:
            pass

    def _ensure_tree_node_loaded(self, node_id: str):
        if node_id in self._tree_loaded:
            return
        self._tree_loaded.add(node_id)
        for ch in self.dir_tree.get_children(node_id):
            if ch not in self._tree_paths:
                self.dir_tree.delete(ch)
        parent_path = self._tree_paths.get(node_id)
        if not parent_path:
            return
        subdirs = []
        try:
            with os.scandir(parent_path) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False) and entry.name.lower() not in {"$recycle.bin", "system volume information"}:
                        subdirs.append(Path(entry.path))
        except Exception:
            return
        subdirs.sort(key=lambda p: p.name.lower())
        for sub in subdirs:
            nid = self.dir_tree.insert(node_id, "end", text=f"📁 {sub.name}")
            self._tree_paths[nid] = sub
            self._add_dummy_child_if_has_dirs(nid, sub)

    def _on_left_tree_open(self, event):
        nid = self.dir_tree.focus()
        if nid:
            self._ensure_tree_node_loaded(nid)

    def _on_left_tree_select(self, event):
        if self._ignore_tree_select:
            return
        sel = self.dir_tree.selection()
        if not sel:
            return
        target = self._tree_paths.get(sel[0])
        if target and target.exists():
            self._navigate_to(target, sync_tree=False)

    def _sync_left_tree_to(self, path: Path):
        """Klappt den linken Ordnerbaum bis zum aktuellen Pfad auf und markiert ihn."""
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        parts = resolved.parts
        if not parts:
            return
        self._ignore_tree_select = True
        try:
            current_nid = None
            rem_parts = parts[1:]

            # Tieferen Direkt-Eintrag (z. B. OneDrive Privat / Firma) bevorzugen, falls passend
            best_len = -1
            for nid in self.dir_tree.get_children(""):
                p = self._tree_paths.get(nid)
                if not p:
                    continue
                try:
                    p_res = p.resolve()
                    rel = resolved.relative_to(p_res)
                    depth = len(p_res.parts)
                    if depth > best_len:
                        best_len = depth
                        current_nid = nid
                        rem_parts = rel.parts
                except Exception:
                    pass

            if current_nid is None:
                root_part = parts[0].rstrip("\\").lower()
                for nid in self.dir_tree.get_children(""):
                    p = self._tree_paths.get(nid)
                    if p and str(p).rstrip("\\").lower() == root_part:
                        current_nid = nid
                        rem_parts = parts[1:]
                        break

            if not current_nid:
                return
            for part in rem_parts:
                self.dir_tree.item(current_nid, open=True)
                self._ensure_tree_node_loaded(current_nid)
                next_nid = None
                for ch in self.dir_tree.get_children(current_nid):
                    cp = self._tree_paths.get(ch)
                    if cp and cp.name.lower() == part.lower():
                        next_nid = ch
                        break
                if not next_nid:
                    break
                current_nid = next_nid
            self.dir_tree.selection_set(current_nid)
            self.dir_tree.see(current_nid)
        finally:
            self._ignore_tree_select = False

    def _navigate_to(self, path: Path, sync_tree: bool = True):
        try:
            target = path if is_network_path(path) else path.resolve()
        except Exception:
            target = path
        if not target.exists() or not target.is_dir():
            sub_msg = (
                f"{target}\n\nHinweis: Bei Firmen-/Serverordnern prüfen Sie bitte VPN bzw. Anmeldung im Windows Explorer."
                if is_network_path(target)
                else str(target)
            )
            dlg = ModernNoticeDialog(self, "Ordner nicht gefunden", "Der gewählte Ordner existiert nicht oder ist nicht erreichbar.", sub_msg, kind="warning")
            self.wait_window(dlg)
            return

        self.current_dir = target
        self.path_var.set(str(target))
        self.selected_var.set(str(target))

        for item in self.content_tree.get_children():
            self.content_tree.delete(item)
        self._right_entries.clear()

        dirs = []
        files = []
        access_error = False
        try:
            with os.scandir(target) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            dirs.append(entry)
                        elif entry.is_file(follow_symlinks=False):
                            files.append(entry)
                    except Exception:
                        pass
        except PermissionError:
            access_error = True
        except Exception:
            access_error = True

        dirs.sort(key=lambda e: e.name.lower())
        files.sort(key=lambda e: e.name.lower())

        doc_count = 0
        for d in dirs:
            try:
                mtime = datetime.datetime.fromtimestamp(d.stat(follow_symlinks=False).st_mtime).strftime("%d.%m.%Y %H:%M")
            except Exception:
                mtime = ""
            rid = self.content_tree.insert("", "end", values=(f"📁 {d.name}", "Ordner", "—", mtime), tags=("tag_dir",))
            self._right_entries[rid] = ("dir", Path(d.path))

        for f in files:
            p = Path(f.path)
            ext = p.suffix.lower()
            is_doc = ext in VALID_EXTENSIONS
            if is_doc:
                doc_count += 1
            if ext in IMAGE_EXTENSIONS:
                icon = "🖼"
            elif ext in INSTALLER_EXTENSIONS:
                icon = "📦"
            elif ext in MEDIA_EXTENSIONS:
                icon = "🎬"
            else:
                icon = "📄"
            ext_label = ext.upper().lstrip(".") or "DATEI"
            try:
                st = f.stat(follow_symlinks=False)
                size_str = _format_file_size(st.st_size)
                mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%d.%m.%Y %H:%M")
            except Exception:
                size_str = ""
                mtime = ""
            tag = "tag_doc" if is_doc else "tag_other"
            rid = self.content_tree.insert("", "end", values=(f"{icon} {f.name}", ext_label, size_str, mtime), tags=(tag,))
            self._right_entries[rid] = ("file", p)

        if access_error:
            self.lbl_dir_stats.config(text="⚠️ Zugriff verweigert")
        elif not dirs and not files:
            self.lbl_dir_stats.config(text="📂 Dieser Ordner ist leer")
        else:
            self.lbl_dir_stats.config(
                text=f"📂 {len(dirs)} Unterordner  •  {len(files)} Dateien  •  {doc_count} erkannte Dokumente"
            )

        if sync_tree:
            self._sync_left_tree_to(target)

    def _on_right_select(self, event):
        sel = self.content_tree.selection()
        if not sel:
            self.selected_var.set(str(self.current_dir))
            return
        entry = self._right_entries.get(sel[0])
        if entry and entry[0] == "dir":
            self.selected_var.set(str(entry[1]))
        else:
            # Bei Klick auf eine Datei bleibt der aktuell geoeffnete Ordner ausgewaehlt
            self.selected_var.set(str(self.current_dir))

    def _on_right_double_click(self, event):
        sel = self.content_tree.selection()
        if not sel:
            return
        entry = self._right_entries.get(sel[0])
        if entry and entry[0] == "dir":
            self._navigate_to(entry[1], sync_tree=True)

    def _go_up(self):
        parent = self.current_dir.parent
        if parent != self.current_dir:
            self._navigate_to(parent, sync_tree=True)

    def _open_typed_path(self):
        raw = self.path_var.get().strip().strip('"')
        if raw:
            self._navigate_to(Path(raw), sync_tree=True)

    def _create_new_folder(self):
        name = simpledialog.askstring(
            "Neuer Ordner",
            f"Name des neuen Unterordners in:\n{self.current_dir}",
            parent=self,
        )
        if not name:
            return
        clean = name.strip()
        if not clean:
            return
        new_dir = self.current_dir / clean
        try:
            new_dir.mkdir(parents=True, exist_ok=True)
            # Baumknoten neu laden, falls aktueller Ordner im Baum bereits expandiert war
            for nid, p in list(self._tree_paths.items()):
                if p == self.current_dir:
                    self._tree_loaded.discard(nid)
                    self._ensure_tree_node_loaded(nid)
            self._navigate_to(new_dir, sync_tree=True)
        except Exception as e:
            dlg = ModernNoticeDialog(self, "Fehler", "Ordner konnte nicht erstellt werden.", str(e), kind="error")
            self.wait_window(dlg)

    def _confirm(self):
        chosen = Path(self.selected_var.get().strip() or str(self.current_dir))
        if chosen.exists() and chosen.is_dir():
            self.result = str(chosen)
        else:
            self.result = str(self.current_dir)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


def ask_folder_with_files(parent, title: str, initialdir: str) -> str | None:
    """Oeffnet den benutzerfreundlichen Ordner-Dialog mit Dateivorschau und gibt den Pfad zurueck."""
    dlg = FolderPickerDialog(parent, title, initialdir)
    parent.wait_window(dlg)
    return dlg.result


# -------------------------------------------------------------
# DATEINAMEN-EINKÜRZUNG & UMBENENNUNG (VARIANTE 1 & 3)
# -------------------------------------------------------------
def shorten_filename_subtitles(filename: str, max_dashes: int = 2) -> str:
    """Entfernt Untertitel nach dem n-ten ' - ' oder ' – ' Trenner (z.B. Autor - Titel)."""
    p = Path(filename)
    stem = p.stem
    ext = p.suffix
    parts = re.split(r'\s+[-–—]\s+', stem)
    if len(parts) > max_dashes:
        new_stem = " - ".join(parts[:max_dashes]).strip()
        new_stem = re.sub(r'[,;:\-–—\s]+$', '', new_stem).strip()
        return f"{new_stem}{ext}"
    return filename

def shorten_filename_max_length(filename: str, max_chars: int = 50, break_on_word: bool = True) -> str:
    """Begrenzt den Dateinamen auf max_chars Zeichen inklusive Dateiendung."""
    if len(filename) <= max_chars:
        return filename
    p = Path(filename)
    ext = p.suffix
    stem = p.stem
    allowed_stem_len = max_chars - len(ext)
    if allowed_stem_len <= 3:
        return filename[:max_chars]
    truncated = stem[:allowed_stem_len]
    if break_on_word:
        last_space = max(truncated.rfind(' '), truncated.rfind('-'), truncated.rfind('_'))
        if last_space >= allowed_stem_len // 2:
            truncated = truncated[:last_space]
    truncated = re.sub(r'[,;:\-–—\._\s]+$', '', truncated).strip()
    return f"{truncated}{ext}"

def clean_filename_underscores(filename: str) -> str:
    """Ersetzt Unterstriche durch Leerzeichen und säubert doppelte Leerzeichen."""
    p = Path(filename)
    stem = p.stem
    ext = p.suffix
    new_stem = stem.replace('_', ' ')
    new_stem = re.sub(r'\s+', ' ', new_stem).strip()
    return f"{new_stem}{ext}"

def clean_windows_filename(name: str) -> str:
    r"""Entfernt unter Windows verbotene Dateinamen-Zeichen: \ / : * ? \" < > |"""
    return re.sub(r'[\\/:*?"<>|]', '', name).strip()


class SingleFileRenameDialog(tk.Toplevel):
    """Eleganter Light-Fluent Dialog zum gezielten Umbenennen einer Datei (Taste F2 / Kontextmenü)."""
    def __init__(self, parent, item: dict, on_success=None):
        super().__init__(parent)
        self.parent = parent
        self.item = item
        self.on_success = on_success

        self.title("Dateiname bearbeiten")
        self.configure(bg="#f8fafc")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        cur_name = str(item.get("name", ""))
        self.orig_name = str(item.get("orig_name") or cur_name)
        self.is_dir = bool(item.get("is_dir", False))
        p = Path(cur_name)
        self.file_ext = "" if self.is_dir else p.suffix
        self.file_stem = cur_name if self.is_dir else p.stem
        self.orig_stem = self.orig_name if self.is_dir else Path(self.orig_name).stem

        self._build_ui()
        self._center_dialog()

    def _build_ui(self):
        outer = tk.Frame(self, bg="#f8fafc", padx=22, pady=18)
        outer.pack(fill="both", expand=True)

        # Header mit Icon & Typographie
        hdr_frame = tk.Frame(outer, bg="#f8fafc")
        hdr_frame.pack(fill="x", pady=(0, 14))

        icon_lbl = tk.Label(hdr_frame, text="✏️", font=("Segoe UI", 16), bg="#eff6ff", fg="#2563eb", width=3, height=1)
        icon_lbl.pack(side="left", padx=(0, 12))

        title_box = tk.Frame(hdr_frame, bg="#f8fafc")
        title_box.pack(side="left", fill="x", expand=True)

        tk.Label(title_box, text="Dateiname bearbeiten", font=("Segoe UI", 12, "bold"), bg="#f8fafc", fg="#0f172a").pack(anchor="w")
        sub_text = f"Original: {self.orig_name} ({len(self.orig_name)} Zeichen)"
        if len(sub_text) > 70:
            sub_text = sub_text[:67] + "…"
        tk.Label(title_box, text=sub_text, font=("Segoe UI", 8), bg="#f8fafc", fg="#64748b").pack(anchor="w")

        # Weisse Karte fuer Eingabefeld & Live-Status
        card = tk.Frame(outer, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1", padx=16, pady=14)
        card.pack(fill="x", pady=(0, 14))

        tk.Label(card, text="Neuer Dateiname (ohne Endung):", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155").pack(anchor="w", pady=(0, 6))

        # Eingabezeile mit getrenntem, geschütztem Endungs-Badge
        self.var_stem = tk.StringVar(value=self.file_stem)
        self.var_stem.trace_add("write", lambda *_a: self._on_stem_change())

        ent_box = tk.Frame(card, bg="#ffffff", highlightthickness=1, highlightbackground="#94a3b8")
        ent_box.pack(fill="x", pady=(0, 8))

        self.entry = tk.Entry(
            ent_box,
            textvariable=self.var_stem,
            font=("Segoe UI", 10),
            bg="#ffffff",
            fg="#0f172a",
            relief="flat",
            bd=0,
            insertbackground="#2563eb"
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=(8, 4), pady=6)

        if self.file_ext:
            self.lbl_ext_badge = tk.Label(
                ent_box,
                text=f" {self.file_ext} ",
                font=("Segoe UI", 9, "bold"),
                bg="#e2e8f0",
                fg="#0f172a",
                padx=6,
                pady=2,
                highlightthickness=1,
                highlightbackground="#94a3b8"
            )
            self.lbl_ext_badge.pack(side="right", padx=(0, 6), pady=4)
            attach_tooltip(self.lbl_ext_badge, f"Dateiendung ({self.file_ext}) bleibt automatisch geschützt und wird beim Speichern angehängt.")

        # Fokus setzen und gesamten Text markieren
        self.after(50, self._focus_entry)

        # Statuszeile: Zeichenzaehler & Endungs-Hinweis
        status_row = tk.Frame(card, bg="#ffffff")
        status_row.pack(fill="x", pady=(0, 12))

        self.lbl_counter = tk.Label(status_row, text="", font=("Segoe UI", 8), bg="#ffffff", fg="#64748b")
        self.lbl_counter.pack(side="left")

        # Schnellauswahl-Aktionen mit klaren, ansprechenden Buttons
        chip_label_row = tk.Frame(card, bg="#ffffff")
        chip_label_row.pack(fill="x", pady=(0, 6))
        tk.Label(chip_label_row, text="Schnell-Aktionen (Klick passt den Namen sofort an):", font=("Segoe UI", 8, "bold"), bg="#ffffff", fg="#475569").pack(side="left")

        chip_row = tk.Frame(card, bg="#ffffff")
        chip_row.pack(fill="x")

        chips = [
            ("📖 Nur Autor & Titel", lambda: self._apply_quick_action("autor_titel"), "#eff6ff", "#1d4ed8", "#dbeafe", "#93c5fd", "Lange Untertitel entfernen: Behält nur Autor und Haupttitel"),
            ("📚 Bis Serie / Band", lambda: self._apply_quick_action("serie_band"), "#f5f3ff", "#6d28d9", "#ede9fe", "#c4b5fd", "Behält Autor, Titel und Band- bzw. Reihenbezeichnung"),
            ("📏 Max. 50 Zeichen", lambda: self._apply_quick_action("max_50"), "#fffbeb", "#b45309", "#fef3c7", "#fde68a", "Kürzt die Gesamtlänge wortgenau auf max. 50 Zeichen"),
            ("🔤 Ohne Unterstriche", lambda: self._apply_quick_action("underscores"), "#f0fdfa", "#0f766e", "#ccfbf1", "#99f6e4", "Ersetzt alle Unterstriche „_“ durch saubere Leerzeichen"),
            ("↺ Originalname", lambda: self._apply_quick_action("original"), "#ffffff", "#1e293b", "#f1f5f9", "#94a3b8", "Setzt den Namen auf den ursprünglichen Zustand zurück"),
        ]
        for c_txt, c_cmd, c_bg, c_fg, c_hbg, c_brd, c_tip in chips:
            btn = ModernRoundedButton(
                chip_row,
                text=c_txt,
                command=c_cmd,
                bg=c_bg,
                fg=c_fg,
                hover_bg=c_hbg,
                border_color=c_brd,
                font=("Segoe UI", 9, "bold"),
                r=8,
                padx=10,
                pady=5,
            )
            btn.pack(side="left", padx=(0, 6))
            attach_tooltip(btn, c_tip)

        # Bottom Button-Leiste
        btn_row = tk.Frame(outer, bg="#f8fafc")
        btn_row.pack(fill="x")

        ModernRoundedButton(
            btn_row,
            text="Abbrechen",
            command=self.destroy,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=16,
            pady=6,
        ).pack(side="right", padx=(6, 0))

        ModernRoundedButton(
            btn_row,
            text="✓ Übernehmen",
            command=self._apply_rename,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            border_color=None,
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=18,
            pady=6,
        ).pack(side="right")

        self.bind("<Return>", lambda _e: (self._apply_rename(), "break")[1])
        self.bind("<Escape>", lambda _e: (self.destroy(), "break")[1])

        self._on_stem_change()

    def _focus_entry(self):
        try:
            self.entry.focus_set()
            self.entry.selection_range(0, tk.END)
        except Exception:
            pass

    def _on_stem_change(self):
        val = self.var_stem.get()
        cur_len = len(val)
        total_len = cur_len + len(self.file_ext)
        orig_len = len(self.orig_name)
        diff = total_len - orig_len

        if diff < 0:
            diff_str = f" ({diff} Zeichen kürzer)"
            diff_fg = "#16a34a"
        elif diff > 0:
            diff_str = f" (+{diff} Zeichen länger)"
            diff_fg = "#ea580c"
        else:
            diff_str = " (gleiche Länge)"
            diff_fg = "#64748b"

        ext_note = f"  •  🔒 Endung {self.file_ext} bleibt automatisch erhalten" if self.file_ext else ""
        self.lbl_counter.config(
            text=f"{total_len} Zeichen gesamt (Name: {cur_len}){ext_note}  • {diff_str}",
            fg=diff_fg if diff != 0 else "#64748b"
        )

    def _apply_quick_action(self, action: str):
        cur = self.var_stem.get()
        if action == "autor_titel":
            new_full = shorten_filename_subtitles(f"{cur}{self.file_ext}", 2)
            new_val = Path(new_full).stem if self.file_ext else new_full
        elif action == "serie_band":
            new_full = shorten_filename_subtitles(f"{cur}{self.file_ext}", 3)
            new_val = Path(new_full).stem if self.file_ext else new_full
        elif action == "max_50":
            new_full = shorten_filename_max_length(f"{cur}{self.file_ext}", 50, break_on_word=True)
            new_val = Path(new_full).stem if self.file_ext else new_full
        elif action == "underscores":
            new_val = clean_filename_underscores(cur)
        elif action == "original":
            new_val = self.orig_stem
        else:
            return
        self.var_stem.set(new_val)
        self._focus_entry()

    def _apply_rename(self):
        raw_stem = self.var_stem.get().strip()
        cleaned_stem = clean_windows_filename(raw_stem)
        if not cleaned_stem:
            messagebox.showwarning("Ungültiger Name", "Der Dateiname darf nicht leer sein.", parent=self)
            return

        # Falls der Benutzer versehentlich die Endung selbst mit eingetippt hat, abschneiden
        if self.file_ext and cleaned_stem.lower().endswith(self.file_ext.lower()):
            cleaned_stem = cleaned_stem[:-len(self.file_ext)].rstrip()

        final_name = f"{cleaned_stem}{self.file_ext}" if self.file_ext else cleaned_stem

        if callable(self.on_success):
            self.on_success(final_name)
        self.destroy()

    def _center_dialog(self):
        self.update_idletasks()
        w = max(680, self.winfo_reqwidth())
        h = max(310, self.winfo_reqheight())
        px = self.parent.winfo_rootx()
        py = self.parent.winfo_rooty()
        pw = self.parent.winfo_width()
        ph = self.parent.winfo_height()
        x = max(50, px + (pw - w) // 2)
        y = max(50, py + (ph - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")


def _get_shorten_badge_photo(size: int = 38) -> ImageTk.PhotoImage:
    """Zeichnet ein scharfes 3x-Supersampling-Scheren-Badge im Light-Fluent-Stil."""
    key = ("shorten_badge_v1", size, CURRENT_THEME_MODE)
    if key in IMG_CACHE:
        return IMG_CACHE[key]
    scale = 3
    s = size * scale
    is_dark = (CURRENT_THEME_MODE == "dark")
    bg_hex = "#172554" if is_dark else "#eff6ff"
    brd_hex = "#2563eb" if is_dark else "#93c5fd"
    fg_hex = "#60a5fa" if is_dark else "#2563eb"

    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = 10 * scale
    d.rounded_rectangle([scale, scale, s - scale - 1, s - scale - 1], radius=r, fill=bg_hex, outline=brd_hex, width=scale)

    # Vektor-Schere exakt zentriert (Griffe links, gekreuzte Klingen rechts)
    lw = max(2, int(2.2 * scale))
    u = s / 38.0
    # Zwei Griffringe links
    d.ellipse([9 * u, 10 * u, 16 * u, 17 * u], outline=fg_hex, width=lw)
    d.ellipse([9 * u, 21 * u, 16 * u, 28 * u], outline=fg_hex, width=lw)
    # Gekreuzte Klingen nach rechts
    d.line([15.2 * u, 14.8 * u, 28.5 * u, 24.5 * u], fill=fg_hex, width=lw)
    d.line([15.2 * u, 23.2 * u, 28.5 * u, 13.5 * u], fill=fg_hex, width=lw)
    # Schraube am Kreuzungspunkt
    d.ellipse([19.8 * u, 17.8 * u, 22.2 * u, 20.2 * u], fill=fg_hex)

    photo = ImageTk.PhotoImage(img.resize((size, size), Image.LANCZOS))
    IMG_CACHE[key] = photo
    return photo


class CustomShortenDialog(tk.Toplevel):
    """Moderner Light-Fluent Dialog zum Einkürzen von Dateinamen mit farblich getrennter Vorher/Nachher Live-Vorschau."""
    def __init__(self, parent, target_rids: list[str]):
        super().__init__(parent)
        self.withdraw()
        self.parent = parent
        self.target_rids = [rid for rid in target_rids if rid in parent.preview_items and parent.preview_items[rid].get("action") != "Gelöscht"]
        self._is_dark = (CURRENT_THEME_MODE == "dark")

        # Klare Farbpalette mit deutlicher Trennung zwischen Hintergrund, Karten, Vorher/Nachher und Buttons vs. Anzeigen
        if self._is_dark:
            self.pal = {
                "dlg_bg": "#0f172a",
                "bar_bg": "#1e293b",
                "card_bg": "#1e293b",
                "card_hdr": "#0f172a",
                "card_brd": "#475569",
                "div": "#334155",
                "txt": "#f1f5f9",
                "muted": "#94a3b8",
                "row_active": "#172554",
                "accent": "#3b82f6",
                "info_bg": "#0f172a",
                "info_fg": "#cbd5e1",
                "inp_bg": "#0f172a",
                "inp_fg": "#f8fafc",
                "inp_brd": "#475569",
                "bef_hdr": "#1e293b",
                "bef_hdr_fg": "#cbd5e1",
                "bef_bg": "#172033",
                "bef_bg_alt": "#131b2b",
                "bef_fg": "#cbd5e1",
                "aft_hdr": "#064e3b",
                "aft_hdr_fg": "#a7f3d0",
                "aft_bg": "#063b2c",
                "aft_bg_alt": "#052e22",
                "aft_fg": "#6ee7b7",
                "same_bg": "#1e293b",
                "same_fg": "#64748b",
                "det_bef_bg": "#172033",
                "det_bef_tag": "#334155",
                "det_bef_tag_fg": "#e2e8f0",
                "det_aft_bg": "#063b2c",
                "det_aft_tag": "#065f46",
                "det_aft_tag_fg": "#a7f3d0",
                "delta_fg": "#34d399",
                "sel_bar": "#60a5fa",
                "btn_sec_bg": "#0f172a",
                "btn_sec_fg": "#e2e8f0",
                "btn_sec_hov": "#334155",
                "btn_sec_brd": "#64748b",
                "btn_opt_bg": "#0f172a",
                "btn_opt_fg": "#93c5fd",
                "btn_opt_hov": "#1e3a8a",
                "btn_opt_brd": "#3b82f6",
            }
        else:
            self.pal = {
                "dlg_bg": "#e8edf4",
                "bar_bg": "#ffffff",
                "card_bg": "#ffffff",
                "card_hdr": "#f1f5f9",
                "card_brd": "#94a3b8",
                "div": "#cbd5e1",
                "txt": "#0f172a",
                "muted": "#64748b",
                "row_active": "#eff6ff",
                "accent": "#2563eb",
                "info_bg": "#f1f5f9",
                "info_fg": "#334155",
                "inp_bg": "#ffffff",
                "inp_fg": "#0f172a",
                "inp_brd": "#94a3b8",
                "bef_hdr": "#e2e8f0",
                "bef_hdr_fg": "#334155",
                "bef_bg": "#f8fafc",
                "bef_bg_alt": "#f1f5f9",
                "bef_fg": "#475569",
                "aft_hdr": "#d1fae5",
                "aft_hdr_fg": "#065f46",
                "aft_bg": "#ecfdf5",
                "aft_bg_alt": "#e2faee",
                "aft_fg": "#065f46",
                "same_bg": "#ffffff",
                "same_fg": "#94a3b8",
                "det_bef_bg": "#f1f5f9",
                "det_bef_tag": "#cbd5e1",
                "det_bef_tag_fg": "#1e293b",
                "det_aft_bg": "#ecfdf5",
                "det_aft_tag": "#bbf7d0",
                "det_aft_tag_fg": "#065f46",
                "delta_fg": "#047857",
                "sel_bar": "#2563eb",
                "btn_sec_bg": "#f8fafc",
                "btn_sec_fg": "#1e293b",
                "btn_sec_hov": "#e2e8f0",
                "btn_sec_brd": "#64748b",
                "btn_opt_bg": "#ffffff",
                "btn_opt_fg": "#1d4ed8",
                "btn_opt_hov": "#dbeafe",
                "btn_opt_brd": "#3b82f6",
            }

        self.title("Dateinamen einkürzen")
        self.configure(bg=self.pal["dlg_bg"])
        self.resizable(True, True)
        self.minsize(780, 560)
        self.transient(parent)
        self.grab_set()
        apply_window_folder_icon(self)

        self.mode_var = tk.StringVar(value="subtitles")
        self.dash_count_var = tk.StringVar(value="2")
        self.max_chars_var = tk.StringVar(value="50")
        self.word_boundary_var = tk.BooleanVar(value=True)
        self.replace_find_var = tk.StringVar(value="")
        self.replace_with_var = tk.StringVar(value="")
        self.only_changed_var = tk.BooleanVar(value=False)

        self._preview_rows_data = {}
        self._row_sel_bars = {}
        self._selected_row_id = None
        self._last_changed_count = 0
        self._preset_len_buttons = {}
        self._option_row_widgets = {}
        self._option_accents = {}

        self._build_ui()
        self._center_dialog()
        self.deiconify()
        self.lift()
        self.focus_set()

    def _safe_int(self, var: tk.StringVar, default: int, lo: int, hi: int) -> int:
        try:
            v = int(str(var.get()).strip())
            return max(lo, min(hi, v))
        except Exception:
            return default

    def _select_mode(self, mode: str):
        if self.mode_var.get() != mode:
            self.mode_var.set(mode)
        self._update_option_highlights()
        self._update_preview()

    def _step_dash(self, delta: int):
        cur = self._safe_int(self.dash_count_var, 2, 1, 10)
        nxt = max(1, min(10, cur + delta))
        self.dash_count_var.set(str(nxt))
        self._select_mode("subtitles")

    def _set_len_preset(self, val: int):
        self.max_chars_var.set(str(val))
        self._select_mode("max_len")

    def _make_fluent_input(self, parent, text_var: tk.StringVar, placeholder: str, width_px: int = 175, on_focus=None, on_change=None):
        """Erstellt ein klares Eingabefeld mit Overlay-Platzhalter und blauem Fokusrahmen."""
        p = self.pal
        box = tk.Frame(parent, bg=p["inp_bg"], highlightthickness=1, highlightbackground=p["inp_brd"], highlightcolor=p["accent"])
        box.configure(width=width_px, height=30)
        box.pack_propagate(False)

        ent = tk.Entry(
            box,
            textvariable=text_var,
            font=("Segoe UI", 9),
            bg=p["inp_bg"],
            fg=p["inp_fg"],
            insertbackground=p["accent"],
            bd=0,
            highlightthickness=0,
        )
        ent.pack(fill="both", expand=True, padx=8, pady=4)

        ph = tk.Label(box, text=placeholder, font=("Segoe UI", 9), bg=p["inp_bg"], fg=p["muted"], cursor="xterm")
        ph.bind("<Button-1>", lambda _e: ent.focus_set())

        def _sync_ph(*_a):
            if text_var.get():
                ph.place_forget()
            else:
                ph.place(x=8, rely=0.5, anchor="w")

        def _on_in(_e=None):
            box.configure(highlightbackground=p["accent"], highlightthickness=2)
            if on_focus:
                on_focus()

        def _on_out(_e=None):
            box.configure(highlightbackground=p["inp_brd"], highlightthickness=1)

        def _on_key(_e=None):
            _sync_ph()
            if on_change:
                on_change()

        ent.bind("<FocusIn>", _on_in)
        ent.bind("<FocusOut>", _on_out)
        ent.bind("<KeyRelease>", _on_key)
        text_var.trace_add("write", _sync_ph)
        _sync_ph()
        return box, ent

    def _on_mousewheel(self, event):
        try:
            if hasattr(self, "prev_canvas") and self.prev_canvas.winfo_exists():
                delta = -1 * int(event.delta / 120) if event.delta else 0
                if delta:
                    self.prev_canvas.yview_scroll(delta, "units")
        except Exception:
            pass

    def _build_ui(self):
        p = self.pal
        cnt = len(self.target_rids)

        # ── 1. KOPFLEISTE (volle Breite, klar abgesetzt) ──
        hdr_bar = tk.Frame(self, bg=p["bar_bg"])
        hdr_bar.pack(side="top", fill="x")
        hdr = tk.Frame(hdr_bar, bg=p["bar_bg"], padx=20, pady=12)
        hdr.pack(fill="x")

        badge_img = _get_shorten_badge_photo(38)
        lbl_ico = tk.Label(hdr, image=badge_img, bg=p["bar_bg"], bd=0)
        lbl_ico.image = badge_img
        lbl_ico.pack(side="left", padx=(0, 12))

        title_box = tk.Frame(hdr, bg=p["bar_bg"])
        title_box.pack(side="left", fill="x", expand=True)

        tk.Label(
            title_box,
            text="Dateinamen einkürzen",
            font=("Segoe UI", 13, "bold"),
            bg=p["bar_bg"],
            fg=p["txt"],
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            title_box,
            text="Kürzungsmethode wählen und das Ergebnis farblich getrennt in der Live-Vorschau prüfen.",
            font=("Segoe UI", 9),
            bg=p["bar_bg"],
            fg=p["muted"],
            anchor="w",
        ).pack(fill="x")

        # Reine Info-Anzeige (flach, ohne Button-Rahmen – klar als Anzeige erkennbar, NICHT als Button)
        cnt_lbl = "Auswahl: 1 Datei" if cnt == 1 else f"Auswahl: {cnt} Dateien"
        tk.Label(
            hdr,
            text=cnt_lbl,
            font=("Segoe UI", 9),
            bg=p["info_bg"],
            fg=p["info_fg"],
            padx=10,
            pady=4,
            bd=0,
        ).pack(side="right", anchor="center")

        tk.Frame(hdr_bar, bg=p["div"], height=1).pack(fill="x")

        # ── 4. FUSSLEISTE ZUERST UNTEN VERANKERN (niemals abschneidbar, klar abgetrennt) ──
        ftr_bar = tk.Frame(self, bg=p["bar_bg"])
        ftr_bar.pack(side="bottom", fill="x")
        tk.Frame(ftr_bar, bg=p["div"], height=1).pack(fill="x")

        btn_row = tk.Frame(ftr_bar, bg=p["bar_bg"], padx=20, pady=12)
        btn_row.pack(fill="x")

        btn_h = 38
        ModernRoundedButton(
            btn_row,
            text="Abbrechen",
            command=self.destroy,
            bg=p["btn_sec_bg"],
            fg=p["btn_sec_fg"],
            hover_bg=p["btn_sec_hov"],
            border_color=p["btn_sec_brd"],
            font=("Segoe UI", 9, "bold"),
            r=10,
            padx=20,
            pady=8,
            min_height=btn_h,
        ).pack(side="left")

        f_apply = tkfont.Font(font=("Segoe UI", 10, "bold"))
        apply_min_w = f_apply.measure("Auf 999 Dateien anwenden ➔") + 44

        self.btn_apply = ModernRoundedButton(
            btn_row,
            text=f"Auf {cnt} Dateien anwenden ➔",
            command=self._apply_shortening,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            border_color=None,
            font=("Segoe UI", 10, "bold"),
            r=10,
            padx=22,
            pady=8,
            min_width=apply_min_w,
            min_height=btn_h,
        )
        self.btn_apply.pack(side="right")

        # ── HAUPTBEREICH AUF KÜHLEM HINTERGRUND (Karten heben sich deutlich ab) ──
        body = tk.Frame(self, bg=p["dlg_bg"], padx=18, pady=14)
        body.pack(fill="both", expand=True)

        # ── 2. BEREICH: KARTE "1. KÜRZUNGSMETHODE WÄHLEN" ──
        card = tk.Frame(body, bg=p["card_bg"], highlightthickness=1, highlightbackground=p["card_brd"])
        card.pack(side="top", fill="x", pady=(0, 12))

        card_hdr = tk.Frame(card, bg=p["card_hdr"], padx=14, pady=7)
        card_hdr.pack(fill="x")
        tk.Label(
            card_hdr,
            text="1. Kürzungsmethode wählen",
            font=("Segoe UI", 9, "bold"),
            bg=p["card_hdr"],
            fg=p["txt"],
        ).pack(side="left")
        tk.Label(
            card_hdr,
            text="Aktive Regel ist blau markiert",
            font=("Segoe UI", 8),
            bg=p["card_hdr"],
            fg=p["muted"],
        ).pack(side="right")
        tk.Frame(card, bg=p["div"], height=1).pack(fill="x")

        def _make_option_row(mode_key: str):
            wrap = tk.Frame(card, bg=p["card_bg"])
            wrap.pack(fill="x")
            accent = tk.Frame(wrap, bg=p["card_bg"], width=4)
            accent.pack(side="left", fill="y")
            inner = tk.Frame(wrap, bg=p["card_bg"], padx=12, pady=7)
            inner.pack(side="left", fill="x", expand=True)
            self._option_accents[mode_key] = accent
            self._option_row_widgets[mode_key] = [wrap, inner]
            return inner

        # Option 1: Autor & Haupttitel
        r1 = _make_option_row("subtitles")
        rb1 = tk.Radiobutton(
            r1,
            text="Autor & Haupttitel behalten",
            variable=self.mode_var,
            value="subtitles",
            font=("Segoe UI", 9, "bold"),
            bg=p["card_bg"],
            activebackground=p["row_active"],
            selectcolor=p["card_bg"],
            fg=p["txt"],
            cursor="hand2",
            command=lambda: self._select_mode("subtitles"),
        )
        rb1.pack(side="left")
        lbl_r1_hint = tk.Label(r1, text="Untertitel nach Bindestrich entfernen", font=("Segoe UI", 8), bg=p["card_bg"], fg=p["muted"])
        lbl_r1_hint.pack(side="left", padx=(6, 12))

        step_grp = tk.Frame(r1, bg=p["card_bg"])
        step_grp.pack(side="right")
        lbl_nach = tk.Label(step_grp, text="Nach dem", font=("Segoe UI", 9), bg=p["card_bg"], fg=p["muted"])
        lbl_nach.pack(side="left", padx=(0, 6))

        ModernRoundedButton(
            step_grp, text="−", command=lambda: self._step_dash(-1),
            bg=p["btn_opt_bg"], fg=p["btn_opt_fg"], hover_bg=p["btn_opt_hov"], border_color=p["btn_opt_brd"],
            font=("Segoe UI", 9, "bold"), r=6, padx=7, pady=2, min_width=28, min_height=26,
        ).pack(side="left")

        dash_box = tk.Frame(step_grp, bg=p["inp_bg"], highlightthickness=1, highlightbackground=p["inp_brd"], width=38, height=26)
        dash_box.pack_propagate(False)
        dash_box.pack(side="left", padx=4)
        ent_dash = tk.Entry(
            dash_box, textvariable=self.dash_count_var, font=("Segoe UI", 9, "bold"),
            bg=p["inp_bg"], fg=p["inp_fg"], insertbackground=p["accent"], justify="center", bd=0, highlightthickness=0,
        )
        ent_dash.pack(fill="both", expand=True)
        ent_dash.bind("<FocusIn>", lambda _e: (dash_box.configure(highlightbackground=p["accent"], highlightthickness=2), self._select_mode("subtitles")))
        ent_dash.bind("<FocusOut>", lambda _e: dash_box.configure(highlightbackground=p["inp_brd"], highlightthickness=1))
        ent_dash.bind("<KeyRelease>", lambda _e: self._select_mode("subtitles"))

        ModernRoundedButton(
            step_grp, text="+", command=lambda: self._step_dash(1),
            bg=p["btn_opt_bg"], fg=p["btn_opt_fg"], hover_bg=p["btn_opt_hov"], border_color=p["btn_opt_brd"],
            font=("Segoe UI", 9, "bold"), r=6, padx=7, pady=2, min_width=28, min_height=26,
        ).pack(side="left")
        lbl_trenner = tk.Label(step_grp, text=". Trenner", font=("Segoe UI", 9), bg=p["card_bg"], fg=p["muted"])
        lbl_trenner.pack(side="left", padx=(6, 0))
        self._option_row_widgets["subtitles"].extend([rb1, lbl_r1_hint, step_grp, lbl_nach, lbl_trenner])

        tk.Frame(card, bg=p["div"], height=1).pack(fill="x")

        # Option 2: Maximale Gesamtlänge
        r2 = _make_option_row("max_len")
        rb2 = tk.Radiobutton(
            r2,
            text="Maximale Länge begrenzen",
            variable=self.mode_var,
            value="max_len",
            font=("Segoe UI", 9, "bold"),
            bg=p["card_bg"],
            activebackground=p["row_active"],
            selectcolor=p["card_bg"],
            fg=p["txt"],
            cursor="hand2",
            command=lambda: self._select_mode("max_len"),
        )
        rb2.pack(side="left")

        len_grp = tk.Frame(r2, bg=p["card_bg"])
        len_grp.pack(side="right")

        for preset in (40, 50, 60, 80):
            p_btn = ModernRoundedButton(
                len_grp,
                text=str(preset),
                command=lambda v=preset: self._set_len_preset(v),
                bg=p["btn_opt_bg"],
                fg=p["btn_opt_fg"],
                hover_bg=p["btn_opt_hov"],
                border_color=p["btn_opt_brd"],
                font=("Segoe UI", 8, "bold"),
                r=6,
                padx=8,
                pady=2,
                min_width=34,
                min_height=26,
            )
            p_btn.pack(side="left", padx=(0, 4))
            self._preset_len_buttons[preset] = p_btn

        len_box = tk.Frame(len_grp, bg=p["inp_bg"], highlightthickness=1, highlightbackground=p["inp_brd"], width=48, height=26)
        len_box.pack_propagate(False)
        len_box.pack(side="left", padx=(4, 4))
        ent_len = tk.Entry(
            len_box, textvariable=self.max_chars_var, font=("Segoe UI", 9, "bold"),
            bg=p["inp_bg"], fg=p["inp_fg"], insertbackground=p["accent"], justify="center", bd=0, highlightthickness=0,
        )
        ent_len.pack(fill="both", expand=True)
        ent_len.bind("<FocusIn>", lambda _e: (len_box.configure(highlightbackground=p["accent"], highlightthickness=2), self._select_mode("max_len")))
        ent_len.bind("<FocusOut>", lambda _e: len_box.configure(highlightbackground=p["inp_brd"], highlightthickness=1))
        ent_len.bind("<KeyRelease>", lambda _e: self._select_mode("max_len"))

        lbl_z = tk.Label(len_grp, text="Zeichen", font=("Segoe UI", 9), bg=p["card_bg"], fg=p["muted"])
        lbl_z.pack(side="left", padx=(0, 10))
        cb_wb = tk.Checkbutton(
            len_grp,
            text="Ganze Wörter erhalten",
            variable=self.word_boundary_var,
            bg=p["card_bg"],
            activebackground=p["row_active"],
            selectcolor=p["card_bg"],
            fg=p["txt"],
            font=("Segoe UI", 9),
            cursor="hand2",
            command=lambda: self._select_mode("max_len"),
        )
        cb_wb.pack(side="left")
        self._option_row_widgets["max_len"].extend([rb2, len_grp, lbl_z, cb_wb])

        tk.Frame(card, bg=p["div"], height=1).pack(fill="x")

        # Option 3: Unterstriche ersetzen
        r3 = _make_option_row("clean_underscores")
        rb3 = tk.Radiobutton(
            r3,
            text="Unterstriche „_“ durch Leerzeichen ersetzen",
            variable=self.mode_var,
            value="clean_underscores",
            font=("Segoe UI", 9, "bold"),
            bg=p["card_bg"],
            activebackground=p["row_active"],
            selectcolor=p["card_bg"],
            fg=p["txt"],
            cursor="hand2",
            command=lambda: self._select_mode("clean_underscores"),
        )
        rb3.pack(side="left")
        self._option_row_widgets["clean_underscores"].append(rb3)

        tk.Frame(card, bg=p["div"], height=1).pack(fill="x")

        # Option 4: Textteil suchen & ersetzen
        r4 = _make_option_row("replace")
        rb4 = tk.Radiobutton(
            r4,
            text="Textteil suchen & ersetzen",
            variable=self.mode_var,
            value="replace",
            font=("Segoe UI", 9, "bold"),
            bg=p["card_bg"],
            activebackground=p["row_active"],
            selectcolor=p["card_bg"],
            fg=p["txt"],
            cursor="hand2",
            command=lambda: self._select_mode("replace"),
        )
        rb4.pack(side="left")

        rep_grp = tk.Frame(r4, bg=p["card_bg"])
        rep_grp.pack(side="right")

        lbl_find = tk.Label(rep_grp, text="Suchen:", font=("Segoe UI", 9), bg=p["card_bg"], fg=p["muted"])
        lbl_find.pack(side="left", padx=(0, 6))
        box_find, _ = self._make_fluent_input(
            rep_grp,
            self.replace_find_var,
            "z. B. _Kopie",
            width_px=165,
            on_focus=lambda: self._select_mode("replace"),
            on_change=lambda: self._select_mode("replace"),
        )
        box_find.pack(side="left", padx=(0, 12))

        lbl_rep = tk.Label(rep_grp, text="Ersetzen:", font=("Segoe UI", 9), bg=p["card_bg"], fg=p["muted"])
        lbl_rep.pack(side="left", padx=(0, 6))
        box_rep, _ = self._make_fluent_input(
            rep_grp,
            self.replace_with_var,
            "leer = entfernen",
            width_px=165,
            on_focus=lambda: self._select_mode("replace"),
            on_change=lambda: self._select_mode("replace"),
        )
        box_rep.pack(side="left")
        self._option_row_widgets["replace"].extend([rb4, rep_grp, lbl_find, lbl_rep])

        # ── 3. BEREICH: KARTE "2. LIVE-VORSCHAU" (FARBLICH GETRENNT VORHER ➔ NACHHER) ──
        prev_card = tk.Frame(body, bg=p["card_bg"], highlightthickness=1, highlightbackground=p["card_brd"])
        prev_card.pack(fill="both", expand=True)

        prev_hdr = tk.Frame(prev_card, bg=p["card_hdr"], padx=14, pady=7)
        prev_hdr.pack(side="top", fill="x")

        tk.Label(
            prev_hdr,
            text="2. Live-Vorschau",
            font=("Segoe UI", 9, "bold"),
            bg=p["card_hdr"],
            fg=p["txt"],
        ).pack(side="left")

        # Flacher Status-Text ohne Button-Rahmen – eindeutig eine Anzeige!
        self.lbl_prev_status = tk.Label(
            prev_hdr,
            text="",
            font=("Segoe UI", 9, "bold"),
            bg=p["card_hdr"],
            fg=p["delta_fg"],
        )
        self.lbl_prev_status.pack(side="left", padx=(12, 0))

        tk.Checkbutton(
            prev_hdr,
            text="Nur geänderte anzeigen",
            variable=self.only_changed_var,
            bg=p["card_hdr"],
            activebackground=p["card_hdr"],
            selectcolor=p["card_bg"],
            fg=p["txt"],
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._update_preview,
        ).pack(side="right")

        tk.Frame(prev_card, bg=p["div"], height=1).pack(side="top", fill="x")

        # Volltext-Detailbereich unten fest verankern – Vorher (Grau) & Nachher (Grün) klar getrennt!
        det_wrap = tk.Frame(prev_card, bg=p["card_bg"], padx=12, pady=10)
        det_wrap.pack(side="bottom", fill="x")

        det_box = tk.Frame(det_wrap, bg=p["card_bg"], highlightthickness=1, highlightbackground=p["div"])
        det_box.pack(fill="x")

        d_row1 = tk.Frame(det_box, bg=p["det_bef_bg"], padx=8, pady=5)
        d_row1.pack(fill="x")
        tk.Label(
            d_row1, text="VORHER", font=("Segoe UI", 7, "bold"),
            bg=p["det_bef_tag"], fg=p["det_bef_tag_fg"], width=9, anchor="center", pady=1,
        ).pack(side="left", padx=(0, 8))
        self.lbl_det_before = tk.Label(d_row1, text="—", font=("Segoe UI", 8), bg=p["det_bef_bg"], fg=p["bef_fg"], anchor="w")
        self.lbl_det_before.pack(side="left", fill="x", expand=True)

        tk.Frame(det_box, bg=p["div"], height=1).pack(fill="x")

        d_row2 = tk.Frame(det_box, bg=p["det_aft_bg"], padx=8, pady=5)
        d_row2.pack(fill="x")
        tk.Label(
            d_row2, text="NACHHER", font=("Segoe UI", 7, "bold"),
            bg=p["det_aft_tag"], fg=p["det_aft_tag_fg"], width=9, anchor="center", pady=1,
        ).pack(side="left", padx=(0, 8))
        self.lbl_det_after = tk.Label(d_row2, text="—", font=("Segoe UI", 8, "bold"), bg=p["det_aft_bg"], fg=p["aft_fg"], anchor="w")
        self.lbl_det_after.pack(side="left", fill="x", expand=True)

        # Farblich getrennte Vorher / Nachher Tabelle (Header + scrollbarer Canvas)
        tbl_outer = tk.Frame(prev_card, bg=p["card_bg"], padx=12, pady=(10, 0))
        tbl_outer.pack(fill="both", expand=True)

        tbl_box = tk.Frame(tbl_outer, bg=p["card_bg"], highlightthickness=1, highlightbackground=p["div"])
        tbl_box.pack(fill="both", expand=True)

        # Fixierter 2-Farben-Spaltenkopf
        tbl_hdr = tk.Frame(tbl_box, bg=p["bef_hdr"])
        tbl_hdr.pack(side="top", fill="x")
        tbl_hdr.columnconfigure(0, weight=5, uniform="pvcol")
        tbl_hdr.columnconfigure(1, weight=0, minsize=28)
        tbl_hdr.columnconfigure(2, weight=5, uniform="pvcol")
        tbl_hdr.columnconfigure(3, weight=0, minsize=100)
        tbl_hdr.columnconfigure(4, weight=0, minsize=17)  # Platzhalter für Scrollbar

        tk.Label(
            tbl_hdr, text="Vorher", font=("Segoe UI", 9, "bold"),
            bg=p["bef_hdr"], fg=p["bef_hdr_fg"], anchor="w", padx=10, pady=6,
        ).grid(row=0, column=0, sticky="nsew")
        tk.Label(
            tbl_hdr, text="➔", font=("Segoe UI", 9, "bold"),
            bg=p["bef_hdr"], fg=p["muted"], anchor="center", pady=6,
        ).grid(row=0, column=1, sticky="nsew")
        tk.Label(
            tbl_hdr, text="Nachher", font=("Segoe UI", 9, "bold"),
            bg=p["aft_hdr"], fg=p["aft_hdr_fg"], anchor="w", padx=10, pady=6,
        ).grid(row=0, column=2, sticky="nsew")
        tk.Label(
            tbl_hdr, text="Änderung", font=("Segoe UI", 9, "bold"),
            bg=p["bef_hdr"], fg=p["bef_hdr_fg"], anchor="center", padx=6, pady=6,
        ).grid(row=0, column=3, sticky="nsew")
        tk.Frame(tbl_hdr, bg=p["bef_hdr"], width=17).grid(row=0, column=4, sticky="nsew")

        tk.Frame(tbl_box, bg=p["div"], height=1).pack(side="top", fill="x")

        # Scrollbereich für Zeilen
        sc_area = tk.Frame(tbl_box, bg=p["card_bg"])
        sc_area.pack(fill="both", expand=True)

        self.prev_canvas = tk.Canvas(sc_area, bg=p["card_bg"], bd=0, highlightthickness=0, height=185)
        v_sb = ttk.Scrollbar(sc_area, orient="vertical", command=self.prev_canvas.yview)
        self.prev_canvas.configure(yscrollcommand=v_sb.set)
        v_sb.pack(side="right", fill="y")
        self.prev_canvas.pack(side="left", fill="both", expand=True)

        self.prev_inner = tk.Frame(self.prev_canvas, bg=p["card_bg"])
        self._prev_win_id = self.prev_canvas.create_window((0, 0), window=self.prev_inner, anchor="nw")

        self.prev_inner.bind("<Configure>", lambda _e: self.prev_canvas.configure(scrollregion=self.prev_canvas.bbox("all")))
        self.prev_canvas.bind("<Configure>", lambda e: self.prev_canvas.itemconfigure(self._prev_win_id, width=e.width))
        self.prev_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.prev_inner.bind("<MouseWheel>", self._on_mousewheel)

        self.bind("<Return>", lambda _e: (self._apply_shortening(), "break")[1])
        self.bind("<Escape>", lambda _e: (self.destroy(), "break")[1])

        self._update_option_highlights()
        self._update_preview()

    def _update_option_highlights(self):
        p = self.pal
        cur_mode = self.mode_var.get()
        for m_key, accent in self._option_accents.items():
            is_sel = (m_key == cur_mode)
            row_bg = p["row_active"] if is_sel else p["card_bg"]
            accent.configure(bg=p["accent"] if is_sel else p["card_bg"])
            for w in self._option_row_widgets.get(m_key, []):
                try:
                    w.configure(bg=row_bg)
                    if isinstance(w, (tk.Radiobutton, tk.Checkbutton)):
                        w.configure(activebackground=row_bg)
                except Exception:
                    pass

        cur_len = self._safe_int(self.max_chars_var, 50, 10, 250)
        for preset, p_btn in self._preset_len_buttons.items():
            is_active = (cur_mode == "max_len" and cur_len == preset)
            if is_active:
                p_btn.update_style(bg="#2563eb", fg="#ffffff", hover_bg="#1d4ed8", border_color=None)
            else:
                p_btn.update_style(bg=p["btn_opt_bg"], fg=p["btn_opt_fg"], hover_bg=p["btn_opt_hov"], border_color=p["btn_opt_brd"])

    def _compute_shortened(self, name: str, is_dir: bool = False) -> str:
        mode = self.mode_var.get()
        if mode == "subtitles":
            d = self._safe_int(self.dash_count_var, 2, 1, 10)
            new_name = shorten_filename_subtitles(name, d)
        elif mode == "max_len":
            m = self._safe_int(self.max_chars_var, 50, 10, 250)
            wb = self.word_boundary_var.get()
            new_name = shorten_filename_max_length(name, m, break_on_word=wb)
        elif mode == "clean_underscores":
            new_name = clean_filename_underscores(name)
        elif mode == "replace":
            f = self.replace_find_var.get()
            w = self.replace_with_var.get()
            new_name = name.replace(f, w) if f else name
        else:
            new_name = name

        cleaned = clean_windows_filename(new_name)
        if not is_dir:
            ext = Path(name).suffix
            if ext and not cleaned.lower().endswith(ext.lower()):
                cleaned += ext
        return cleaned if cleaned else name

    def _select_preview_row(self, row_id: str):
        p = self.pal
        prev_id = self._selected_row_id
        if prev_id and prev_id in self._row_sel_bars:
            bar, orig_bg = self._row_sel_bars[prev_id]
            try:
                bar.configure(bg=orig_bg)
            except Exception:
                pass
        self._selected_row_id = row_id
        if row_id in self._row_sel_bars:
            bar, _orig_bg = self._row_sel_bars[row_id]
            try:
                bar.configure(bg=p["sel_bar"])
            except Exception:
                pass
        data = self._preview_rows_data.get(row_id)
        if data:
            cur, new = data
            self.lbl_det_before.config(text=cur)
            self.lbl_det_after.config(text=new, fg=p["aft_fg"] if new != cur else p["same_fg"])

    def _update_preview(self):
        p = self.pal
        self._update_option_highlights()
        for child in self.prev_inner.winfo_children():
            child.destroy()
        self._preview_rows_data.clear()
        self._row_sel_bars.clear()
        self._selected_row_id = None

        total_cnt = 0
        changed_cnt = 0
        rows_to_render = []
        only_changed = bool(self.only_changed_var.get())

        for rid in self.target_rids:
            it = self.parent.preview_items.get(rid)
            if not it or it.get("action") == "Gelöscht":
                continue
            total_cnt += 1
            cur = it.get("name", "")
            new = self._compute_shortened(cur, is_dir=bool(it.get("is_dir", False)))
            is_changed = (new != cur)
            if is_changed:
                changed_cnt += 1
            if only_changed and not is_changed:
                continue
            rows_to_render.append((rid, cur, new, is_changed))

        self._last_changed_count = changed_cnt

        # Status-Text (flach, klar als Anzeige erkennbar) & Primär-Button aktualisieren
        if changed_cnt > 0:
            self.lbl_prev_status.config(
                text=f"●  {changed_cnt} von {total_cnt} werden geändert",
                fg=p["delta_fg"],
            )
            lbl_btn = "Auf 1 Datei anwenden ➔" if changed_cnt == 1 else f"Auf {changed_cnt} Dateien anwenden ➔"
            self.btn_apply.update_style(
                text=lbl_btn,
                bg="#2563eb",
                fg="#ffffff",
                hover_bg="#1d4ed8",
                border_color=None,
            )
        else:
            self.lbl_prev_status.config(
                text=f"●  0 von {total_cnt} geändert",
                fg=p["muted"],
            )
            self.btn_apply.update_style(
                text="Keine Änderungen",
                bg="#e2e8f0" if not self._is_dark else "#334155",
                fg="#64748b" if not self._is_dark else "#94a3b8",
                hover_bg="#e2e8f0" if not self._is_dark else "#334155",
                border_color="#cbd5e1" if not self._is_dark else "#475569",
            )

        max_render = 150
        first_changed_id = None
        first_any_id = None

        for idx, (rid, cur, new, is_changed) in enumerate(rows_to_render[:max_render]):
            row_id = f"r_{idx}"
            self._preview_rows_data[row_id] = (cur, new)
            if first_any_id is None:
                first_any_id = row_id
            if is_changed and first_changed_id is None:
                first_changed_id = row_id

            diff = len(new) - len(cur)
            if not is_changed:
                delta_str = "Unverändert"
            elif diff < 0:
                delta_str = f"{diff} Zeichen"
            elif diff > 0:
                delta_str = f"+{diff} Zeichen"
            else:
                delta_str = "Geändert"

            if is_changed:
                bg_bef = p["bef_bg"] if idx % 2 == 0 else p["bef_bg_alt"]
                fg_bef = p["bef_fg"]
                bg_aft = p["aft_bg"] if idx % 2 == 0 else p["aft_bg_alt"]
                fg_aft = p["aft_fg"]
                arrow_ch = "➔"
                arrow_fg = p["delta_fg"]
                fg_delta = p["delta_fg"]
                aft_font = ("Segoe UI", 9, "bold")
                del_font = ("Segoe UI", 8, "bold")
            else:
                bg_bef = p["same_bg"]
                fg_bef = p["same_fg"]
                bg_aft = p["same_bg"]
                fg_aft = p["same_fg"]
                arrow_ch = "="
                arrow_fg = p["div"]
                fg_delta = p["same_fg"]
                aft_font = ("Segoe UI", 9)
                del_font = ("Segoe UI", 8)

            row_f = tk.Frame(self.prev_inner, bg=bg_bef, cursor="hand2")
            row_f.pack(fill="x")
            row_f.columnconfigure(0, weight=5, uniform="pvcol")
            row_f.columnconfigure(1, weight=0, minsize=28)
            row_f.columnconfigure(2, weight=5, uniform="pvcol")
            row_f.columnconfigure(3, weight=0, minsize=100)

            # Spalte 0: Vorher (mit linkem Auswahl-Indikator)
            c0 = tk.Frame(row_f, bg=bg_bef)
            c0.grid(row=0, column=0, sticky="nsew")
            sel_bar = tk.Frame(c0, bg=bg_bef, width=3)
            sel_bar.pack(side="left", fill="y")
            self._row_sel_bars[row_id] = (sel_bar, bg_bef)
            lbl_b = tk.Label(c0, text=cur, font=("Segoe UI", 9), bg=bg_bef, fg=fg_bef, anchor="w", width=1, padx=7, pady=4)
            lbl_b.pack(side="left", fill="both", expand=True)

            # Spalte 1: Deutliche Trennspalte mit Pfeil
            c1 = tk.Frame(row_f, bg=bg_bef)
            c1.grid(row=0, column=1, sticky="nsew")
            tk.Frame(c1, bg=p["div"], width=1).pack(side="left", fill="y")
            lbl_ar = tk.Label(c1, text=arrow_ch, font=("Segoe UI", 9, "bold"), bg=bg_bef, fg=arrow_fg, anchor="center")
            lbl_ar.pack(side="left", fill="both", expand=True)
            tk.Frame(c1, bg=p["div"], width=1).pack(side="right", fill="y")

            # Spalte 2: Nachher (farblich klar abgesetzt in Grün bei Änderung)
            lbl_a = tk.Label(row_f, text=new, font=aft_font, bg=bg_aft, fg=fg_aft, anchor="w", width=1, padx=10, pady=4)
            lbl_a.grid(row=0, column=2, sticky="nsew")

            # Spalte 3: Änderung (Zeichen-Differenz)
            c3 = tk.Frame(row_f, bg=bg_bef)
            c3.grid(row=0, column=3, sticky="nsew")
            tk.Frame(c3, bg=p["div"], width=1).pack(side="left", fill="y")
            lbl_d = tk.Label(c3, text=delta_str, font=del_font, bg=bg_bef, fg=fg_delta, anchor="center", padx=6, pady=4)
            lbl_d.pack(side="left", fill="both", expand=True)

            tk.Frame(self.prev_inner, bg=p["div"], height=1).pack(fill="x")

            for w in (row_f, c0, sel_bar, lbl_b, c1, lbl_ar, lbl_a, c3, lbl_d):
                w.bind("<Button-1>", lambda _e, rid_key=row_id: self._select_preview_row(rid_key))
                w.bind("<MouseWheel>", self._on_mousewheel)

        if len(rows_to_render) > max_render:
            more_lbl = tk.Label(
                self.prev_inner,
                text=f"… und {len(rows_to_render) - max_render} weitere Einträge (Anwenden gilt für alle {changed_cnt} geänderten Dateien)",
                font=("Segoe UI", 8),
                bg=p["card_hdr"],
                fg=p["muted"],
                pady=6,
            )
            more_lbl.pack(fill="x")
            more_lbl.bind("<MouseWheel>", self._on_mousewheel)

        if not rows_to_render:
            empty_lbl = tk.Label(
                self.prev_inner,
                text="Keine geänderten Dateinamen mit der aktuellen Einstellung.",
                font=("Segoe UI", 9),
                bg=p["card_bg"],
                fg=p["muted"],
                pady=26,
            )
            empty_lbl.pack(fill="x")

        self.prev_canvas.yview_moveto(0)

        pick_id = first_changed_id or first_any_id
        if pick_id:
            self._select_preview_row(pick_id)
        else:
            self.lbl_det_before.config(text="Keine Einträge sichtbar.")
            self.lbl_det_after.config(text="—", fg=p["muted"])

    def _apply_shortening(self):
        if self._last_changed_count <= 0:
            self.parent.show_notice(
                "Keine Änderungen",
                "Mit den aktuellen Einstellungen wird kein Dateiname verändert.",
                kind="info",
            )
            return

        mode = self.mode_var.get()
        param = None
        if mode == "subtitles":
            param = self._safe_int(self.dash_count_var, 2, 1, 10)
        elif mode == "max_len":
            param = (self._safe_int(self.max_chars_var, 50, 10, 250), bool(self.word_boundary_var.get()))
        elif mode == "replace":
            param = (self.replace_find_var.get(), self.replace_with_var.get())

        self.parent.apply_name_shortening(mode, param=param, target_rids=self.target_rids)
        self.destroy()

    def _center_dialog(self):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w = min(sw - 60, max(940, self.winfo_reqwidth() + 24))
        h = min(sh - 80, max(670, self.winfo_reqheight() + 24))
        px = self.parent.winfo_rootx()
        py = self.parent.winfo_rooty()
        pw = self.parent.winfo_width()
        ph = self.parent.winfo_height()
        x = max(30, min(sw - w - 30, px + (pw - w) // 2))
        y = max(30, min(sh - h - 50, py + (ph - h) // 2))
        self.geometry(f"{w}x{h}+{x}+{y}")


class App(tk.Tk):
    def __init__(self, startup_source: str | None = None, startup_target: str | None = None,
                 startup_handoff: str | None = None, startup_theme: str | None = None):
        super().__init__()
        self._startup_source = startup_source
        self._startup_target = startup_target
        self._startup_handoff = startup_handoff
        self._startup_theme = startup_theme if startup_theme in ("light", "dark") else None
        self.theme_mode = self._startup_theme or "light"
        self._handoff_explicit_files = None
        try:
            register_seven_folder_bridge()
        except Exception:
            pass
        self.title(f"7-Ordner-System Organizer Pro  {APP_VERSION}")
        self.minsize(980, 600)
        self.configure(bg="#f8fafc")
        apply_window_folder_icon(self, is_root=True)
        self._last_normal_geometry = None
        self._watch_fs_after_explorer = False
        self._last_scan_snapshot = None
        self._restore_window_geometry()
        self.bind("<Configure>", self._on_window_configure)
        self.bind("<FocusIn>", self._on_app_focus_in)
        self.bind("<F5>", lambda e: self.refresh_preview() if self.frame_step2.winfo_manager() else None)
        self.bind("<Control-f>", self._focus_step2_search)
        self.bind("<Control-F>", self._focus_step2_search)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # TTK Style Setup fuer moderne Komponenten
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        # Globale Design-Farben & Styles
        self.style.configure("TFrame", background="#f8fafc")
        self.style.configure("White.TFrame", background="#ffffff")
        self.style.configure("TLabel", background="#f8fafc", foreground="#1e293b", font=("Segoe UI", 9))
        self.style.configure("Header.TLabel", background="#f8fafc", foreground="#0f172a", font=("Segoe UI", 16, "bold"))
        self.style.configure("SubHeader.TLabel", background="#f8fafc", foreground="#475569", font=("Segoe UI", 10))
        self.style.configure("Hint.TLabel", background="#f8fafc", foreground="#64748b", font=("Segoe UI", 9, "italic"))
        
        self.style.configure("Card.TLabel", background="#ffffff", foreground="#1e293b", font=("Segoe UI", 9))
        self.style.configure("TRadiobutton", background="#ffffff", foreground="#1e293b", font=("Segoe UI", 10))
        self.style.configure("TCheckbutton", background="#ffffff", foreground="#1e293b", font=("Segoe UI", 10))

        # Moderne Baumansicht (Spacious, Windows 11 Fluent mit klar abgegrenzten Spaltenkoepfen)
        self.style.configure("Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground="#0f172a",
            rowheight=28,
            font=("Segoe UI", 9),
            borderwidth=0,
            relief="flat"
        )
        self.style.configure("Treeview.Heading",
            background="#f1f5f9",
            foreground="#1e293b",
            font=("Segoe UI", 9, "bold"),
            bordercolor="#cbd5e1",
            lightcolor="#cbd5e1",
            darkcolor="#cbd5e1",
            borderwidth=1,
            relief="solid",
            padding=(6, 6)
        )
        self.style.map("Treeview.Heading",
            background=[("active", "#e2e8f0"), ("pressed", "#cbd5e1")],
            bordercolor=[("active", "#94a3b8")]
        )

        # State Variables
        self.source_dir = tk.StringVar(value=r"C:\Users\01\Sortier_Test")
        self.target_dir = tk.StringVar(value=r"C:\Users\01\Sortier_Test_01")
        self.target_mode = "adjacent"
        self.action_mode = tk.StringVar(value="copy")  # "copy" oder "move"
        self.file_filter_mode = tk.StringVar(value="all")  # Legacy
        self.filter_all_var = tk.BooleanVar(value=True)  # Standard: Alle Dateien aktiv
        self.filter_images_var = tk.BooleanVar(value=False)
        self.filter_text_docs_var = tk.BooleanVar(value=False)
        self.filter_office_var = tk.BooleanVar(value=False)
        self.filter_media_var = tk.BooleanVar(value=False)
        self.filter_installers_var = tk.BooleanVar(value=False)
        self.subfolder_mode = tk.StringVar(value="split")  # "split", "whole", "ignore"
        self.recursive_var = tk.BooleanVar(value=True)
        self.include_installers_var = tk.BooleanVar(value=False)
        self.include_media_var = tk.BooleanVar(value=False)
        self.include_onedrive_var = tk.BooleanVar(value=False)
        self.exclude_system_dirs_var = tk.BooleanVar(value=True)
        self.network_guard_var = tk.BooleanVar(value=True)
        self.date_from_var = tk.StringVar(value="")
        self.date_to_var = tk.StringVar(value="")
        self.keyword_filter_var = tk.StringVar(value="")
        self.min_size_var = tk.StringVar(value="")
        self._calendar_popup = None
        self.date_from_var.trace_add("write", lambda *_a: self.update_date_preset_ui())
        self.date_to_var.trace_add("write", lambda *_a: self.update_date_preset_ui())
        self.min_size_var.trace_add("write", lambda *_a: self.update_size_preset_ui())

        self.preview_items = {}  # tree_id -> item_dict
        self.duplicate_ids = []  # list of tree_ids of duplicates
        self._preview_row_order = []  # geordnete tree_ids fuer verlustfreie Filter/Sortierung
        self.step2_search_var = tk.StringVar(value="")
        self.step2_filter_chip = "all"
        self.step2_file_filter_mode = "all"  # "all", "no_images", "only_images", "installers", "media"
        self.step2_active_types = set()  # set of active types in step 2 (empty = "all")
        self.step2_only_onedrive = False
        self.step2_date_from_var = tk.StringVar(value="")
        self.step2_date_to_var = tk.StringVar(value="")
        self.step2_min_size_var = tk.StringVar(value="")
        self._suppress_step2_filter_trace = False
        self._step2_filter_after_id = None
        self.step2_search_var.trace_add("write", lambda *_a: self._schedule_step2_filter())
        self.step2_date_from_var.trace_add("write", lambda *_a: self._on_step2_detail_var_change())
        self.step2_date_to_var.trace_add("write", lambda *_a: self._on_step2_detail_var_change())
        self.step2_min_size_var.trace_add("write", lambda *_a: self._on_step2_detail_var_change())
        self.dup_bulk_mode = "skip"  # "skip", "copy", "delete"
        self.dup_original_strategy = "clean_name"  # "clean_name", "newest", "oldest", "shortest_path"
        self._last_explorer_target = ""
        self.recent_target_folders = []  # max. 5 Zielordner: [{"path": str, "pinned": bool, "label": str, "timestamp": float}]
        self._is_scanning = False
        self._scan_cancel_event = None
        self._active_popup_menu = None
        self.preview_visible = False
        self._last_filter_tag = ""
        self._custom_col_widths = False
        self._resizing_separator = False
        self._restoring_state = False

        # Spaltenkonfiguration inklusive Grösse, Elemente und Original / Duplikat
        self.col_keys = ["file", "ext", "size", "items_count", "dup_status", "category", "target_folder", "date", "source_sub", "action"]
        self.col_titles = {
            "file": "Dateiname",
            "ext": "Typ",
            "size": "Grösse",
            "items_count": "Elemente",
            "dup_status": "Original / Duplikat",
            "category": "Erkannte Kategorie",
            "target_folder": "Zielordner",
            "date": "Änderungsdatum",
            "source_sub": "Gefunden in (Pfad)",
            "action": "Aktion"
        }
        self.col_widths = {
            "file": 210,
            "ext": 52,
            "size": 86,
            "items_count": 82,
            "dup_status": 135,
            "category": 165,
            "target_folder": 195,
            "date": 120,
            "source_sub": 185,
            "action": 95
        }
        self.selected_col_key = "file"

        # Drag & Drop Tracking
        self.drag_start_x = 0
        self.drag_start_col_idx = None

        # Container
        self.container = tk.Frame(self, bg="#f8fafc", padx=16, pady=16)
        self.container.pack(fill="both", expand=True)

        # Phasen-Frames
        self.frame_step1 = tk.Frame(self.container, bg="#f8fafc")
        self.frame_step2 = tk.Frame(self.container, bg="#f8fafc")
        self.frame_step3 = tk.Frame(self.container, bg="#f8fafc")

        self.target_mode = "adjacent"  # "adjacent", "subfolder", "custom"

        self.build_step1()
        self.build_step2()
        self.build_step3()
        self._restore_app_state()

        # Reaktive Synchronisation der Vorauswahl
        self.source_dir.trace_add("write", lambda *a: self.sync_target_mode_ui())
        self.target_dir.trace_add("write", lambda *a: self.sync_target_mode_ui())
        self.update_target_mode_ui()

        self.show_step1()

        if self._startup_theme in ("light", "dark"):
            self.apply_theme(self._startup_theme, save=False)
        elif self.theme_mode == "dark":
            self.apply_theme("dark", save=False)

        if startup_handoff:
            self.after(140, lambda hf=startup_handoff: self.load_handoff_file(hf))
            self.after(220, lambda: bring_window_to_front(self))
        elif startup_source:
            self._apply_startup_source(startup_source, startup_target)
            self.after(220, lambda: bring_window_to_front(self))

        self.after(250, self._start_live_handoff_watcher)

    def _apply_ttk_theme(self, mode: str):
        is_dark = (mode == "dark")
        p = THEME_PALETTES["dark" if is_dark else "light"]
        self.style.configure("TFrame", background=p["bg_app"])
        self.style.configure("White.TFrame", background=p["bg_card"])
        self.style.configure("TLabel", background=p["bg_app"], foreground=p["fg_secondary"], font=("Segoe UI", 9))
        self.style.configure("Header.TLabel", background=p["bg_app"], foreground=p["fg_primary"], font=("Segoe UI", 16, "bold"))
        self.style.configure("SubHeader.TLabel", background=p["bg_app"], foreground=p["fg_muted"], font=("Segoe UI", 10))
        self.style.configure("Hint.TLabel", background=p["bg_app"], foreground=p["fg_hint"], font=("Segoe UI", 9, "italic"))
        self.style.configure("Card.TLabel", background=p["bg_card"], foreground=p["fg_secondary"], font=("Segoe UI", 9))
        self.style.configure("TRadiobutton", background=p["bg_card"], foreground=p["fg_secondary"], font=("Segoe UI", 10))
        self.style.configure("TCheckbutton", background=p["bg_card"], foreground=p["fg_secondary"], font=("Segoe UI", 10))
        self.style.configure(
            "Treeview",
            background=p["bg_card"],
            fieldbackground=p["bg_card"],
            foreground=p["fg_primary"],
            rowheight=28,
            font=("Segoe UI", 9),
            borderwidth=0,
            relief="flat",
        )
        self.style.map(
            "Treeview",
            background=[("selected", "#1d4ed8" if is_dark else "#2563eb")],
            foreground=[("selected", "#ffffff")],
        )
        self.style.configure(
            "Treeview.Heading",
            background=p["bg_subtle"],
            foreground=p["fg_secondary"],
            font=("Segoe UI", 9, "bold"),
            bordercolor=p["border"],
            lightcolor=p["border"],
            darkcolor=p["border"],
            borderwidth=1,
            relief="solid",
            padding=(6, 6),
        )
        self.style.map(
            "Treeview.Heading",
            background=[("active", p["bg_hover"]), ("pressed", p["border"])],
            bordercolor=[("active", p["fg_subtle"])],
        )
        self.style.configure(
            "Vertical.TScrollbar",
            background=p["bg_subtle"],
            troughcolor=p["bg_app"],
            bordercolor=p["border"],
            arrowcolor=p["fg_muted"],
        )
        self.style.configure(
            "Horizontal.TScrollbar",
            background=p["bg_subtle"],
            troughcolor=p["bg_app"],
            bordercolor=p["border"],
            arrowcolor=p["fg_muted"],
        )
        if getattr(self, "tree", None) is not None:
            if is_dark:
                self.tree.tag_configure("tag_orig", background="#1e3a8a", foreground="#dbeafe")
                self.tree.tag_configure("tag_dup", background="#431407", foreground="#fdba74")
                self.tree.tag_configure("tag_dup_copy", background="#14532d", foreground="#86efac")
                self.tree.tag_configure("tag_del", background="#450a0a", foreground="#fca5a5")
                self.tree.tag_configure("tag_cloud", background="#082f49", foreground="#7dd3fc")
                self.tree.tag_configure("tag_normal", background="#1e293b", foreground="#f8fafc")
            else:
                self.tree.tag_configure("tag_orig", background="#dbeafe", foreground="#1e40af")
                self.tree.tag_configure("tag_dup", background="#ffedd5", foreground="#c2410c")
                self.tree.tag_configure("tag_dup_copy", background="#dcfce7", foreground="#15803d")
                self.tree.tag_configure("tag_del", background="#fee2e2", foreground="#b91c1c")
                self.tree.tag_configure("tag_cloud", background="#f0f9ff", foreground="#0369a1")
                self.tree.tag_configure("tag_normal", background="#ffffff", foreground="#0f172a")

    def apply_theme(self, mode: str, save: bool = True, sync_suchmaschine: bool = False):
        """Schaltet das komplette Design zwischen Light Fluent und Portfolio-Manager Dark Mode um."""
        global CURRENT_THEME_MODE
        clean_mode = "dark" if str(mode or "").strip().lower() == "dark" else "light"
        CURRENT_THEME_MODE = clean_mode
        self.theme_mode = clean_mode
        self._apply_ttk_theme(clean_mode)
        apply_theme_to_widget_tree(self, clean_mode)
        apply_dwm_dark_titlebar(self, clean_mode == "dark")

        next_label = "☀️ Hell" if clean_mode == "dark" else "🌙 Dark"
        for btn_attr in ("btn_theme_step1", "btn_theme_step2"):
            btn = getattr(self, btn_attr, None)
            if btn is not None:
                try:
                    btn.update_style(text=next_label)
                except Exception:
                    pass
        try:
            self.refresh_git_badge()
        except Exception:
            pass

        if save:
            set_shared_bridge_theme(clean_mode)
            if not getattr(self, "_restoring_state", False):
                self._save_app_state()
        if sync_suchmaschine:
            def _notify_sm():
                try:
                    port = int(find_suchmaschine_app().get("port") or 5005)
                    url = f"http://127.0.0.1:{port}/api/bridge/theme"
                    req = urllib.request.Request(
                        url,
                        data=json.dumps({"theme": clean_mode}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=0.6):
                        pass
                except Exception:
                    pass
            threading.Thread(target=_notify_sm, daemon=True).start()

    def toggle_theme(self):
        next_mode = "light" if getattr(self, "theme_mode", "light") == "dark" else "dark"
        self.apply_theme(next_mode, save=True, sync_suchmaschine=True)

    # -------------------------------------------------------------
    # SCHRITT 1: Auswahl & Konfiguration
    # -------------------------------------------------------------
    def build_step1(self):
        header_row = tk.Frame(self.frame_step1, bg="#f8fafc")
        header_row.pack(fill="x", pady=(0, 2))
        title = tk.Label(header_row, text="7-Ordner-System: Dokumenten-Sortierer", font=("Segoe UI", 16, "bold"), bg="#f8fafc", fg="#0f172a")
        title.pack(side="left")
        ver_badge = tk.Label(header_row, text=APP_VERSION, font=("Segoe UI", 9, "bold"), bg="#e2e8f0", fg="#0f172a", padx=8, pady=2)
        ver_badge.pack(side="left", padx=(10, 0), pady=(3, 0))
        self.btn_info_step1 = ModernRoundedButton(
            header_row,
            text="ⓘ Info / Funktionsweise",
            command=self.show_app_info,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=5,
        )
        self.btn_info_step1.pack(side="right")
        attach_tooltip(self.btn_info_step1, "Erklärung: Funktionsweise & Duplikaterkennung")
        self.btn_theme_step1 = ModernRoundedButton(
            header_row,
            text="🌙 Dark",
            command=self.toggle_theme,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=11,
            pady=5,
        )
        self.btn_theme_step1.pack(side="right", padx=(0, 8))
        attach_tooltip(self.btn_theme_step1, "Zwischen hellem und dunklem Design wechseln")
        self.btn_git_step1 = ModernRoundedButton(
            header_row,
            text=f"⬆ GitHub {APP_VERSION}",
            command=self.open_github_sync_dialog,
            bg="#eef2ff",
            fg="#3730a3",
            hover_bg="#e0e7ff",
            border_color="#a5b4fc",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=11,
            pady=5,
        )
        self.btn_git_step1.pack(side="right", padx=(0, 8))
        attach_tooltip(self.btn_git_step1, "GitHub Versions-Aktualisierung, USB-Stick EXE-Download & Retour")
        self.btn_admin_pw = ModernRoundedButton(
            header_row,
            text="🔑 IT-Passwort",
            command=self.open_change_admin_password_dialog,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=5,
        )
        self.btn_admin_pw.pack(side="right", padx=(0, 8))
        attach_tooltip(self.btn_admin_pw, "IT-Passwort für NetzwerkSchutz & Systemschutz ändern (Standard: 1234)")
        self.btn_open_search_app = ModernRoundedButton(
            header_row,
            text="🔍 Suchmaschine",
            command=lambda: self.open_in_suchmaschine(None),
            bg="#ecfeff",
            fg="#0f766e",
            hover_bg="#cffafe",
            border_color="#99f6e4",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=5,
        )
        self.btn_open_search_app.pack(side="right", padx=(0, 8))
        attach_tooltip(self.btn_open_search_app, "Datei-Suchmaschine Pro öffnen (startet bei Bedarf automatisch)")
        subtitle = tk.Label(self.frame_step1, text="Wählen Sie den Quellordner oder eine Festplatte aus, die analysiert und nach dem 7-Ordner-System sortiert werden soll.", font=("Segoe UI", 10), bg="#f8fafc", fg="#475569")
        subtitle.pack(anchor="w", pady=(0, 14))

        # 1. Quellordner Card
        card_src = tk.Frame(self.frame_step1, bg="#ffffff", highlightthickness=1, highlightbackground="#e2e8f0", padx=16, pady=12)
        card_src.pack(fill="x", pady=4)

        tk.Label(card_src, text="1. Zu sortierender Quellordner / Festplatte", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#0f172a").pack(anchor="w", pady=(0, 6))
        row_src = tk.Frame(card_src, bg="#ffffff")
        row_src.pack(fill="x")
        
        ent_src = tk.Entry(row_src, textvariable=self.source_dir, font=("Segoe UI", 10), bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        ent_src.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=4)
        ModernRoundedButton(row_src, "Durchsuchen...", command=self.browse_source, bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"), r=8, padx=14, pady=5).pack(side="right")

        # 2. Zielordner Card
        card_dest = tk.Frame(self.frame_step1, bg="#ffffff", highlightthickness=1, highlightbackground="#e2e8f0", padx=16, pady=12)
        card_dest.pack(fill="x", pady=8)

        tk.Label(card_dest, text="2. Zielverzeichnis (7-Ordner-Struktur)", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#0f172a").pack(anchor="w", pady=(0, 6))
        row_dest = tk.Frame(card_dest, bg="#ffffff")
        row_dest.pack(fill="x")
        
        ent_dest = tk.Entry(row_dest, textvariable=self.target_dir, font=("Segoe UI", 10), bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        ent_dest.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=4)
        ModernRoundedButton(row_dest, "Durchsuchen...", command=self.browse_target, bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"), r=8, padx=14, pady=5).pack(side="right")

        # Einheitliche Pill-Breiten berechnen (damit Buttons in Groesse, Icon-X und Spalten exakt fluchten)
        f_pill_bold = tkfont.Font(font=("Segoe UI", 9, "bold"))
        quick_labels = ["Neben Original (Pfad_01)", "Als Unterordner (Pfad\\_01)", "Im gleichen Ordner"]
        quick_min_w = max(14 + 18 + 9 + f_pill_bold.measure(lbl) + 14 for lbl in quick_labels)

        # Schnellwahl-Buttons mit aktiver Farb-Hervorhebung
        quick_row = tk.Frame(card_dest, bg="#ffffff")
        quick_row.pack(fill="x", pady=(10, 0))
        tk.Label(quick_row, text="Schnellwahl:", width=12, anchor="w", font=("Segoe UI", 9, "italic"), bg="#ffffff", fg="#64748b").pack(side="left", padx=(0, 8))
        self.btn_target_adjacent = ModernRoundedButton(
            quick_row,
            text="Neben Original (Pfad_01)",
            command=self.set_target_adjacent,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=6,
            icon_kind="folder_adjacent",
            min_width=quick_min_w
        )
        self.btn_target_adjacent.pack(side="left", padx=(0, 10))

        self.btn_target_subfolder = ModernRoundedButton(
            quick_row,
            text="Als Unterordner (Pfad\\_01)",
            command=self.set_target_subfolder,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=6,
            icon_kind="folder_sub",
            min_width=quick_min_w
        )
        self.btn_target_subfolder.pack(side="left", padx=(0, 10))

        self.btn_target_same = ModernRoundedButton(
            quick_row,
            text="Im gleichen Ordner",
            command=self.set_target_same,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=6,
            icon_kind="folder_same",
            min_width=quick_min_w
        )
        self.btn_target_same.pack(side="left", padx=(0, 10))

        self.btn_target_usb = ModernRoundedButton(
            quick_row,
            text="USB_Stick",
            command=self.set_target_usb,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=6,
            icon_kind="usb_stick",
        )
        self.btn_target_usb.pack(side="left", padx=(0, 10))
        attach_tooltip(self.btn_target_usb, "Erkennt den USB-Stick automatisch (egal ob E:, F:, G: ...) und setzt X:\\USB_Stick als Ziel")

        self.btn_target_onedrive = ModernRoundedButton(
            quick_row,
            text="OneDrive",
            command=self.set_target_onedrive,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=6,
            icon_kind="cloud_onedrive",
        )
        self.btn_target_onedrive.pack(side="left", padx=(0, 10))
        attach_tooltip(self.btn_target_onedrive, "Erkennt den Microsoft OneDrive-Ordner und setzt ihn als Zielverzeichnis")

        self.btn_target_recent = ModernRoundedButton(
            quick_row,
            text="🕒 Letzte Ordner ▾",
            command=self.open_step1_recent_targets_menu,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=6,
            icon_kind="folder_adjacent",
        )
        self.btn_target_recent.pack(side="left")
        attach_tooltip(self.btn_target_recent, "Schnellauswahl: Einen der letzten 5 genutzten Zielordner als Zielverzeichnis setzen")

        # 3. Optionen Card
        card_opt = tk.Frame(self.frame_step1, bg="#ffffff", highlightthickness=1, highlightbackground="#e2e8f0", padx=16, pady=12)
        card_opt.pack(fill="x", pady=6)

        tk.Label(card_opt, text="3. Optionen & Kopiermodus", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#0f172a").pack(anchor="w", pady=(0, 6))
        
        opt_mode = tk.Frame(card_opt, bg="#ffffff")
        opt_mode.pack(fill="x", pady=(0, 4))
        ttk.Radiobutton(opt_mode, text="Kopieren (Empfohlen: Originaldateien bleiben unberührt)", variable=self.action_mode, value="copy").pack(anchor="w", pady=3)
        ttk.Radiobutton(opt_mode, text="Verschieben (Verschiebt Dateien direkt & räumt Quellordner auf)", variable=self.action_mode, value="move").pack(anchor="w", pady=3)

        sep = tk.Frame(card_opt, bg="#e2e8f0", height=1)
        sep.pack(fill="x", pady=8)

        subfolder_specs = [
            ("split", "Inhalte einzeln", "folder_split", "Durchsucht alle Unterordner und sortiert jede Datei einzeln nach Kategorie"),
            ("whole", "Als Ganzes", "folder_whole", "Hält jeden Unterordner komplett zusammen – Dateien bleiben im selben Ordner"),
            ("ignore", "Ignorieren", "folder_ignore", "Sortiert nur lose Dateien im Hauptordner – alle Unterordner bleiben unberührt"),
        ]
        filter_specs_row2 = [
            ("all", "Alle Dateien", "all_files", "Alle unterstützten Dateitypen einbeziehen (Standard)"),
            ("images", "Bilder", "images_only", "Bilder einbeziehen: .jpg, .png, .gif, .webp, .svg, .raw etc."),
            ("text_docs", "Texte & Docs", "doc_only", "Dokumente & Texte einbeziehen: .txt, .pdf, .doc, .rtf, .scan, .epub etc."),
        ]
        filter_specs_row3 = [
            ("office", "Office-Dateien", "office_doc", "Office-Dokumente einbeziehen: .docx, .xlsx, .pptx, .odt, .ods, .csv etc."),
            ("media", "Videos & Audio", "media_video", "Medien einbeziehen: .mp4, .mkv, .avi, .mp3, .wav etc."),
            ("installers", "Programme", "installer_app", "Installationsprogramme einbeziehen: .exe, .msi, .msix, .iso etc."),
        ]
        security_labels = ["NetzwerkSchutz", "Systemschutz"]
        all_labels_to_measure = (
            [lbl for _, lbl, _, _ in subfolder_specs] +
            [lbl for _, lbl, _, _ in filter_specs_row2] +
            [lbl for _, lbl, _, _ in filter_specs_row3] +
            security_labels
        )
        opt_min_w = max(14 + 18 + 9 + f_pill_bold.measure(lbl) + 14 for lbl in all_labels_to_measure)

        subfolder_row = tk.Frame(card_opt, bg="#ffffff")
        subfolder_row.pack(fill="x", pady=(2, 4))
        tk.Label(subfolder_row, text="Unterordner:", width=12, anchor="w", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155").pack(side="left", padx=(0, 8))

        self.subfolder_buttons = {}
        for idx, (mode_key, label, icon_k, sub_tip) in enumerate(subfolder_specs):
            btn = ModernRoundedButton(
                subfolder_row,
                text=label,
                command=lambda m=mode_key: self.set_subfolder_mode(m),
                bg="#2563eb" if mode_key == "split" else "#ffffff",
                fg="#ffffff" if mode_key == "split" else "#475569",
                hover_bg="#1d4ed8" if mode_key == "split" else "#f1f5f9",
                border_color=None if mode_key == "split" else "#cbd5e1",
                font=("Segoe UI", 9, "bold") if mode_key == "split" else ("Segoe UI", 9),
                r=8,
                padx=14,
                pady=6,
                icon_kind=icon_k,
                min_width=opt_min_w
            )
            btn.pack(side="left", padx=(0, 0 if idx == 2 else 10))
            attach_tooltip(btn, sub_tip)
            self.subfolder_buttons[mode_key] = btn

        tk.Frame(subfolder_row, bg="#e2e8f0", width=1).pack(side="left", fill="y", padx=14)
        tk.Label(
            subfolder_row,
            text="🔒 Sicherheit (IT):",
            font=("Segoe UI", 9, "bold"),
            bg="#ffffff",
            fg="#475569",
            anchor="w",
        ).pack(side="left")

        filter_row = tk.Frame(card_opt, bg="#ffffff")
        filter_row.pack(fill="x", pady=(4, 4))
        tk.Label(filter_row, text="Dateifilter:", width=12, anchor="w", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155").pack(side="left", padx=(0, 8))

        self.filter_buttons = {}
        for idx, (f_key, f_lbl, f_icon, f_tip) in enumerate(filter_specs_row2):
            btn = ModernRoundedButton(
                filter_row,
                text=f_lbl,
                command=lambda k=f_key: self.toggle_file_filter(k),
                bg="#2563eb" if f_key == "all" else "#ffffff",
                fg="#ffffff" if f_key == "all" else "#475569",
                hover_bg="#1d4ed8" if f_key == "all" else "#f1f5f9",
                border_color=None if f_key == "all" else "#cbd5e1",
                font=("Segoe UI", 9, "bold") if f_key == "all" else ("Segoe UI", 9),
                r=8,
                padx=14,
                pady=6,
                icon_kind=f_icon,
                min_width=opt_min_w,
            )
            btn.pack(side="left", padx=(0, 0 if idx == 2 else 10))
            attach_tooltip(btn, f_tip)
            self.filter_buttons[f_key] = btn

        tk.Frame(filter_row, bg="#e2e8f0", width=1).pack(side="left", fill="y", padx=14)
        self.btn_extra_firmguard = ModernRoundedButton(
            filter_row,
            text="NetzwerkSchutz",
            command=self.toggle_network_guard,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            border_color=None,
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=6,
            icon_kind="shield_firm",
            min_width=opt_min_w,
        )
        self.btn_extra_firmguard.pack(side="left", padx=(0, 0))
        attach_tooltip(self.btn_extra_firmguard, "🔒 Passwortgeschützt: Verhindert Löschen ohne Papierkorb auf Netzlaufwerken & warnt vor Verschieben aus Team-Ordnern")

        extra_row = tk.Frame(card_opt, bg="#ffffff")
        extra_row.pack(fill="x", pady=(4, 6))
        tk.Label(extra_row, text="Zusätzlich:", width=12, anchor="w", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155").pack(side="left", padx=(0, 8))

        for idx, (f_key, f_lbl, f_icon, f_tip) in enumerate(filter_specs_row3):
            btn = ModernRoundedButton(
                extra_row,
                text=f_lbl,
                command=lambda k=f_key: self.toggle_file_filter(k),
                bg="#ffffff",
                fg="#475569",
                hover_bg="#f1f5f9",
                border_color="#cbd5e1",
                font=("Segoe UI", 9),
                r=8,
                padx=14,
                pady=6,
                icon_kind=f_icon,
                min_width=opt_min_w,
            )
            btn.pack(side="left", padx=(0, 0 if idx == 2 else 10))
            attach_tooltip(btn, f_tip)
            self.filter_buttons[f_key] = btn

        # Kompatibilitäts-Aliase
        self.btn_extra_installers = self.filter_buttons["installers"]
        self.btn_extra_media = self.filter_buttons["media"]

        tk.Frame(extra_row, bg="#e2e8f0", width=1).pack(side="left", fill="y", padx=14)
        self.btn_extra_sysguard = ModernRoundedButton(
            extra_row,
            text="Systemschutz",
            command=self.toggle_exclude_system_dirs,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            border_color=None,
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=6,
            icon_kind="shield_sys",
            min_width=opt_min_w,
        )
        self.btn_extra_sysguard.pack(side="left", padx=(0, 0))
        attach_tooltip(self.btn_extra_sysguard, "🔒 Passwortgeschützt: System-, Treiber- & Entwicklerordner bei C:\\-Scans überspringen (z. B. ProgramData, Python3*, __pycache__, .venv)")

        tk.Frame(card_opt, bg="#e2e8f0", height=1).pack(fill="x", pady=(6, 8))

        opt_h = max(10, f_pill_bold.metrics("linespace") + 6 * 2 + 4)
        total_3col = 3 * opt_min_w + 2 * 10

        preset_specs = [
            ("today", "Heute"),
            ("week", "Diese Woche"),
            ("month", "Dieser Monat"),
            ("year", "Dieses Jahr"),
        ]
        preset_gap = 8
        preset_min_w = max(
            max(f_pill_bold.measure(lbl) + 20 for _, lbl in preset_specs),
            (total_3col - 3 * preset_gap) // 4,
        )

        preset_row = tk.Frame(card_opt, bg="#ffffff")
        preset_row.pack(fill="x", pady=(2, 4))
        tk.Label(preset_row, text="Zeitraum:", width=12, anchor="w", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155").pack(side="left", padx=(0, 8))

        self.date_preset_buttons = {}
        for p_key, p_label in preset_specs:
            p_btn = ModernRoundedButton(
                preset_row,
                text=p_label,
                command=lambda k=p_key: self.set_date_preset(k),
                bg="#ffffff",
                fg="#475569",
                hover_bg="#f1f5f9",
                border_color="#cbd5e1",
                font=("Segoe UI", 9),
                r=8,
                padx=10,
                pady=6,
                min_width=preset_min_w,
            )
            p_btn.pack(side="left", padx=(0, preset_gap))
            self.date_preset_buttons[p_key] = p_btn

        kw_row = tk.Frame(card_opt, bg="#ffffff")
        kw_row.pack(fill="x", pady=(4, 6))
        tk.Label(kw_row, text="Suchfilter:", width=12, anchor="w", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155").pack(side="left", padx=(0, 8))

        self.box_kw_filter = create_filter_box(
            kw_row,
            textvariable=self.keyword_filter_var,
            width=opt_min_w,
            height=opt_h,
            placeholder="z. B. Berge, Alm",
            on_commit=self.generate_preview,
            on_focus_save=self._save_app_state,
        )
        self.box_kw_filter.pack(side="left", padx=(0, 10))
        self.ent_kw = self.box_kw_filter.entry

        self.box_date_from = create_filter_box(
            kw_row,
            textvariable=self.date_from_var,
            width=opt_min_w,
            height=opt_h,
            placeholder="TT.MM.JJJJ",
            prefix="Von",
            on_calendar=lambda w: self.open_date_picker(w, self.date_from_var),
            on_commit=self.generate_preview,
            on_focus_save=self._save_app_state,
        )
        self.box_date_from.pack(side="left", padx=(0, 10))
        self.ent_from = self.box_date_from.entry
        if self.box_date_from.calendar_button is not None:
            attach_tooltip(self.box_date_from.calendar_button, "Datumsauswahl anzeigen")

        self.box_date_to = create_filter_box(
            kw_row,
            textvariable=self.date_to_var,
            width=opt_min_w,
            height=opt_h,
            placeholder="TT.MM.JJJJ",
            prefix="Bis",
            on_calendar=lambda w: self.open_date_picker(w, self.date_to_var),
            on_commit=self.generate_preview,
            on_focus_save=self._save_app_state,
        )
        self.box_date_to.pack(side="left", padx=(0, 10))
        self.ent_to = self.box_date_to.entry
        if self.box_date_to.calendar_button is not None:
            attach_tooltip(self.box_date_to.calendar_button, "Datumsauswahl anzeigen")

        self.btn_clear_filters = ModernRoundedButton(
            kw_row,
            text="Filter leeren",
            command=self.clear_extra_filters,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=14,
            pady=6,
        )
        self.btn_clear_filters.pack(side="left")

        size_row = tk.Frame(card_opt, bg="#ffffff")
        size_row.pack(fill="x", pady=(4, 6))
        tk.Label(size_row, text="Dateigröße:", width=12, anchor="w", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155").pack(side="left", padx=(0, 8))

        self.box_min_size = create_filter_box(
            size_row,
            textvariable=self.min_size_var,
            width=opt_min_w,
            height=opt_h,
            placeholder="z. B. 100 KB, 1 MB",
            prefix="Ab",
            on_commit=self.generate_preview,
            on_focus_save=self._save_app_state,
        )
        self.box_min_size.pack(side="left", padx=(0, 10))
        self.ent_min_size = self.box_min_size.entry

        size_preset_specs = [
            ("100 KB", "> 100 KB"),
            ("500 KB", "> 500 KB"),
            ("1 MB", "> 1 MB"),
        ]
        size_gap = 8
        size_span = 2 * opt_min_w + 10
        size_btn_w = max(72, (size_span - 2 * size_gap) // 3)
        self.size_preset_buttons = {}
        for idx, (s_val, s_lbl) in enumerate(size_preset_specs):
            s_btn = ModernRoundedButton(
                size_row,
                text=s_lbl,
                command=lambda v=s_val: self.set_size_preset(v),
                bg="#ffffff",
                fg="#475569",
                hover_bg="#f1f5f9",
                border_color="#cbd5e1",
                font=("Segoe UI", 9),
                r=8,
                padx=8,
                pady=6,
                min_width=size_btn_w,
            )
            s_btn.pack(side="left", padx=(0, size_gap if idx < len(size_preset_specs) - 1 else 0))
            self.size_preset_buttons[s_val] = s_btn

        # Hinweis Duplikaterkennung
        hint_dup = tk.Label(
            card_opt,
            text="💡 Duplikaterkennung ist aktiv: In Schritt 2 werden doppelte Dateien farblich hervorgehoben.\nDort können Sie frei wählen: Alle überspringen, alle mitkopieren oder direkt löschen.",
            font=("Segoe UI", 9),
            bg="#f0f9ff",
            fg="#0369a1",
            justify="left",
            anchor="w",
            padx=12,
            pady=8
        )
        hint_dup.pack(fill="x", pady=(8, 2))

        # Bottom Bar mit abgerundeten modernen Buttons (optisch & in Hoehe identisch zu Schritt 2)
        btn_bar = tk.Frame(self.frame_step1, bg="#f8fafc")
        btn_bar.pack(fill="x", pady=(18, 0))

        step1_nav_h = tkfont.Font(font=("Segoe UI", 9, "bold")).metrics("linespace") * 2 + 5 * 2 + 4

        ModernRoundedButton(
            btn_bar,
            text="↺ Letzte Aktion rückgängig machen",
            command=self.revert_last_transaction,
            bg="#eff6ff",
            fg="#1d4ed8",
            hover_bg="#dbeafe",
            border_color="#93c5fd",
            font=("Segoe UI", 9, "bold"),
            r=10,
            padx=18,
            pady=8,
            min_height=step1_nav_h,
        ).pack(side="left")

        ModernRoundedButton(
            btn_bar,
            text="Vorschau vor Ausführung generieren ➔",
            command=self.generate_preview,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            font=("Segoe UI", 10, "bold"),
            r=10,
            padx=22,
            pady=10,
            min_height=step1_nav_h,
        ).pack(side="right")

    # -------------------------------------------------------------
    # SCHRITT 2: Interaktive Vorschau & Bestaetigung
    # -------------------------------------------------------------
    def build_step2(self):
        step2_header = tk.Frame(self.frame_step2, bg="#f8fafc")
        step2_header.pack(fill="x", pady=(0, 2))

        title = tk.Label(step2_header, text="Schritt 2: Vorschau vor Ausführung", font=("Segoe UI", 16, "bold"), bg="#f8fafc", fg="#0f172a")
        title.pack(side="left")

        self.btn_refresh = ModernRoundedButton(
            step2_header,
            text="🔄 Aktualisieren",
            command=self.refresh_preview,
            bg="#ffffff",
            fg="#0f766e",
            hover_bg="#ccfbf1",
            border_color="#99f6e4",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=5
        )
        self.btn_refresh.pack(side="right", padx=(6, 0))

        self.btn_info_step2 = ModernRoundedButton(
            step2_header,
            text="ⓘ Info / Funktionsweise",
            command=self.show_app_info,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=5,
        )
        self.btn_info_step2.pack(side="right")
        attach_tooltip(self.btn_info_step2, "Erklärung: Funktionsweise & Duplikaterkennung")
        self.btn_theme_step2 = ModernRoundedButton(
            step2_header,
            text="🌙 Dark",
            command=self.toggle_theme,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=11,
            pady=5,
        )
        self.btn_theme_step2.pack(side="right", padx=(0, 8))
        attach_tooltip(self.btn_theme_step2, "Zwischen hellem und dunklem Design wechseln")
        self.btn_git_step2 = ModernRoundedButton(
            step2_header,
            text=f"⬆ GitHub {APP_VERSION}",
            command=self.open_github_sync_dialog,
            bg="#eef2ff",
            fg="#3730a3",
            hover_bg="#e0e7ff",
            border_color="#a5b4fc",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=11,
            pady=5,
        )
        self.btn_git_step2.pack(side="right", padx=(0, 8))
        attach_tooltip(self.btn_git_step2, "GitHub Versions-Aktualisierung, USB-Stick EXE-Download & Retour")

        self.lbl_target_info = tk.Label(self.frame_step2, text="", font=("Segoe UI", 10, "bold"), bg="#f8fafc", fg="#2563eb")
        self.lbl_target_info.pack(anchor="w", pady=(0, 2))

        self.lbl_summary = tk.Label(self.frame_step2, text="", font=("Segoe UI", 10), bg="#f8fafc", fg="#475569")
        self.lbl_summary.pack(anchor="w", pady=(0, 8))

        # Toolbar Card (Duplikate, Filter & Aktionen klar abgegrenzt)
        tb_card = tk.Frame(self.frame_step2, bg="#ffffff", highlightthickness=1, highlightbackground="#94a3b8", padx=12, pady=10)
        tb_card.pack(fill="x", pady=(0, 10))

        # Zeile 1: Duplikate-Steuerung mit abgerundeten Buttons & synchroner Farbwahl
        dup_row = tk.Frame(tb_card, bg="#ffffff")
        dup_row.pack(fill="x", pady=(0, 8))

        tk.Label(dup_row, text="👥 Duplikate:", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#0f172a").pack(side="left", padx=(0, 6))

        # Farbige Status-Badges "vorne" (abgerundet)
        self.badge_skip = ModernRoundedButton(
            dup_row,
            text="0 Überspringen",
            command=lambda: self.set_all_duplicates("Überspringen"),
            bg="#ea580c",
            fg="#ffffff",
            hover_bg="#c2410c",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=4
        )
        self.badge_skip.pack(side="left", padx=(0, 4))

        self.badge_copy = ModernRoundedButton(
            dup_row,
            text="0 Mitkopieren",
            command=lambda: self.set_all_duplicates("Mitkopieren"),
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=4
        )
        self.badge_copy.pack(side="left", padx=(0, 4))

        self.badge_del = ModernRoundedButton(
            dup_row,
            text="0 Gelöscht",
            command=self.delete_all_duplicates,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=4
        )
        self.badge_del.pack(side="left", padx=(0, 10))

        # Vertikaler Trenner zwischen Statusanzeige und Buttons
        tk.Frame(dup_row, bg="#e2e8f0", width=1, height=22).pack(side="left", padx=(0, 10))

        # Duplikat-Buttons mit synchroner Farbumschaltung (Rechts wie Links)
        self.btn_dup_skip = ModernRoundedButton(
            dup_row,
            text="⛔ Alle überspringen",
            command=lambda: self.set_all_duplicates("Überspringen"),
            bg="#ea580c",
            fg="#ffffff",
            hover_bg="#c2410c",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=5
        )
        self.btn_dup_skip.pack(side="left", padx=(0, 4))

        self.btn_dup_copy = ModernRoundedButton(
            dup_row,
            text="📋 Alle mitkopieren",
            command=lambda: self.set_all_duplicates("Mitkopieren"),
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=12,
            pady=5
        )
        self.btn_dup_copy.pack(side="left", padx=(0, 4))

        # Button fuer Mehrfach-Loeschung
        self.btn_del_selected = ModernRoundedButton(
            dup_row,
            text="🗑 Ausgewählte löschen",
            command=self.delete_selected_files,
            bg="#ffffff",
            fg="#dc2626",
            hover_bg="#fee2e2",
            border_color="#fca5a5",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=5
        )
        self.btn_del_selected.pack(side="left", padx=(0, 4))

        self.btn_dup_del = ModernRoundedButton(
            dup_row,
            text="🗑 Alle löschen",
            command=self.delete_all_duplicates,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=5
        )
        self.btn_dup_del.pack(side="left", padx=(0, 8))

        # Zusatz-Aktionen: Vergleichen & In Standard-App oeffnen
        ModernRoundedButton(
            dup_row,
            text="🔍 Vergleichen...",
            command=self.compare_selected_duplicate,
            bg="#ffffff",
            fg="#4f46e5",
            hover_bg="#ede9fe",
            border_color="#c7d2fe",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=5
        ).pack(side="left", padx=(0, 4))

        ModernRoundedButton(
            dup_row,
            text="📄 In Standard-App öffnen",
            command=self.open_selected_file,
            bg="#ffffff",
            fg="#0284c7",
            hover_bg="#e0f2fe",
            border_color="#bae6fd",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=5
        ).pack(side="left", padx=(0, 4))

        self.btn_toggle_preview = ModernRoundedButton(
            dup_row,
            text="👁 Vorschau",
            command=lambda: self.toggle_preview_pane(),
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#eff6ff",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=5
        )
        self.btn_toggle_preview.pack(side="left")

        # Deutliche Trennlinie zwischen Duplikate- und Filter-Bereich
        tk.Frame(tb_card, bg="#cbd5e1", height=1).pack(fill="x", pady=(2, 7))

        # Zeile 2: Live-Suchfeld, Schnellfilter-Chips & nachtraegliche Datei-/Datums-/Groessenfilter (ohne neuen Scan)
        filter_row = tk.Frame(tb_card, bg="#ffffff")
        filter_row.pack(fill="x", pady=(0, 6))

        tk.Label(filter_row, text="🔎 Filter:", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#0f172a").pack(side="left", padx=(0, 6))

        self.box_step2_search = create_filter_box(
            filter_row,
            textvariable=self.step2_search_var,
            width=155,
            height=27,
            placeholder="Live suchen…",
            on_commit=self.apply_step2_filters,
        )
        self.box_step2_search.pack(side="left", padx=(0, 5))
        self.ent_step2_search = self.box_step2_search.entry
        self.ent_step2_search.bind("<Escape>", lambda _e: (self.clear_step2_filters(), "break")[1])
        attach_tooltip(self.box_step2_search, "Ergebnisse sofort durchsuchen (Strg+F)")

        self._step2_chip_defs = [
            ("all", "Alle"),
            ("dups", "Duplikate"),
            ("inbox", "Inbox"),
            ("cloud", "in Cloud"),
            ("deleted", "Gelöscht"),
        ]
        self.step2_chip_buttons = {}
        for chip_key, chip_lbl in self._step2_chip_defs:
            is_def = (chip_key == "all")
            c_btn = ModernRoundedButton(
                filter_row,
                text=f"{chip_lbl} (0)",
                command=lambda ck=chip_key: self.set_step2_filter_chip(ck),
                bg="#2563eb" if is_def else "#ffffff",
                fg="#ffffff" if is_def else "#1e293b",
                hover_bg="#1d4ed8" if is_def else "#f1f5f9",
                border_color=None if is_def else "#94a3b8",
                font=("Segoe UI", 9, "bold"),
                r=8,
                padx=8,
                pady=4,
            )
            c_btn.pack(side="left", padx=(0, 3))
            self.step2_chip_buttons[chip_key] = c_btn

        self.btn_step2_filter_reset = ModernRoundedButton(
            filter_row,
            text="✕ Zurücksetzen",
            command=self.clear_step2_filters,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=7,
            pady=4,
        )
        self.btn_step2_filter_reset.pack(side="left", padx=(2, 6))
        attach_tooltip(self.btn_step2_filter_reset, "Suchfeld und alle Schritt-2-Filter zurücksetzen")

        # Trenner zu den nachtraeglichen Datei-, Datums- & Groessenfiltern (kompakt auf derselben Linie)
        tk.Frame(filter_row, bg="#e2e8f0", width=1, height=22).pack(side="left", padx=(0, 6))

        self._step2_file_filter_specs = [
            ("all", "Alle", "all_files"),
            ("images", "Bilder", "images_only"),
            ("text_docs", "Texte & Docs", "doc_only"),
            ("office", "Office", "office_doc"),
            ("media", "Videos & Audio", "media_video"),
            ("installers", "Programme", "installer_app"),
        ]
        self.step2_file_filter_buttons = {}
        for f_mode, f_lbl, f_icon in self._step2_file_filter_specs:
            is_def = (f_mode == "all")
            f_btn = ModernRoundedButton(
                filter_row,
                text=f_lbl,
                command=lambda m=f_mode: self.set_step2_file_filter_mode(m),
                bg="#2563eb" if is_def else "#ffffff",
                fg="#ffffff" if is_def else "#1e293b",
                hover_bg="#1d4ed8" if is_def else "#f1f5f9",
                border_color=None if is_def else "#94a3b8",
                font=("Segoe UI", 9, "bold"),
                r=8,
                padx=6,
                pady=4,
                icon_kind=f_icon,
            )
            f_btn.pack(side="left", padx=(0, 3))
            self.step2_file_filter_buttons[f_mode] = f_btn

        tk.Frame(filter_row, bg="#e2e8f0", width=1, height=22).pack(side="left", padx=(0, 6))

        self.btn_step2_date_preset = ModernRoundedButton(
            filter_row,
            text="Zeitraum ▾",
            command=self.open_step2_date_preset_menu,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=7,
            pady=4,
        )
        self.btn_step2_date_preset.pack(side="left", padx=(0, 4))

        self.box_step2_date_from = create_filter_box(
            filter_row,
            textvariable=self.step2_date_from_var,
            width=114,
            height=27,
            placeholder="TT.MM.JJJJ",
            prefix="Von",
            on_calendar=lambda w: self.open_step2_date_picker(w, self.step2_date_from_var),
            on_commit=self.apply_step2_filters,
        )
        self.box_step2_date_from.pack(side="left", padx=(0, 3))
        self.ent_step2_date_from = self.box_step2_date_from.entry
        if self.box_step2_date_from.calendar_button is not None:
            attach_tooltip(self.box_step2_date_from.calendar_button, "Datumsauswahl anzeigen")

        self.box_step2_date_to = create_filter_box(
            filter_row,
            textvariable=self.step2_date_to_var,
            width=114,
            height=27,
            placeholder="TT.MM.JJJJ",
            prefix="Bis",
            on_calendar=lambda w: self.open_step2_date_picker(w, self.step2_date_to_var),
            on_commit=self.apply_step2_filters,
        )
        self.box_step2_date_to.pack(side="left", padx=(0, 6))
        self.ent_step2_date_to = self.box_step2_date_to.entry
        if self.box_step2_date_to.calendar_button is not None:
            attach_tooltip(self.box_step2_date_to.calendar_button, "Datumsauswahl anzeigen")

        tk.Frame(filter_row, bg="#e2e8f0", width=1, height=22).pack(side="left", padx=(0, 6))

        self.box_step2_min_size = create_filter_box(
            filter_row,
            textvariable=self.step2_min_size_var,
            width=76,
            height=27,
            placeholder="500 KB",
            prefix="Ab",
            on_commit=self.apply_step2_filters,
        )
        self.box_step2_min_size.pack(side="left", padx=(0, 3))
        self.ent_step2_min_size = self.box_step2_min_size.entry

        self.btn_step2_size_preset = ModernRoundedButton(
            filter_row,
            text="Größe ▾",
            command=self.open_step2_size_preset_menu,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=7,
            pady=4,
        )
        self.btn_step2_size_preset.pack(side="left", padx=(0, 0))

        # Deutliche Trennlinie zwischen Filter- und Aktions-Bereich
        sep_tb = tk.Frame(tb_card, bg="#cbd5e1", height=1)
        sep_tb.pack(fill="x", pady=(1, 7))

        # Zeile 3: Spalten-Reset & Toolbar & deutliche Tastatur-Kürzel
        col_row = tk.Frame(tb_card, bg="#ffffff")
        col_row.pack(fill="x", pady=(2, 0))

        # 1. Zielordner Gruppe
        self.btn_assign_target = ModernRoundedButton(
            col_row,
            "📂 Zielordner ändern ▾",
            command=self.open_assign_target_menu,
            bg="#ffffff",
            fg="#2563eb",
            hover_bg="#eff6ff",
            border_color="#93c5fd",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=5,
        )
        self.btn_assign_target.pack(side="left", padx=(0, 4))
        attach_tooltip(self.btn_assign_target, "Zielordner ändern: Menü mit Schnellauswahl der letzten 5 Ordner oder Dialog öffnen")

        self.btn_quick_last_target = ModernRoundedButton(
            col_row,
            "⚡ Zuletzt: —",
            command=self.quick_apply_last_target,
            bg="#eff6ff",
            fg="#1d4ed8",
            hover_bg="#dbeafe",
            border_color="#93c5fd",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=5,
        )
        self.btn_quick_last_target.pack(side="left", padx=(0, 6))
        attach_tooltip(self.btn_quick_last_target, "1-Klick: Letzten Zielordner sofort für alle Einträge übernehmen")

        # Trenner
        tk.Frame(col_row, bg="#e2e8f0", width=1, height=22).pack(side="left", padx=(0, 6))

        # 2. Dateinamen & F2 Gruppe
        self.btn_shorten_names = ModernRoundedButton(
            col_row,
            "✂️ Namen kürzen ▾",
            command=self.open_shorten_names_menu,
            bg="#ffffff",
            fg="#6d28d9",
            hover_bg="#f5f3ff",
            border_color="#c4b5fd",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=5,
        )
        self.btn_shorten_names.pack(side="left", padx=(0, 4))
        attach_tooltip(self.btn_shorten_names, "Dateinamen einkürzen: Untertitel nach '-' entfernen, max. Länge begrenzen oder freier Dialog")

        self.btn_rename_single = ModernRoundedButton(
            col_row,
            "✏️ F2: Name bearbeiten",
            command=self.start_rename_selected_file,
            bg="#ffffff",
            fg="#0f766e",
            hover_bg="#f0fdfa",
            border_color="#99f6e4",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=5,
        )
        self.btn_rename_single.pack(side="left", padx=(0, 6))
        attach_tooltip(self.btn_rename_single, "Taste F2 drücken oder hier klicken: Ausgewählte Datei direkt umbenennen")

        # Trenner
        tk.Frame(col_row, bg="#e2e8f0", width=1, height=22).pack(side="left", padx=(0, 6))

        # 3. Regeln & Duplikate & Spalten Gruppe
        self.btn_dup_strategy = ModernRoundedButton(
            col_row,
            "🎯 Original-Wahl ▾",
            command=self.open_dup_strategy_menu,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=10,
            pady=5,
        )
        self.btn_dup_strategy.pack(side="left", padx=(0, 4))
        attach_tooltip(self.btn_dup_strategy, "Strategie wählen, welche inhaltsgleiche Datei als Original behalten wird")

        self.btn_manage_rules = ModernRoundedButton(
            col_row,
            "⚙ Regeln",
            command=self.open_custom_rules_manager,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=9,
            pady=5,
        )
        self.btn_manage_rules.pack(side="left", padx=(0, 4))
        attach_tooltip(self.btn_manage_rules, "Gespeicherte Stichwort-Regeln anzeigen oder löschen")

        ModernRoundedButton(
            col_row,
            "↺ Spalten",
            command=self.reset_column_order,
            bg="#ffffff",
            fg="#1e293b",
            hover_bg="#f1f5f9",
            border_color="#94a3b8",
            font=("Segoe UI", 9, "bold"),
            r=8,
            padx=8,
            pady=5,
        ).pack(side="left", padx=(0, 6))

        # 4. Rechts: Dezente, flache Tastenkürzel-Legende (klar als reine Anzeige von klickbaren Buttons unterscheidbar)
        sc_bar = tk.Frame(col_row, bg="#ffffff")
        sc_bar.pack(side="right")

        tk.Label(sc_bar, text="Tasten:", font=("Segoe UI", 8), bg="#ffffff", fg="#94a3b8", padx=4).pack(side="left")

        for k_idx, (k_key, k_desc) in enumerate((("Strg+F", "Suche"), ("Leertaste", "Vorschau"), ("Entf", "Löschen"))):
            if k_idx > 0:
                tk.Label(sc_bar, text="·", font=("Segoe UI", 8), bg="#ffffff", fg="#cbd5e1", padx=4).pack(side="left")
            tk.Label(
                sc_bar, text=f" {k_key} ", font=("Segoe UI", 8, "bold"),
                bg="#f1f5f9", fg="#475569", padx=2, pady=1, bd=0,
            ).pack(side="left")
            tk.Label(
                sc_bar, text=k_desc, font=("Segoe UI", 8),
                bg="#ffffff", fg="#64748b", padx=3, pady=1,
            ).pack(side="left")

        # Hauptbereich Schritt 2: Tabelle links + beliebig aufziehbare eingebettete Fluent-Vorschau rechts
        self.step2_body = tk.PanedWindow(
            self.frame_step2,
            orient="horizontal",
            bg="#e2e8f0",
            sashwidth=6,
            sashpad=1,
            bd=0,
            relief="flat",
        )
        self.step2_body.pack(fill="both", expand=True)

        # Baum-Tabelle Container
        tree_frame = tk.Frame(self.step2_body, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1")
        self.step2_body.add(tree_frame, stretch="always", minsize=420)
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        self._tree_frame = tree_frame
        self._preview_pane_width = 380

        self.preview_pane = FilePreviewPane(
            self.step2_body,
            on_close=lambda: self.toggle_preview_pane(False),
            on_open_app=lambda it: self.open_file_directly(it["src_path"]),
            on_open_folder=lambda it: self.open_file_folder(it["src_path"]),
        )
        self.step2_body.bind("<ButtonRelease-1>", self._on_step2_sash_release)

        # selectmode="extended" fuer Mehrfachauswahl mit Strg / Umschalt
        self.tree = ttk.Treeview(tree_frame, columns=self.col_keys, show="headings", selectmode="extended")

        # Farb-Tags fuer Baumansicht: Maximaler, glasklarer Kontrast zwischen Original und Duplikat!
        self.tree.tag_configure("tag_orig", background="#dbeafe", foreground="#1e40af")       # Klares Eisblau fuer Original mit Zwilling
        self.tree.tag_configure("tag_dup", background="#ffedd5", foreground="#c2410c")        # Warmes Pfirsich/Koralle fuer Duplikat (Ueberspringen) - max. Kontrast zu Blau!
        self.tree.tag_configure("tag_dup_copy", background="#dcfce7", foreground="#15803d")   # Frisches Mintgruen fuer Duplikat (Mitkopieren)
        self.tree.tag_configure("tag_del", background="#fee2e2", foreground="#b91c1c")        # Softes Hellrot fuer Geloeschte
        self.tree.tag_configure("tag_cloud", background="#f0f9ff", foreground="#0369a1")      # Sanftes Cloud-Blau fuer OneDrive-Platzhalter
        self.tree.tag_configure("tag_normal", background="#ffffff", foreground="#0f172a")     # Neutrales Weiss fuer regulaere Dateien

        # Spalten konfigurieren: minwidth=35 und stretch=False ermoeglichen flexibles Engerschieben
        for c in self.col_keys:
            self.tree.heading(c, text=self.col_titles[c], command=lambda _c=c: self.sort_column(_c, False))
            anchor = "e" if c in ["size", "items_count"] else ("center" if c in ["ext", "date", "action", "dup_status"] else "w")
            self.tree.column(c, width=self.col_widths.get(c, 100), minwidth=35, stretch=False, anchor=anchor)

        # Standard-Displayreihenfolge
        self.tree["displaycolumns"] = tuple(self.col_keys)

        # Scrollbars (horizontale Scrollbar erscheint nur, falls Spalten breiter als die Ansicht sind)
        scroll_y = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.scroll_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=self._on_tree_xscroll)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")

        # Events fuer Spaltenkoepfe & Zeilen
        self.tree.bind("<ButtonPress-1>", self.on_tree_click_press)
        self.tree.bind("<B1-Motion>", self.on_tree_mouse_drag)
        self.tree.bind("<ButtonRelease-1>", self.on_tree_click_release)
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<Button-3>", self.show_context_menu)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_tree_selection_changed())
        self.tree.bind("<space>", self._on_tree_space_key)
        self.tree.bind("<F2>", lambda _e: (self.start_rename_selected_file(), "break")[1])
        self.bind("<F2>", lambda _e: (self.start_rename_selected_file(), "break")[1])
        self.tree.bind("<Control-z>", lambda _e: (self.reset_renamed_names(), "break")[1])
        self.tree.bind("<Control-Z>", lambda _e: (self.reset_renamed_names(), "break")[1])
        self.bind("<Control-z>", lambda _e: (self.reset_renamed_names(), "break")[1])
        self.bind("<Control-Z>", lambda _e: (self.reset_renamed_names(), "break")[1])
        self.tree.bind("<Delete>", lambda e: self.delete_selected_files())
        self.tree.bind("<BackSpace>", lambda e: self.delete_selected_files())
        self.tree.bind("<Return>", lambda e: self.open_selected_file())
        self.tree.bind("<Control-a>", self.select_all_rows)
        self.tree.bind("<Control-A>", self.select_all_rows)

        # Bottom Bar mit BACK-Knopf, mittigem Original-Vorschau-Reset und Ausfuehren-Knoepfen (alle exakt gleich hoch)
        nav_bar = tk.Frame(self.frame_step2, bg="#f8fafc")
        nav_bar.pack(fill="x", pady=(14, 0))
        nav_bar.columnconfigure(0, weight=1)
        nav_bar.columnconfigure(1, weight=0)
        nav_bar.columnconfigure(2, weight=1)

        nav_btn_h = tkfont.Font(font=("Segoe UI", 9, "bold")).metrics("linespace") * 2 + 5 * 2 + 4

        ModernRoundedButton(
            nav_bar,
            text="⬅ Zurück zur Ordnerauswahl",
            command=self.show_step1,
            bg="#eff6ff",
            fg="#1d4ed8",
            hover_bg="#dbeafe",
            border_color="#93c5fd",
            font=("Segoe UI", 9, "bold"),
            r=10,
            padx=16,
            pady=8,
            min_height=nav_btn_h,
        ).grid(row=0, column=0, sticky="w")

        self.btn_reset_original_preview = ModernRoundedButton(
            nav_bar,
            text="⬅ Zurück zur\nOriginal-Vorschau",
            command=self.reset_to_original_preview,
            bg="#eff6ff",
            fg="#1d4ed8",
            hover_bg="#dbeafe",
            border_color="#93c5fd",
            font=("Segoe UI", 9, "bold"),
            r=10,
            padx=22,
            pady=5,
            min_height=nav_btn_h,
        )
        self.btn_reset_original_preview.grid(row=0, column=1, padx=12)
        attach_tooltip(
            self.btn_reset_original_preview,
            "Alle geänderten Dateinamen, Zielordner und Filter sofort auf die Original-Vorschau zurücksetzen",
        )

        nav_right = tk.Frame(nav_bar, bg="#f8fafc")
        nav_right.grid(row=0, column=2, sticky="e")

        f_nav_sel = tkfont.Font(font=("Segoe UI", 9, "bold"))
        sel_all_min_w = max(
            f_nav_sel.measure("✓ Alle wählen"),
            f_nav_sel.measure("☐ Auswahl aufheben"),
        ) + 32

        self.btn_toggle_select_all = ModernRoundedButton(
            nav_right,
            text="✓ Alle wählen",
            command=self.toggle_select_all_rows,
            bg="#eff6ff",
            fg="#1d4ed8",
            hover_bg="#dbeafe",
            border_color="#93c5fd",
            font=("Segoe UI", 9, "bold"),
            r=10,
            padx=16,
            pady=8,
            min_width=sel_all_min_w,
            min_height=nav_btn_h,
        )
        self.btn_toggle_select_all.pack(side="left", padx=(0, 10))
        attach_tooltip(
            self.btn_toggle_select_all,
            "Alle sichtbaren Zeilen markieren oder Auswahl wieder aufheben (Strg+A)",
        )

        f_nav_exec = tkfont.Font(font=("Segoe UI", 10, "bold"))
        exec_min_w = f_nav_exec.measure("Auf alle 999 anwenden ➔") + 44

        self.btn_execute = ModernRoundedButton(
            nav_right,
            text="Keine Auswahl",
            command=self.execute_selected_or_filtered_sort,
            bg="#e2e8f0",
            fg="#64748b",
            hover_bg="#e2e8f0",
            border_color="#cbd5e1",
            font=("Segoe UI", 10, "bold"),
            r=10,
            padx=22,
            pady=10,
            min_width=exec_min_w,
            min_height=nav_btn_h,
        )
        self.btn_execute.pack(side="left")
        attach_tooltip(
            self.btn_execute,
            "Sortierung auf die markierten Einträge anwenden (zuerst Zeilen markieren oder „✓ Alle wählen“ klicken)",
        )

    # -------------------------------------------------------------
    # SPALTEN-STEUERUNG & DRAG-AND-DROP
    # -------------------------------------------------------------
    def _on_tree_xscroll(self, lo, hi):
        """Zeigt die horizontale Scrollbar nur dann an, wenn Spalten ueber die Breite hinausgehen."""
        self.scroll_x.set(lo, hi)
        try:
            if float(lo) <= 0.001 and float(hi) >= 0.999:
                if self.scroll_x.winfo_manager() == "grid":
                    self.scroll_x.grid_remove()
            else:
                if self.scroll_x.winfo_manager() != "grid":
                    self.scroll_x.grid(row=1, column=0, sticky="ew")
        except Exception:
            pass

    def get_current_display_columns(self):
        raw = self.tree["displaycolumns"]
        if raw == "#all" or raw == ("#all",) or not raw:
            return list(self.col_keys)
        return list(raw)

    def on_tree_click_press(self, event):
        region = self.tree.identify_region(event.x, event.y)
        self._resizing_separator = (region == "separator")
        if region == "heading":
            col_str = self.tree.identify_column(event.x)
            if col_str:
                idx = int(col_str.replace("#", "")) - 1
                disp = self.get_current_display_columns()
                if 0 <= idx < len(disp):
                    self.drag_start_x = event.x
                    self.drag_start_col_idx = idx
                    self.selected_col_key = disp[idx]
        else:
            self.drag_start_col_idx = None

    def on_tree_mouse_drag(self, event):
        if self.drag_start_col_idx is not None and abs(event.x - self.drag_start_x) > 15:
            self.tree.config(cursor="exchange")

    def on_tree_click_release(self, event):
        self.tree.config(cursor="")
        if self._resizing_separator:
            self._resizing_separator = False
            self._custom_col_widths = True
            for c in self.col_keys:
                try:
                    self.col_widths[c] = int(self.tree.column(c, "width"))
                except Exception:
                    pass
            self._save_app_state()
            self.drag_start_col_idx = None
            return

        if self.drag_start_col_idx is None:
            return

        region = self.tree.identify_region(event.x, event.y)
        col_str = self.tree.identify_column(event.x)
        if col_str and region in ("heading", "separator") and abs(event.x - self.drag_start_x) > 25:
            target_idx = int(col_str.replace("#", "")) - 1
            disp = self.get_current_display_columns()
            if 0 <= target_idx < len(disp) and target_idx != self.drag_start_col_idx:
                col_to_move = disp.pop(self.drag_start_col_idx)
                disp.insert(target_idx, col_to_move)
                self.tree["displaycolumns"] = tuple(disp)
                self.selected_col_key = col_to_move
                self._save_app_state()

        self.drag_start_col_idx = None

    def _close_active_popup_menu(self):
        if getattr(self, "_active_popup_menu", None) is not None:
            try:
                self._active_popup_menu.close()
            except Exception:
                pass
            self._active_popup_menu = None

    def _open_fluent_popup(self, items: list[dict], x: int, y: int):
        self._close_active_popup_menu()
        self._active_popup_menu = FluentPopupMenu(self, items, x, y)
        return self._active_popup_menu

    def _on_tree_space_key(self, _event=None):
        sel = self.tree.selection()
        if sel:
            self.toggle_preview_pane(True if not self.preview_visible else False, row_id=sel[0])
            return "break"
        return None

    def _on_tree_selection_changed(self):
        self._update_preview_pane_selection()
        self._update_execute_scope_button()

    def _update_preview_pane_selection(self):
        if not self.preview_visible:
            return
        sel = self.tree.selection()
        if not sel:
            self.preview_pane.show_empty()
            return
        item = self.preview_items.get(sel[0])
        self.preview_pane.show_item(item)

    def _on_step2_sash_release(self, _event=None):
        if not self.preview_visible:
            return
        try:
            w = int(self.preview_pane.winfo_width())
            if 280 <= w <= 1100:
                self._preview_pane_width = w
                if not getattr(self, "_restoring_state", False):
                    self._save_app_state()
        except Exception:
            pass

    def toggle_preview_pane(self, show: bool | None = None, row_id: str | None = None):
        target_state = (not self.preview_visible) if show is None else bool(show)
        self.preview_visible = target_state

        if row_id and not self.tree.selection():
            self.tree.selection_set(row_id)

        pane_ids = set(str(p) for p in self.step2_body.panes())
        pane_id = str(self.preview_pane)

        if target_state:
            desired_w = max(280, min(900, int(getattr(self, "_preview_pane_width", 380) or 380)))
            if pane_id not in pane_ids:
                self.step2_body.add(self.preview_pane, stretch="never", minsize=280, width=desired_w)
            try:
                self.update_idletasks()
                total_w = self.step2_body.winfo_width()
                if total_w > desired_w + 420:
                    self.step2_body.sash_place(0, total_w - desired_w, 0)
            except Exception:
                pass
            if hasattr(self, "btn_toggle_preview"):
                self.btn_toggle_preview.update_style(bg="#2563eb", fg="#ffffff", hover_bg="#1d4ed8", border_color="#2563eb")
            if row_id:
                self.preview_pane.show_item(self.preview_items.get(row_id))
            else:
                self._on_tree_selection_changed()
        else:
            if pane_id in pane_ids:
                try:
                    w = int(self.preview_pane.winfo_width())
                    if 280 <= w <= 1100:
                        self._preview_pane_width = w
                except Exception:
                    pass
                self.step2_body.forget(self.preview_pane)
            if hasattr(self, "btn_toggle_preview"):
                self.btn_toggle_preview.update_style(bg="#ffffff", fg="#1e293b", hover_bg="#eff6ff", border_color="#94a3b8")

        try:
            self.update_idletasks()
            if not self._custom_col_widths:
                self.auto_fit_columns()
        except Exception:
            pass
        if not getattr(self, "_restoring_state", False):
            self._save_app_state()

    def show_context_menu(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region == "heading":
            col_str = self.tree.identify_column(event.x)
            if col_str:
                idx = int(col_str.replace("#", "")) - 1
                disp = self.get_current_display_columns()
                if 0 <= idx < len(disp):
                    self.selected_col_key = disp[idx]
            col_items = [
                {"icon": "arrow_left", "label": "Spalte nach links verschieben", "command": self.move_selected_col_left},
                {"icon": "arrow_right", "label": "Spalte nach rechts verschieben", "command": self.move_selected_col_right},
                {"icon": "arrow_first", "label": "An den Anfang verschieben", "command": self.move_selected_col_first},
                {"icon": "arrow_last", "label": "An das Ende verschieben", "command": self.move_selected_col_last},
                {"kind": "sep"},
                {"icon": "refresh", "label": "Spalten zurücksetzen & einpassen", "command": self.reset_column_order},
            ]
            self._open_fluent_popup(col_items, event.x_root, event.y_root)
        elif region in ["cell", "tree"]:
            row_id = self.tree.identify_row(event.y)
            if not row_id:
                return

            current_selection = list(self.tree.selection())
            if row_id not in current_selection:
                self.tree.selection_set(row_id)
                current_selection = [row_id]

            count = len(current_selection)
            menu_items = []

            if count > 1:
                menu_items.append({
                    "icon": "play",
                    "label": f"Nur diese {count} markierten jetzt ausführen",
                    "command": self.execute_selected_or_filtered_sort,
                })
                menu_items.append({
                    "icon": "folder",
                    "label": f"Zielordner ändern für {count} Einträge…",
                    "command": self.assign_target_folder_selected,
                })
                menu_items.append({
                    "icon": "scissors",
                    "label": f"Dateinamen einkürzen ({count} Dateien)…",
                    "command": self.open_custom_shorten_dialog,
                })
                recent = getattr(self, "recent_target_folders", [])
                if recent:
                    r0 = recent[0]
                    r0_lbl = r0.get("label") or os.path.basename(r0["path"].rstrip("\\/")) or r0["path"]
                    r0_short = r0_lbl if len(r0_lbl) <= 20 else r0_lbl[:18] + "…"
                    menu_items.append({
                        "icon": "zap",
                        "label": f"Ziel: Zuletzt ({r0_short})",
                        "command": lambda sp=r0["path"], sl=r0_lbl: self.quick_assign_target_to_preview(sp, sl, apply_to_all=False),
                    })
                menu_items.append({"kind": "sep"})
                menu_items.append({
                    "icon": "trash",
                    "label": f"{count} ausgewählte Dateien in Papierkorb löschen",
                    "shortcut": "Entf",
                    "command": self.delete_selected_files,
                    "danger": True,
                })
                menu_items.append({"kind": "sep"})
                menu_items.append({
                    "icon": "copy",
                    "label": "Ausgewählte auf „Mitkopieren“ setzen",
                    "command": lambda: self.set_selected_duplicates_action("Mitkopieren"),
                })
                menu_items.append({
                    "icon": "skip",
                    "label": "Ausgewählte auf „Überspringen“ setzen",
                    "command": lambda: self.set_selected_duplicates_action("Überspringen"),
                })
                menu_items.append({"kind": "sep"})
                menu_items.append({
                    "icon": "doc",
                    "label": f"In Standard-App öffnen ({count} Dateien)",
                    "command": self.open_selected_file,
                })
            else:
                item = self.preview_items.get(row_id)
                if not item:
                    return

                menu_items.append({
                    "icon": "preview",
                    "label": "Dateivorschau hier anzeigen",
                    "shortcut": "Leertaste",
                    "command": lambda r=row_id: self.toggle_preview_pane(True, row_id=r),
                })
                menu_items.append({
                    "icon": "folder",
                    "label": "Zielordner ändern / Regel merken…",
                    "command": lambda r=row_id: self.assign_target_folder_selected([r]),
                })
                menu_items.append({
                    "icon": "rename",
                    "label": "Dateiname bearbeiten…",
                    "shortcut": "F2",
                    "command": lambda r=row_id: self.start_rename_selected_file(r),
                })
                recent = getattr(self, "recent_target_folders", [])
                if recent:
                    r0 = recent[0]
                    r0_lbl = r0.get("label") or os.path.basename(r0["path"].rstrip("\\/")) or r0["path"]
                    r0_short = r0_lbl if len(r0_lbl) <= 20 else r0_lbl[:18] + "…"
                    menu_items.append({
                        "icon": "zap",
                        "label": f"Ziel: Zuletzt ({r0_short})",
                        "command": lambda sp=r0["path"], sl=r0_lbl, r=row_id: self.quick_assign_target_to_preview(sp, sl, apply_to_all=False),
                    })
                menu_items.append({
                    "icon": "play",
                    "label": "Nur diese Datei jetzt ausführen",
                    "command": lambda r=row_id: self.execute_sort(target_row_ids=[r], scope_label="aus Markierung"),
                })
                if item.get("is_dir"):
                    menu_items.append({"icon": "folder", "label": "Ordner im Explorer öffnen", "command": lambda p=item["src_path"]: self.open_file_directly(p)})
                    menu_items.append({"icon": "explorer", "label": "Im Überordner zeigen", "command": lambda p=item["src_path"]: self.open_file_folder(p)})
                else:
                    menu_items.append({"icon": "doc", "label": "In Standard-App öffnen", "command": lambda p=item["src_path"]: self.open_file_directly(p)})
                    menu_items.append({"icon": "explorer", "label": "Im Windows-Explorer zeigen", "command": lambda p=item["src_path"]: self.open_file_folder(p)})
                menu_items.append({"kind": "sep"})

                if item.get("is_duplicate"):
                    menu_items.append({"icon": "copy", "label": "Auf „Mitkopieren“ setzen", "command": lambda r=row_id: self.set_duplicate_action(r, "Mitkopieren")})
                    menu_items.append({"icon": "skip", "label": "Auf „Überspringen“ setzen", "command": lambda r=row_id: self.set_duplicate_action(r, "Überspringen")})
                    menu_items.append({"icon": "swap", "label": "Zum Original machen (Rollen tauschen)", "command": lambda r=row_id: self.swap_roles_for_item(r)})
                    menu_items.append({"icon": "compare", "label": "Mit Original im Detail vergleichen…", "command": lambda r=row_id: self.open_compare_dialog(r)})
                    menu_items.append({"kind": "sep"})
                    menu_items.append({"icon": "trash", "label": "In Windows-Papierkorb löschen", "shortcut": "Entf", "command": self.delete_selected_files, "danger": True})
                else:
                    if item.get("action") == "Überspringen":
                        base_act = "Kopieren" if self.action_mode.get() == "copy" else "Verschieben"
                        menu_items.append({"icon": "play", "label": f"Auf „{base_act}“ setzen", "command": lambda r=row_id, a=base_act: self.set_item_action(r, a)})
                    else:
                        menu_items.append({"icon": "skip", "label": "Auf „Überspringen“ setzen", "command": lambda r=row_id: self.set_item_action(r, "Überspringen")})
                    if item.get("has_duplicates"):
                        menu_items.append({"icon": "compare", "label": "Mit Duplikat im Detail vergleichen…", "command": lambda r=row_id: self.open_compare_dialog(r)})
                    menu_items.append({"kind": "sep"})
                    menu_items.append({"icon": "trash", "label": "In Windows-Papierkorb löschen", "shortcut": "Entf", "command": self.delete_selected_files, "danger": True})

            self._open_fluent_popup(menu_items, event.x_root, event.y_root)

    def on_tree_double_click(self, event):
        """Doppelklick: Oeffnet die ausgewaehlte Datei direkt in der jeweiligen Standard-App."""
        region = self.tree.identify_region(event.x, event.y)
        if region in ["cell", "tree"]:
            row_id = self.tree.identify_row(event.y)
            if row_id:
                item = self.preview_items.get(row_id)
                if item and item.get("src_path"):
                    self.open_file_directly(item["src_path"])

    def move_selected_col_left(self):
        disp = self.get_current_display_columns()
        try:
            idx = disp.index(self.selected_col_key)
        except ValueError:
            idx = 0
        if idx > 0:
            disp[idx - 1], disp[idx] = disp[idx], disp[idx - 1]
            self.tree["displaycolumns"] = tuple(disp)
            self._save_app_state()

    def move_selected_col_right(self):
        disp = self.get_current_display_columns()
        try:
            idx = disp.index(self.selected_col_key)
        except ValueError:
            idx = 0
        if idx < len(disp) - 1:
            disp[idx], disp[idx + 1] = disp[idx + 1], disp[idx]
            self.tree["displaycolumns"] = tuple(disp)
            self._save_app_state()

    def move_selected_col_first(self):
        disp = self.get_current_display_columns()
        try:
            idx = disp.index(self.selected_col_key)
        except ValueError:
            idx = 0
        if idx > 0:
            item = disp.pop(idx)
            disp.insert(0, item)
            self.tree["displaycolumns"] = tuple(disp)
            self._save_app_state()

    def move_selected_col_last(self):
        disp = self.get_current_display_columns()
        try:
            idx = disp.index(self.selected_col_key)
        except ValueError:
            idx = 0
        if idx < len(disp) - 1:
            item = disp.pop(idx)
            disp.append(item)
            self.tree["displaycolumns"] = tuple(disp)
            self._save_app_state()

    def reset_column_order(self):
        self._custom_col_widths = False
        self.tree["displaycolumns"] = tuple(self.col_keys)
        self.selected_col_key = "file"
        self.auto_fit_columns()
        self._save_app_state()

    def select_all_rows(self, event=None):
        self.tree.selection_set(self.tree.get_children(""))
        self._update_execute_scope_button()
        return "break"

    def toggle_select_all_rows(self):
        """Schaltet zwischen 'Alle sichtbaren Zeilen markieren' und 'Auswahl aufheben' um."""
        vis = list(self.tree.get_children(""))
        if not vis:
            return
        sel = set(self.tree.selection())
        if all(rid in sel for rid in vis):
            self.tree.selection_remove(tuple(sel))
        else:
            self.tree.selection_set(vis)
        self._update_execute_scope_button()
        if hasattr(self, "_update_preview_pane_selection"):
            self._update_preview_pane_selection()

    def sort_column(self, col, reverse):
        """Sortiert Vorschau-Zeilen nach der Spalte; Original und seine Duplikate bleiben (ausser bei Status-Sortierung) als Block zusammen."""
        def get_sort_key(val):
            return str(val).lstrip("⛔📋🗑📄📁☁️ ").strip().lower()

        def row_sort_value(rid):
            if col == "size":
                sz = self.preview_items.get(rid, {}).get("size_bytes", -1)
                return (sz < 0, sz)
            if col == "items_count":
                cnt = self.preview_items.get(rid, {}).get("inner_count", 0)
                return (cnt <= 0, cnt)
            return get_sort_key(self.tree.set(rid, col))

        row_ids = list(self._preview_row_order) if getattr(self, "_preview_row_order", None) else list(self.tree.get_children(''))

        if col == "dup_status":
            items = [(row_sort_value(k), k) for k in row_ids]
            try:
                items.sort(key=lambda t: t[0], reverse=reverse)
            except Exception:
                items.sort(reverse=reverse)
            sorted_rids = [k for _, k in items]
        else:
            row_set = set(row_ids)
            orig_obj_to_rid = {}
            for k in row_ids:
                it = self.preview_items.get(k)
                if it and not it.get("is_duplicate"):
                    orig_obj_to_rid[id(it)] = k

            anchor_map = {}
            anchors = []
            for k in row_ids:
                it = self.preview_items.get(k) or {}
                orig_obj = it.get("original_item")
                anchor = orig_obj_to_rid.get(id(orig_obj)) if (it.get("is_duplicate") and orig_obj is not None) else None
                if not anchor or anchor not in row_set:
                    anchor = k
                anchor_map[k] = anchor
                if anchor == k:
                    anchors.append(k)

            # Eventuelle verwaiste Duplikate ohne sichtbares Original ebenfalls als eigene Anker aufnehmen
            seen_anchors = set(anchors)
            for k in row_ids:
                anc = anchor_map[k]
                if anc not in seen_anchors:
                    anchors.append(anc)
                    seen_anchors.add(anc)

            try:
                anchors.sort(key=row_sort_value, reverse=reverse)
            except Exception:
                pass
            group_rank = {anc: idx for idx, anc in enumerate(anchors)}

            def grouped_key(rid):
                it = self.preview_items.get(rid) or {}
                anc = anchor_map[rid]
                is_dup = 1 if (it.get("is_duplicate") and anc != rid) else 0
                return (group_rank.get(anc, len(anchors)), is_dup, row_sort_value(rid))

            try:
                sorted_rids = sorted(row_ids, key=grouped_key)
            except Exception:
                sorted_rids = list(row_ids)

        if getattr(self, "_preview_row_order", None):
            self._preview_row_order = sorted_rids

        visible_set = set(self.tree.get_children(''))
        vis_idx = 0
        for k in sorted_rids:
            if k in visible_set:
                self.tree.move(k, '', vis_idx)
                vis_idx += 1

        # Pfeil-Indikator setzen und Toggle vorbereiten
        for c in self.col_keys:
            base_text = self.col_titles.get(c, c)
            if c == col:
                arrow = " ▼" if reverse else " ▲"
                self.tree.heading(c, text=base_text + arrow, command=lambda _c=c: self.sort_column(_c, not reverse))
            else:
                self.tree.heading(c, text=base_text, command=lambda _c=c: self.sort_column(_c, False))

    def _focus_step2_search(self, _event=None):
        """Fokussiert in Schritt 2 das Live-Suchfeld (Strg+F)."""
        if self.frame_step2.winfo_manager() and hasattr(self, "ent_step2_search"):
            self.ent_step2_search.focus_set()
            self.ent_step2_search.select_range(0, "end")
            return "break"
        return None

    def _schedule_step2_filter(self):
        if getattr(self, "_suppress_step2_filter_trace", False):
            return
        if self._step2_filter_after_id is not None:
            try:
                self.after_cancel(self._step2_filter_after_id)
            except Exception:
                pass
            self._step2_filter_after_id = None
        if self.preview_items:
            self._step2_filter_after_id = self.after(40, self.apply_step2_filters)

    def _on_step2_detail_var_change(self):
        if getattr(self, "_suppress_step2_filter_trace", False):
            return
        self.update_step2_detail_filter_ui()
        self._schedule_step2_filter()

    def set_step2_filter_chip(self, chip_key: str):
        self.step2_filter_chip = chip_key if chip_key in {"all", "dups", "inbox", "cloud", "deleted"} else "all"
        self.apply_step2_filters()

    def set_step2_file_filter_mode(self, mode: str):
        if not hasattr(self, "step2_active_types"):
            self.step2_active_types = set()
        if mode == "all":
            self.step2_active_types.clear()
            self.step2_file_filter_mode = "all"
        else:
            if mode in self.step2_active_types:
                self.step2_active_types.remove(mode)
            else:
                self.step2_active_types.add(mode)
            if not self.step2_active_types:
                self.step2_file_filter_mode = "all"
            else:
                self.step2_file_filter_mode = "custom"
        self.update_step2_detail_filter_ui()
        self.apply_step2_filters()

    def toggle_step2_onedrive_filter(self):
        self.step2_only_onedrive = not bool(getattr(self, "step2_only_onedrive", False))
        self.update_step2_detail_filter_ui()
        self.apply_step2_filters()

    def set_step2_date_preset(self, preset_key: str):
        if preset_key == "clear":
            self._suppress_step2_filter_trace = True
            try:
                self.step2_date_from_var.set("")
                self.step2_date_to_var.set("")
            finally:
                self._suppress_step2_filter_trace = False
            self.update_step2_detail_filter_ui()
            self.apply_step2_filters()
            return
        d_start, d_end = self._compute_date_preset_range(preset_key)
        if d_start is None or d_end is None:
            return
        s_from = d_start.strftime("%d.%m.%Y")
        s_to = d_end.strftime("%d.%m.%Y")
        self._suppress_step2_filter_trace = True
        try:
            if self.step2_date_from_var.get().strip() == s_from and self.step2_date_to_var.get().strip() == s_to:
                self.step2_date_from_var.set("")
                self.step2_date_to_var.set("")
            else:
                self.step2_date_from_var.set(s_from)
                self.step2_date_to_var.set(s_to)
        finally:
            self._suppress_step2_filter_trace = False
        self.update_step2_detail_filter_ui()
        self.apply_step2_filters()

    def open_step2_date_preset_menu(self):
        """Öffnet ein helles Fluent-Menü zur Schnellauswahl des Zeitraums in Schritt 2."""
        preset_specs = [
            ("today", "Heute"),
            ("week", "Diese Woche"),
            ("month", "Dieser Monat"),
            ("year", "Dieses Jahr"),
        ]
        try:
            parsed_from = self._parse_filter_date(self.step2_date_from_var.get(), is_end=False)
        except ValueError:
            parsed_from = None
        try:
            parsed_to = self._parse_filter_date(self.step2_date_to_var.get(), is_end=True)
        except ValueError:
            parsed_to = None

        menu_items = []
        for p_key, p_lbl in preset_specs:
            d1, d2 = self._compute_date_preset_range(p_key)
            active = (
                parsed_from is not None
                and parsed_to is not None
                and parsed_from.date() == d1
                and parsed_to.date() == d2
            )
            menu_items.append({
                "icon": "✓" if active else "",
                "label": p_lbl,
                "accent": active,
                "command": lambda k=p_key: self.set_step2_date_preset(k),
            })
        if self.step2_date_from_var.get().strip() or self.step2_date_to_var.get().strip():
            menu_items.append({"separator": True})
            menu_items.append({
                "icon": "✕",
                "label": "Datum leeren",
                "command": lambda: self.set_step2_date_preset("clear"),
            })
        btn = getattr(self, "btn_step2_date_preset", None)
        if btn is not None and btn.winfo_exists():
            x = btn.winfo_rootx()
            y = btn.winfo_rooty() + btn.winfo_height() + 2
        else:
            x = self.winfo_rootx() + 200
            y = self.winfo_rooty() + 140
        FluentPopupMenu(self, menu_items, x, y)

    def open_step2_date_picker(self, anchor_widget, target_var: tk.StringVar):
        if getattr(self, "_calendar_popup", None) is not None:
            try:
                self._calendar_popup.close()
            except Exception:
                pass
            self._calendar_popup = None

        def _after_pick(_day):
            self.update_step2_detail_filter_ui()
            self.apply_step2_filters()

        self._calendar_popup = CalendarPopup(anchor_widget, target_var, on_select=_after_pick)
        return self._calendar_popup

    def set_step2_size_preset(self, size_label: str):
        self._suppress_step2_filter_trace = True
        try:
            if size_label == "clear" or self.step2_min_size_var.get().strip().upper() == size_label.upper():
                self.step2_min_size_var.set("")
            else:
                self.step2_min_size_var.set(size_label)
        finally:
            self._suppress_step2_filter_trace = False
        self.update_step2_detail_filter_ui()
        self.apply_step2_filters()

    def open_step2_size_preset_menu(self):
        """Öffnet ein helles Fluent-Menü zur Schnellauswahl der Mindest-Dateigröße in Schritt 2."""
        size_specs = [
            ("100 KB", "> 100 KB"),
            ("500 KB", "> 500 KB"),
            ("1 MB", "> 1 MB"),
        ]
        try:
            cur_bytes = self._parse_min_file_size(self.step2_min_size_var.get())
        except ValueError:
            cur_bytes = None

        menu_items = []
        for s_key, s_lbl in size_specs:
            target_b = self._parse_min_file_size(s_key)
            active = (cur_bytes is not None and target_b is not None and cur_bytes == target_b)
            menu_items.append({
                "icon": "✓" if active else "",
                "label": s_lbl,
                "accent": active,
                "command": lambda v=s_key: self.set_step2_size_preset(v),
            })
        if self.step2_min_size_var.get().strip():
            menu_items.append({"separator": True})
            menu_items.append({
                "icon": "✕",
                "label": "Größe leeren",
                "command": lambda: self.set_step2_size_preset("clear"),
            })
        btn = getattr(self, "btn_step2_size_preset", None)
        if btn is not None and btn.winfo_exists():
            x = btn.winfo_rootx()
            y = btn.winfo_rooty() + btn.winfo_height() + 2
        else:
            x = self.winfo_rootx() + 400
            y = self.winfo_rooty() + 140
        FluentPopupMenu(self, menu_items, x, y)

    def update_step2_detail_filter_ui(self):
        neutral_kw = dict(bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"))
        active_kw = dict(bg="#2563eb", fg="#ffffff", hover_bg="#1d4ed8", border_color=None, font=("Segoe UI", 9, "bold"))

        if hasattr(self, "step2_file_filter_buttons"):
            active_types = getattr(self, "step2_active_types", set())
            is_all = (len(active_types) == 0)
            for m_key, btn in self.step2_file_filter_buttons.items():
                if m_key == "all":
                    btn.update_style(**(active_kw if is_all else neutral_kw))
                else:
                    is_act = (not is_all) and (m_key in active_types)
                    btn.update_style(**(active_kw if is_act else neutral_kw))

        if hasattr(self, "btn_step2_date_preset"):
            preset_labels = [
                ("today", "Heute"),
                ("week", "Diese Woche"),
                ("month", "Dieser Monat"),
                ("year", "Dieses Jahr"),
            ]
            try:
                p_from = self._parse_filter_date(self.step2_date_from_var.get(), is_end=False)
            except ValueError:
                p_from = None
            try:
                p_to = self._parse_filter_date(self.step2_date_to_var.get(), is_end=True)
            except ValueError:
                p_to = None
            matched_lbl = ""
            if p_from is not None and p_to is not None:
                for p_key, p_lbl in preset_labels:
                    d1, d2 = self._compute_date_preset_range(p_key)
                    if p_from.date() == d1 and p_to.date() == d2:
                        matched_lbl = p_lbl
                        break
            if matched_lbl:
                self.btn_step2_date_preset.update_style(text=f"{matched_lbl} ▾", **active_kw)
            else:
                self.btn_step2_date_preset.update_style(text="Zeitraum ▾", **neutral_kw)

        if hasattr(self, "btn_step2_size_preset"):
            try:
                cur_bytes = self._parse_min_file_size(self.step2_min_size_var.get())
            except ValueError:
                cur_bytes = None
            size_labels = [
                ("100 KB", "> 100 KB"),
                ("500 KB", "> 500 KB"),
                ("1 MB", "> 1 MB"),
            ]
            matched_size = ""
            if cur_bytes is not None:
                for s_key, s_lbl in size_labels:
                    if cur_bytes == self._parse_min_file_size(s_key):
                        matched_size = s_lbl
                        break
            if matched_size:
                self.btn_step2_size_preset.update_style(text=f"{matched_size} ▾", **active_kw)
            else:
                self.btn_step2_size_preset.update_style(text="Größe ▾", **neutral_kw)

        if hasattr(self, "btn_step2_extra_onedrive"):
            self.btn_step2_extra_onedrive.update_style(**(active_kw if getattr(self, "step2_only_onedrive", False) else neutral_kw))

    def _has_active_step2_filters(self) -> bool:
        return bool(
            getattr(self, "step2_filter_chip", "all") != "all"
            or self.step2_search_var.get().strip()
            or bool(getattr(self, "step2_active_types", set()))
            or getattr(self, "step2_file_filter_mode", "all") != "all"
            or getattr(self, "step2_only_onedrive", False)
            or self.step2_date_from_var.get().strip()
            or self.step2_date_to_var.get().strip()
            or self.step2_min_size_var.get().strip()
        )

    def clear_step2_filters(self):
        if self._step2_filter_after_id is not None:
            try:
                self.after_cancel(self._step2_filter_after_id)
            except Exception:
                pass
            self._step2_filter_after_id = None
        self.step2_filter_chip = "all"
        self.step2_file_filter_mode = "all"
        self.step2_active_types = set()
        self.step2_only_onedrive = False
        self._suppress_step2_filter_trace = True
        try:
            for var in (
                self.step2_search_var,
                self.step2_date_from_var,
                self.step2_date_to_var,
                self.step2_min_size_var,
            ):
                if var.get():
                    var.set("")
        finally:
            self._suppress_step2_filter_trace = False
        self.update_step2_detail_filter_ui()
        self.apply_step2_filters()

    def _matches_step2_detail_filters(
        self,
        item: dict,
        file_mode: str,
        dt_from,
        dt_to,
        min_bytes: int | None,
        only_onedrive: bool = False,
        onedrive_roots: list[Path] | None = None,
    ) -> bool:
        is_dir = bool(item.get("is_dir"))
        ext_dot = ""
        src_p = item.get("src_path")
        if not is_dir:
            if src_p is not None:
                try:
                    ext_dot = Path(src_p).suffix.lower()
                except Exception:
                    ext_dot = ""
            if not ext_dot:
                raw_ext = str(item.get("ext", "")).strip().lower()
                ext_dot = f".{raw_ext}" if raw_ext and raw_ext not in ("ordner", "datei") else ""

        if only_onedrive:
            in_onedrive = False
            src_str_low = str(item.get("source_path", "")).lower()
            parts_low = [pt.strip() for pt in src_str_low.replace("/", "\\").split("\\") if pt.strip()]
            if any(pt == "onedrive" or pt.startswith("onedrive -") or pt.startswith("onedrive-") for pt in parts_low):
                in_onedrive = True
            elif onedrive_roots and src_p is not None:
                try:
                    p_obj = Path(src_p)
                    if any(root == p_obj or root in p_obj.parents for root in onedrive_roots):
                        in_onedrive = True
                except Exception:
                    pass
            if not in_onedrive:
                return False

        active_types = getattr(self, "step2_active_types", set())
        if active_types:
            type_matched = False
            if is_dir:
                target_ordner = str(item.get("target_folder", "")).lower()
                if "images" in active_types and "07_bilder" in target_ordner:
                    type_matched = True
                if "office" in active_types and any(k in target_ordner for k in ("finanzen", "wohnen", "vertraege", "arbeit")):
                    type_matched = True
                if "text_docs" in active_types and any(k in target_ordner for k in ("archiv", "eingang", "belege", "identitaet")):
                    type_matched = True
                if "media" in active_types and "09_videos" in target_ordner:
                    type_matched = True
                if "installers" in active_types and "08_programme" in target_ordner:
                    type_matched = True
                if "ordner" in target_ordner or "projekte" in target_ordner:
                    type_matched = True
            else:
                if "images" in active_types and ext_dot in IMAGE_EXTENSIONS:
                    type_matched = True
                if "text_docs" in active_types and ext_dot in TEXT_DOC_EXTENSIONS:
                    type_matched = True
                if "office" in active_types and ext_dot in OFFICE_EXTENSIONS:
                    type_matched = True
                if "media" in active_types and ext_dot in MEDIA_EXTENSIONS:
                    type_matched = True
                if "installers" in active_types and ext_dot in INSTALLER_EXTENSIONS:
                    type_matched = True
            if not type_matched:
                return False

        if dt_from is not None or dt_to is not None:
            mtime_ts = float(item.get("mtime_ts", 0.0) or 0.0)
            if mtime_ts <= 0.0:
                return False
            try:
                mtime = datetime.datetime.fromtimestamp(mtime_ts)
            except Exception:
                return False
            if dt_from is not None and mtime < dt_from:
                return False
            if dt_to is not None and mtime > dt_to:
                return False

        if min_bytes is not None:
            size_b = item.get("size_bytes", -1)
            try:
                if int(size_b) >= 0 and int(size_b) < min_bytes:
                    return False
            except Exception:
                pass

        return True

    @staticmethod
    def _matches_step2_chip(item: dict, chip_key: str) -> bool:
        if chip_key == "dups":
            return bool(item.get("is_duplicate"))
        if chip_key == "inbox":
            return (
                item.get("action") != "Gelöscht"
                and not item.get("is_duplicate")
                and str(item.get("target_sub", "")).startswith("01_Eingang_Inbox")
            )
        if chip_key == "cloud":
            return bool(item.get("is_cloud_only")) and item.get("action") != "Gelöscht"
        if chip_key == "deleted":
            return item.get("action") == "Gelöscht"
        return True

    @staticmethod
    def _matches_step2_query(item: dict, query_terms: list[str]) -> bool:
        if not query_terms:
            return True
        status_words = []
        if item.get("is_duplicate"):
            status_words.append("duplikat")
        elif item.get("has_duplicates"):
            status_words.append("original")
        if item.get("is_cloud_only"):
            status_words.append("cloud onedrive")
        if item.get("is_dir"):
            status_words.append("ordner")
        hay = " ".join([
            str(item.get("name", "")),
            str(item.get("ext", "")),
            str(item.get("size_str", "")),
            str(item.get("label", "")),
            str(item.get("target_sub", "")).replace("/", " \\ "),
            str(item.get("date", "")),
            str(item.get("source_path", "")),
            str(item.get("action", "")),
            " ".join(status_words),
        ]).lower()
        return all(term in hay for term in query_terms)

    def apply_step2_filters(self):
        """Filtert die Schritt-2-Tabelle live im Arbeitsspeicher via detach/reattach."""
        if self._step2_filter_after_id is not None:
            try:
                self.after_cancel(self._step2_filter_after_id)
            except Exception:
                pass
            self._step2_filter_after_id = None

        row_order = list(self._preview_row_order) if getattr(self, "_preview_row_order", None) else list(self.preview_items.keys())
        query_terms = [t for t in self.step2_search_var.get().strip().lower().split() if t]
        chip = getattr(self, "step2_filter_chip", "all")
        file_mode = getattr(self, "step2_file_filter_mode", "all")

        try:
            dt_from = self._parse_filter_date(self.step2_date_from_var.get(), is_end=False)
        except ValueError:
            dt_from = None
        try:
            dt_to = self._parse_filter_date(self.step2_date_to_var.get(), is_end=True)
        except ValueError:
            dt_to = None
        try:
            min_bytes = self._parse_min_file_size(self.step2_min_size_var.get())
        except ValueError:
            min_bytes = None

        only_onedrive = bool(getattr(self, "step2_only_onedrive", False))
        onedrive_roots = get_onedrive_roots() if only_onedrive else []

        # Chip-Zaehler ueber alle Vorschau-Eintraege aktualisieren
        counts = {"all": 0, "dups": 0, "inbox": 0, "cloud": 0, "deleted": 0}
        visible_rids = []
        for rid in row_order:
            item = self.preview_items.get(rid)
            if not item:
                continue
            counts["all"] += 1
            if self._matches_step2_chip(item, "dups"):
                counts["dups"] += 1
            if self._matches_step2_chip(item, "inbox"):
                counts["inbox"] += 1
            if self._matches_step2_chip(item, "cloud"):
                counts["cloud"] += 1
            if self._matches_step2_chip(item, "deleted"):
                counts["deleted"] += 1
            if (
                self._matches_step2_chip(item, chip)
                and self._matches_step2_query(item, query_terms)
                and self._matches_step2_detail_filters(
                    item,
                    file_mode,
                    dt_from,
                    dt_to,
                    min_bytes,
                    only_onedrive=only_onedrive,
                    onedrive_roots=onedrive_roots,
                )
            ):
                visible_rids.append(rid)

        if hasattr(self, "step2_chip_buttons"):
            neutral_kw = dict(bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"))
            active_kw = dict(bg="#2563eb", fg="#ffffff", hover_bg="#1d4ed8", border_color=None, font=("Segoe UI", 9, "bold"))
            for c_key, c_lbl in getattr(self, "_step2_chip_defs", []):
                btn = self.step2_chip_buttons.get(c_key)
                if btn is not None:
                    style_kw = dict(active_kw if c_key == chip else neutral_kw)
                    btn.update_style(text=f"{c_lbl} ({counts.get(c_key, 0)})", **style_kw)

        has_active_filter = self._has_active_step2_filters()
        if hasattr(self, "btn_step2_filter_reset"):
            if has_active_filter:
                self.btn_step2_filter_reset.update_style(bg="#eff6ff", fg="#1d4ed8", hover_bg="#dbeafe", border_color="#93c5fd", font=("Segoe UI", 9, "bold"))
            else:
                self.btn_step2_filter_reset.update_style(bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"))

        visible_set = set(visible_rids)
        current_visible = list(self.tree.get_children(""))
        if current_visible != visible_rids:
            hidden_sel = [rid for rid in self.tree.selection() if rid not in visible_set]
            if hidden_sel:
                self.tree.selection_remove(*hidden_sel)
            for rid in current_visible:
                if rid not in visible_set:
                    self.tree.detach(rid)
            for idx, rid in enumerate(visible_rids):
                self.tree.reattach(rid, "", idx)

        self._update_step2_summary(visible_count=len(visible_rids), total_count=len(row_order), is_filtered=has_active_filter)
        self._update_preview_pane_selection()
        self._update_execute_scope_button()

    # -------------------------------------------------------------
    # DUPLIKATE-LOGIK & AKTIONEN (SCHRITT 2)
    # -------------------------------------------------------------
    def update_row_display(self, row_id: str):
        item = self.preview_items.get(row_id)
        if not item:
            return

        is_dup = item.get("is_duplicate", False)
        act = item.get("action", "")

        # Formatierung des Zielordners mit Windows-Backslash
        raw_t = str(item.get("target_sub", ""))
        target_clean = os.path.normpath(raw_t) if os.path.isabs(raw_t) else raw_t.replace("/", " \\ ")
        cat_str = item["label"]  # Immer die saubere Kategorie (kein Suffix wie "(Original)"!)

        if not is_dup:
            if item.get("is_cloud_only"):
                dup_str = "☁️ Cloud"
            elif item.get("has_duplicates"):
                dup_str = "Original"
            else:
                dup_str = "—"

            if act == "Überspringen":
                target_str = "—"
                tag = "tag_dup"
            elif act == "Gelöscht":
                target_str = "—"
                dup_str = "Gelöscht"
                tag = "tag_del"
            else:
                target_str = target_clean
                if item.get("is_cloud_only"):
                    tag = "tag_cloud"
                elif item.get("has_duplicates"):
                    tag = "tag_orig"
                else:
                    tag = "tag_normal"
            act_display = act
        else:
            dup_str = "Duplikat"

            if act == "Überspringen":
                # Saubere Zielordner-Spalte: Nur "—", kein störendes "wird uebersprungen"
                target_str = "—"
                tag = "tag_dup"
                act_display = "Überspringen"
            elif act == "Mitkopieren":
                # Saubere Zielordner-Spalte: Zeigt den regulären Zielordner
                target_str = target_clean
                tag = "tag_dup_copy"
                act_display = "Mitkopieren"
            elif act == "Gelöscht":
                target_str = "—"
                dup_str = "Gelöscht"
                tag = "tag_del"
                act_display = "Gelöscht"
            else:
                target_str = target_clean
                tag = "tag_normal"
                act_display = act

        display_name = f"📁 {item['name']}" if item.get("is_dir") else item["name"]

        self.tree.item(row_id, values=(
            display_name,
            item["ext"],
            item.get("size_str", "—"),
            item.get("items_count_str", "—"),
            dup_str,
            cat_str,
            target_str,
            item["date"],
            item["source_path"],
            act_display
        ), tags=(tag,))

    def update_dup_counter(self):
        total_dups = len(self.duplicate_ids)
        skip_count = sum(1 for rid in self.duplicate_ids if self.preview_items[rid]["action"] == "Überspringen")
        copy_count = sum(1 for rid in self.duplicate_ids if self.preview_items[rid]["action"] == "Mitkopieren")
        del_count = sum(1 for rid in self.duplicate_ids if self.preview_items[rid]["action"] == "Gelöscht")

        skip_txt = f"● {skip_count} Überspringen"
        copy_txt = f"● {copy_count} Mitkopieren"
        del_txt = f"● {del_count} Gelöscht"

        neutral_kw = dict(bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"))

        # Farbliche Hervorhebung: Farben werden erst gesetzt wenn angewaehlt, dann BEIDE GLEICHFARBIG (Rechts wie Links)!
        if del_count == total_dups and total_dups > 0:
            # Beide Seiten identisch ROT
            self.badge_del.update_style(text=del_txt, bg="#dc2626", fg="#ffffff", hover_bg="#b91c1c", font=("Segoe UI", 9, "bold"))
            self.btn_dup_del.update_style(bg="#dc2626", fg="#ffffff", hover_bg="#b91c1c", font=("Segoe UI", 9, "bold"))

            self.badge_skip.update_style(text=skip_txt, **neutral_kw)
            self.btn_dup_skip.update_style(**neutral_kw)
            self.badge_copy.update_style(text=copy_txt, **neutral_kw)
            self.btn_dup_copy.update_style(**neutral_kw)

        elif copy_count > skip_count:
            # Beide Seiten identisch SMARAGDGRÜN
            self.badge_copy.update_style(text=copy_txt, bg="#059669", fg="#ffffff", hover_bg="#047857", font=("Segoe UI", 9, "bold"))
            self.btn_dup_copy.update_style(bg="#059669", fg="#ffffff", hover_bg="#047857", font=("Segoe UI", 9, "bold"))

            self.badge_skip.update_style(text=skip_txt, **neutral_kw)
            self.btn_dup_skip.update_style(**neutral_kw)
            self.badge_del.update_style(text=del_txt, **neutral_kw)
            self.btn_dup_del.update_style(**neutral_kw)

        else:
            # Beide Seiten identisch KORALL / WARM-PFIRSICH (maximaler Kontrast zu Blau, sauber & modern!)
            self.badge_skip.update_style(text=skip_txt, bg="#ea580c", fg="#ffffff", hover_bg="#c2410c", font=("Segoe UI", 9, "bold"))
            self.btn_dup_skip.update_style(bg="#ea580c", fg="#ffffff", hover_bg="#c2410c", font=("Segoe UI", 9, "bold"))

            self.badge_copy.update_style(text=copy_txt, **neutral_kw)
            self.btn_dup_copy.update_style(**neutral_kw)
            self.badge_del.update_style(text=del_txt, **neutral_kw)
            self.btn_dup_del.update_style(**neutral_kw)

    def set_duplicate_action(self, row_id: str, new_action: str):
        item = self.preview_items.get(row_id)
        if not item or not item.get("is_duplicate"):
            return

        item["action"] = new_action
        self.update_row_display(row_id)
        self.update_dup_counter()
        self.apply_step2_filters()

    def set_item_action(self, row_id: str, new_action: str):
        """Setzt die Aktion einer beliebigen Zeile (z. B. Cloud-Datei ueberspringen)."""
        item = self.preview_items.get(row_id)
        if not item or item.get("action") == "Gelöscht":
            return
        item["action"] = new_action
        self.update_row_display(row_id)
        if item.get("is_duplicate"):
            self.update_dup_counter()
        self.apply_step2_filters()

    def set_all_duplicates(self, new_action: str):
        for rid in self.duplicate_ids:
            item = self.preview_items[rid]
            if item["action"] != "Gelöscht":
                item["action"] = new_action
                self.update_row_display(rid)
        self.update_dup_counter()
        self.apply_step2_filters()

    def swap_roles_for_item(self, dup_row_id: str):
        """Tauscht die Rollen: Das Duplikat wird zum Original, das Original zum Duplikat."""
        dup_item = self.preview_items.get(dup_row_id)
        if not dup_item or not dup_item.get("is_duplicate"):
            return

        orig_item = dup_item.get("original_item")
        if not orig_item:
            return

        # Finde row_id des Originals
        orig_row_id = None
        for rid, it in self.preview_items.items():
            if it == orig_item:
                orig_row_id = rid
                break

        if not orig_row_id:
            return

        # Rollen tauschen
        base_act = "Kopieren" if self.action_mode.get() == "copy" else "Verschieben"

        dup_item["is_duplicate"] = False
        dup_item["has_duplicates"] = True
        dup_item["original_item"] = None
        dup_item["action"] = base_act

        orig_item["is_duplicate"] = True
        orig_item["has_duplicates"] = False
        orig_item["original_item"] = dup_item
        orig_item["action"] = "Überspringen"

        # Etwaige weitere Geschwister-Duplikate derselben Hash-Gruppe ebenfalls auf das neue Original zeigen lassen
        for rid, it in self.preview_items.items():
            if rid not in (dup_row_id, orig_row_id) and it.get("is_duplicate") and it.get("original_item") == orig_item:
                it["original_item"] = dup_item
                it["target_sub"] = dup_item["target_sub"]
                it["label"] = dup_item["label"]

        # duplicate_ids Liste aktualisieren
        if dup_row_id in self.duplicate_ids:
            self.duplicate_ids.remove(dup_row_id)
        if orig_row_id not in self.duplicate_ids:
            self.duplicate_ids.append(orig_row_id)

        self.update_row_display(dup_row_id)
        self.update_row_display(orig_row_id)
        self.update_dup_counter()
        self.apply_step2_filters()

    def _update_dup_strategy_button(self):
        short_names = {
            "clean_name": "Sauberer Name",
            "newest": "Neueste Datei",
            "oldest": "Älteste Datei",
            "shortest_path": "Kürzester Pfad",
        }
        cur = getattr(self, "dup_original_strategy", "clean_name")
        short = short_names.get(cur, "Original-Wahl")
        btn = getattr(self, "btn_dup_strategy", None)
        if btn is not None:
            is_custom = cur != "clean_name"
            btn.update_style(
                text=f"🎯 Original: {short} ▾",
                bg="#eff6ff" if is_custom else "#ffffff",
                fg="#1d4ed8" if is_custom else "#1e293b",
                hover_bg="#dbeafe" if is_custom else "#f1f5f9",
                border_color="#93c5fd" if is_custom else "#94a3b8",
                font=("Segoe UI", 9, "bold"),
            )

    def open_dup_strategy_menu(self):
        """Öffnet ein helles Fluent-Menü zur Wahl der automatischen Original-Strategie."""
        cur = getattr(self, "dup_original_strategy", "clean_name")
        menu_items = []
        for key, label in DUP_ORIGINAL_STRATEGIES:
            active = (key == cur)
            menu_items.append({
                "icon": "✓" if active else "",
                "label": label,
                "accent": active,
                "command": lambda s=key: self.apply_duplicate_original_strategy(s),
            })
        btn = getattr(self, "btn_dup_strategy", None)
        if btn is not None and btn.winfo_exists():
            x = btn.winfo_rootx()
            y = btn.winfo_rooty() + btn.winfo_height() + 2
        else:
            x = self.winfo_rootx() + 120
            y = self.winfo_rooty() + 140
        FluentPopupMenu(self, menu_items, x, y)

    def apply_duplicate_original_strategy(self, strategy: str) -> int:
        """Wendet eine Original-Wahl-Strategie sofort auf alle aktiven Duplikat-Gruppen an."""
        valid_keys = {k for k, _ in DUP_ORIGINAL_STRATEGIES}
        if strategy not in valid_keys:
            strategy = "clean_name"
        self.dup_original_strategy = strategy
        self._update_dup_strategy_button()

        # Gruppiere alle nicht gelöschten Elemente nach identischem Datei-Hash
        groups: dict[str, list[str]] = {}
        order_source = self._preview_row_order or list(self.preview_items.keys())
        for rid in order_source:
            it = self.preview_items.get(rid)
            if not it:
                continue
            fhash = it.get("hash") or ""
            if fhash and it.get("action") != "Gelöscht":
                groups.setdefault(fhash, []).append(rid)

        base_act = "Kopieren" if self.action_mode.get() == "copy" else "Verschieben"
        new_dup_ids: list[str] = []
        changed_groups = 0

        for fhash, rids in groups.items():
            if len(rids) < 2:
                # Nur noch eine aktive Datei in dieser Gruppe übrig
                if len(rids) == 1:
                    sole_rid = rids[0]
                    sole_it = self.preview_items[sole_rid]
                    if sole_it.get("is_duplicate"):
                        new_dup_ids.append(sole_rid)
                continue

            sorted_rids = sorted(
                rids,
                key=lambda rid: duplicate_priority_key(self.preview_items[rid], strategy),
            )
            winner_rid = sorted_rids[0]
            winner = self.preview_items[winner_rid]

            was_winner = bool(winner.get("has_duplicates") and not winner.get("is_duplicate"))
            if not was_winner:
                changed_groups += 1

            # Falls die Gruppe bisher einen anderen Zielordner am Original hatte, konsistent halten
            winner["is_duplicate"] = False
            winner["has_duplicates"] = True
            winner["original_item"] = None
            if winner.get("action") in ("Überspringen", "Mitkopieren", "", None):
                winner["action"] = base_act
            self.update_row_display(winner_rid)

            for dup_rid in sorted_rids[1:]:
                dup_it = self.preview_items[dup_rid]
                dup_it["is_duplicate"] = True
                dup_it["has_duplicates"] = False
                dup_it["original_item"] = winner
                dup_it["target_sub"] = winner["target_sub"]
                dup_it["label"] = winner["label"]
                if dup_it.get("action") not in ("Überspringen", "Mitkopieren"):
                    dup_it["action"] = "Überspringen"
                new_dup_ids.append(dup_rid)
                self.update_row_display(dup_rid)

        # Gelöschte Duplikate für konsistente Zähler erhalten
        for rid in self.duplicate_ids:
            it = self.preview_items.get(rid)
            if it and it.get("action") == "Gelöscht" and rid not in new_dup_ids:
                new_dup_ids.append(rid)

        if groups:
            self.duplicate_ids = new_dup_ids

        self.update_dup_counter()
        self.apply_step2_filters()
        self._update_step2_summary()
        return changed_groups

    def set_selected_duplicates_action(self, new_action: str):
        selected = self.tree.selection()
        if not selected:
            return
        updated = 0
        for rid in selected:
            item = self.preview_items.get(rid)
            if item and item.get("is_duplicate") and item.get("action") != "Gelöscht":
                item["action"] = new_action
                self.update_row_display(rid)
                updated += 1
        if updated > 0:
            self.update_dup_counter()
            self.apply_step2_filters()

# -------------------------------------------------------------
    # DATEINAMEN EINKÜRZEN & UMBENENNEN (VARIANTE 1 & 3)
    # -------------------------------------------------------------
    def _get_target_rows_for_shortening(self) -> list[str]:
        """Gibt die Ziel-Zeilen für Kürzungen zurück: Entweder markierte oder alle sichtbaren."""
        sel = list(self.tree.selection())
        if sel:
            return sel
        return list(self.tree.get_children())

    def open_shorten_names_menu(self):
        """Öffnet ein helles Fluent-Menü zum schnellen Einkürzen von Dateinamen."""
        vis_count = len(self.tree.get_children(""))
        sel_count = len(self.tree.selection())
        if 0 < sel_count < vis_count:
            scope_note = " – 1 markierte" if sel_count == 1 else f" – {sel_count} markierte"
        else:
            scope_note = " – Alle"

        menu_items = [
            {
                "icon": "📖",
                "label": f"Nur Autor & Haupttitel behalten{scope_note}",
                "command": lambda: self.apply_name_shortening("subtitles", 2),
            },
            {
                "icon": "📚",
                "label": f"Bis Serie / Bandnummer behalten{scope_note}",
                "command": lambda: self.apply_name_shortening("subtitles", 3),
            },
            {"kind": "sep"},
            {
                "icon": "📏",
                "label": f"Auf max. 40 Zeichen begrenzen{scope_note}",
                "command": lambda: self.apply_name_shortening("max_len", 40),
            },
            {
                "icon": "📏",
                "label": f"Auf max. 50 Zeichen begrenzen{scope_note}",
                "command": lambda: self.apply_name_shortening("max_len", 50),
            },
            {
                "icon": "📏",
                "label": f"Auf max. 60 Zeichen begrenzen{scope_note}",
                "command": lambda: self.apply_name_shortening("max_len", 60),
            },
            {
                "icon": "📏",
                "label": f"Auf max. 80 Zeichen begrenzen{scope_note}",
                "command": lambda: self.apply_name_shortening("max_len", 80),
            },
            {"kind": "sep"},
            {
                "icon": "🔤",
                "label": f"Unterstriche „_“ durch Leerzeichen ersetzen{scope_note}",
                "command": lambda: self.apply_name_shortening("clean_underscores"),
            },
            {
                "icon": "rename",
                "label": "Länge, Trenner & Ersetzen anpassen…",
                "command": self.open_custom_shorten_dialog,
            },
            {"kind": "sep"},
            {
                "icon": "refresh",
                "label": f"Auf Original-Dateinamen zurücksetzen{scope_note}",
                "shortcut": "Strg+Z",
                "command": self.reset_renamed_names,
            },
        ]
        btn = getattr(self, "btn_shorten_names", None)
        if btn is not None and btn.winfo_exists():
            x = btn.winfo_rootx()
            y = btn.winfo_rooty() + btn.winfo_height() + 2
        else:
            x = self.winfo_rootx() + 140
            y = self.winfo_rooty() + 140
        self._open_fluent_popup(menu_items, x, y)

    def apply_name_shortening(self, mode: str, param=None, target_rids=None):
        """Wendet eine Kürzungsfunktion auf die angegebenen oder aktiven Zeilen an."""
        if target_rids is None:
            target_rids = self._get_target_rows_for_shortening()
        if not target_rids:
            self.show_notice("Keine Dateien", "Es sind keine Dateien in der Vorschau vorhanden.")
            return

        changed = 0
        for rid in target_rids:
            it = self.preview_items.get(rid)
            if not it or it.get("action") == "Gelöscht":
                continue
            cur_name = it.get("name", "")
            if "orig_name" not in it:
                it["orig_name"] = cur_name

            if mode == "subtitles":
                d = param if isinstance(param, int) else 2
                new_name = shorten_filename_subtitles(cur_name, d)
            elif mode == "max_len":
                if isinstance(param, (tuple, list)) and len(param) >= 2:
                    m, wb = int(param[0]), bool(param[1])
                else:
                    m, wb = (param if isinstance(param, int) else 50), True
                new_name = shorten_filename_max_length(cur_name, m, break_on_word=wb)
            elif mode == "clean_underscores":
                new_name = clean_filename_underscores(cur_name)
            elif mode == "replace":
                f, w = param if isinstance(param, (tuple, list)) else ("", "")
                new_name = cur_name.replace(f, w) if f else cur_name
            else:
                new_name = cur_name

            cleaned = clean_windows_filename(new_name)
            # Endung erhalten falls noetig
            if not it.get("is_dir"):
                p_cur = Path(cur_name)
                ext = p_cur.suffix
                if ext and not cleaned.lower().endswith(ext.lower()):
                    cleaned += ext

            if cleaned and cleaned != cur_name:
                it["name"] = cleaned
                self.update_row_display(rid)
                changed += 1

        if changed > 0:
            if hasattr(self, "_update_preview_pane_selection"):
                self._update_preview_pane_selection()
            self.show_notice("Dateinamen gekürzt", f"{changed} von {len(target_rids)} Dateinamen wurden erfolgreich angepasst.")
        else:
            self.show_notice("Keine Änderung", "Die gewählte Kürzungsregel hat bei den ausgewählten Dateien keine Namen verändert.")

    def reset_renamed_names(self, target_rids=None):
        """Setzt die Dateinamen der ausgewaehlten Zeilen auf ihren urspruenglichen Zustand zurueck."""
        if target_rids is None:
            target_rids = self._get_target_rows_for_shortening()
        if not target_rids:
            return

        reset_count = 0
        for rid in target_rids:
            it = self.preview_items.get(rid)
            if not it:
                continue
            orig = it.get("orig_name")
            if orig and it.get("name") != orig:
                it["name"] = orig
                self.update_row_display(rid)
                reset_count += 1

        if reset_count > 0:
            if hasattr(self, "_update_preview_pane_selection"):
                self._update_preview_pane_selection()
            self.show_notice("Dateinamen zurückgesetzt", f"{reset_count} Dateinamen wurden auf das Original zurückgesetzt.")
        else:
            self.show_notice("Bereits Original", "Alle ausgewählten Dateien besitzen bereits ihren ursprünglichen Dateinamen.")

    def reset_to_original_preview(self):
        """Setzt alle Dateinamen, geaenderte Zielordner und Schritt-2-Filter sofort auf die Original-Vorschau zurueck."""
        if not self.preview_items:
            return

        names_reset = 0
        targets_reset = 0
        actions_reset = 0
        for rid, it in self.preview_items.items():
            if not it or it.get("action") == "Gelöscht":
                continue
            changed = False
            orig_name = it.get("orig_name")
            if orig_name and it.get("name") != orig_name:
                it["name"] = orig_name
                names_reset += 1
                changed = True
            orig_target = it.get("orig_target_sub")
            orig_label = it.get("orig_label")
            if orig_target is not None and (it.get("target_sub") != orig_target or it.get("label") != orig_label):
                it["target_sub"] = orig_target
                if orig_label is not None:
                    it["label"] = orig_label
                targets_reset += 1
                changed = True
            orig_action = it.get("orig_action")
            if orig_action and it.get("action") != orig_action:
                it["action"] = orig_action
                actions_reset += 1
                changed = True
            if changed:
                self.update_row_display(rid)

        had_filters = self._has_active_step2_filters()
        if had_filters:
            self.clear_step2_filters()
        else:
            self.update_dup_counter()
            self._update_step2_summary()

        if hasattr(self, "_update_preview_pane_selection"):
            self._update_preview_pane_selection()

        parts = []
        if names_reset:
            parts.append(f"{names_reset} Dateinamen")
        if targets_reset:
            parts.append(f"{targets_reset} Zielordner")
        if actions_reset and not targets_reset:
            parts.append(f"{actions_reset} Aktionen")
        if had_filters:
            parts.append("Filter")

        if parts:
            self.show_notice(
                "Original-Vorschau wiederhergestellt",
                f"Zurückgesetzt: {', '.join(parts)}.",
            )
        else:
            self.show_notice(
                "Bereits Original-Vorschau",
                "Alle Dateinamen, Zielordner und Filter entsprechen bereits der Original-Vorschau.",
            )

    def start_rename_selected_file(self, row_id=None):
        """Startet die Umbenennung fuer die ausgewaehlte Datei (F2 oder Rechtsklick)."""
        if row_id is None:
            sel = list(self.tree.selection())
            if not sel:
                return "break"
            if len(sel) > 1:
                self.open_custom_shorten_dialog()
                return "break"
            row_id = sel[0]

        it = self.preview_items.get(row_id)
        if not it:
            return "break"

        dlg = SingleFileRenameDialog(
            self,
            it,
            on_success=lambda new_name, r=row_id: self._on_single_file_renamed(r, new_name)
        )
        self.wait_window(dlg)
        return "break"

    def _on_single_file_renamed(self, row_id: str, new_name: str):
        it = self.preview_items.get(row_id)
        if not it:
            return
        if "orig_name" not in it:
            it["orig_name"] = it.get("name", "")
        old_name = it.get("name", "")
        it["name"] = new_name
        self.update_row_display(row_id)
        if hasattr(self, "_update_preview_pane_selection"):
            self._update_preview_pane_selection()
        if old_name != new_name:
            self.show_notice("Dateiname geändert", f"Neuer Name: {new_name}")

    def open_custom_shorten_dialog(self):
        """Oeffnet den erweiterten Kuerzungsdialog fuer markierte oder alle Dateien."""
        target_rids = self._get_target_rows_for_shortening()
        if not target_rids:
            self.show_notice("Keine Dateien", "Es sind keine Dateien in der Vorschau vorhanden.")
            return
        dlg = CustomShortenDialog(self, target_rids)
        self.wait_window(dlg)

    def open_custom_rules_manager(self):
        dlg = CustomRulesDialog(self)
        self.wait_window(dlg)

    def _apply_target_assignment(
        self,
        row_ids: list[str],
        target_sub: str,
        label: str,
        save_rule: bool = False,
        keyword: str = "",
        apply_to_preview: bool = False,
        forced_action_mode: str | None = None,
        out_rids: list[str] | None = None,
    ) -> int:
        """Weist den angegebenen Zeilen einen neuen Zielordner zu und speichert optional die Stichwort-Regel."""
        raw_t = str(target_sub or "").strip()
        if not raw_t:
            return 0
        clean_target = os.path.normpath(raw_t) if os.path.isabs(raw_t) else raw_t.replace("\\", "/")
        clean_label = str(label or "").strip() or os.path.basename(clean_target.rstrip("/\\")) or clean_target
        clean_kw = str(keyword or "").strip().lower()
        target_act = ("Verschieben" if forced_action_mode == "move" else "Kopieren") if forced_action_mode in ("copy", "move") else None

        updated_rids = set()
        updated_order = []
        updated_originals = []
        for rid in row_ids:
            item = self.preview_items.get(rid)
            if not item or item.get("action") == "Gelöscht":
                continue
            item["target_sub"] = clean_target
            item["label"] = clean_label
            if item.get("is_duplicate"):
                if item.get("action") == "Überspringen" or target_act == "Kopieren":
                    item["action"] = "Mitkopieren"
            elif target_act is not None:
                item["action"] = target_act
            elif item.get("action") == "Überspringen":
                item["action"] = "Verschieben" if self.action_mode.get() == "move" else "Kopieren"
            if rid not in updated_rids:
                updated_rids.add(rid)
                updated_order.append(rid)
            if item.get("has_duplicates"):
                updated_originals.append(item)

        if save_rule and len(clean_kw) >= 2:
            save_custom_rule(clean_kw, clean_target, clean_label)
            if apply_to_preview:
                for rid, item in self.preview_items.items():
                    if rid in updated_rids or item.get("action") == "Gelöscht" or item.get("is_duplicate"):
                        continue
                    p_ext = item["src_path"].suffix.lower() if not item.get("is_dir") else ""
                    if p_ext in IMAGE_EXTENSIONS or p_ext in INSTALLER_EXTENSIONS or p_ext in MEDIA_EXTENSIONS:
                        continue
                    hay = f"{item.get('name', '')} {item.get('source_path', '')}".lower()
                    if _rule_matches_text(clean_kw, hay):
                        item["target_sub"] = clean_target
                        item["label"] = clean_label
                        if target_act is not None:
                            item["action"] = target_act
                        updated_rids.add(rid)
                        updated_order.append(rid)
                        if item.get("has_duplicates"):
                            updated_originals.append(item)

        if updated_originals:
            for dup_rid in self.duplicate_ids:
                dup_item = self.preview_items.get(dup_rid)
                if dup_item and dup_item.get("original_item") in updated_originals:
                    dup_item["target_sub"] = clean_target
                    dup_item["label"] = clean_label
                    if dup_rid not in updated_rids:
                        updated_rids.add(dup_rid)

        for rid in updated_rids:
            self.update_row_display(rid)
        if updated_rids:
            self.apply_step2_filters()
        if out_rids is not None:
            out_rids.extend(updated_order)
        return len(updated_rids)

    def add_recent_target_folder(self, path: str, pinned: bool = False, label: str = "", keep_slot1: bool = False):
        """Fügt einen Zielordner zur Schnellauswahl (max. 5 Plätze, MRU + Fixierung) hinzu."""
        raw_p = str(path or "").strip()
        if not raw_p:
            return
        norm_p = os.path.normpath(raw_p) if os.path.isabs(raw_p) else raw_p.replace("\\", "/")
        lbl = str(label or "").strip() or os.path.basename(norm_p.rstrip("\\/")) or norm_p

        if not hasattr(self, "recent_target_folders"):
            self.recent_target_folders = []

        def _norm_cmp(s: str) -> str:
            t = str(s or "").strip()
            return (os.path.normpath(t) if os.path.isabs(t) else t.replace("\\", "/")).lower()

        existing_idx = None
        cmp_new = _norm_cmp(norm_p)
        for i, item in enumerate(self.recent_target_folders):
            if _norm_cmp(item.get("path", "")) == cmp_new:
                existing_idx = i
                break

        if existing_idx is not None:
            old = self.recent_target_folders[existing_idx]
            is_pinned = old.get("pinned", False) or pinned
            cur_lbl = lbl if lbl != norm_p else (old.get("label") or lbl)
            updated_entry = {
                "path": norm_p,
                "pinned": is_pinned,
                "label": cur_lbl,
                "timestamp": time.time(),
            }
            if keep_slot1:
                self.recent_target_folders[existing_idx] = updated_entry
            else:
                self.recent_target_folders.pop(existing_idx)
                self.recent_target_folders.insert(0, updated_entry)
        else:
            new_entry = {
                "path": norm_p,
                "pinned": pinned,
                "label": lbl,
                "timestamp": time.time(),
            }
            if keep_slot1 and len(self.recent_target_folders) >= 1:
                self.recent_target_folders.append(new_entry)
            else:
                self.recent_target_folders.insert(0, new_entry)

        # Max 5 Plätze: älteste nicht fixierte Einträge zuerst verwerfen (Slot 0 schützen)
        while len(self.recent_target_folders) > 5:
            dropped = False
            for i in range(len(self.recent_target_folders) - 1, 0, -1):
                if not self.recent_target_folders[i].get("pinned", False):
                    self.recent_target_folders.pop(i)
                    dropped = True
                    break
            if not dropped:
                self.recent_target_folders.pop()

        self.update_recent_target_buttons()
        self._save_app_state()

    def toggle_pin_recent_target(self, path: str) -> bool:
        """Fixiert einen Ordner in der Schnellauswahl oder hebt die Fixierung auf."""
        raw_p = str(path or "").strip()
        cmp_p = (os.path.normpath(raw_p) if os.path.isabs(raw_p) else raw_p.replace("\\", "/")).lower()
        new_pinned = False
        for item in getattr(self, "recent_target_folders", []):
            it_p = str(item.get("path", "")).strip()
            it_cmp = (os.path.normpath(it_p) if os.path.isabs(it_p) else it_p.replace("\\", "/")).lower()
            if it_cmp == cmp_p:
                item["pinned"] = not item.get("pinned", False)
                new_pinned = item["pinned"]
                break
        self.update_recent_target_buttons()
        self._save_app_state()
        return new_pinned

    def remove_recent_target_folder(self, path: str):
        """Entfernt einen Ordner aus der 5-Slot-Schnellauswahl."""
        raw_p = str(path or "").strip()
        cmp_p = (os.path.normpath(raw_p) if os.path.isabs(raw_p) else raw_p.replace("\\", "/")).lower()
        cur_list = getattr(self, "recent_target_folders", [])
        self.recent_target_folders = [
            it for it in cur_list
            if (os.path.normpath(str(it.get("path", "")).strip()) if os.path.isabs(str(it.get("path", "")).strip()) else str(it.get("path", "")).strip().replace("\\", "/")).lower() != cmp_p
        ]
        self.update_recent_target_buttons()
        self._save_app_state()

    def update_recent_target_buttons(self):
        """Aktualisiert die Schnellauswahl-Buttons für die letzten Zielordner in Toolbar und Schritt 1."""
        recent = getattr(self, "recent_target_folders", [])
        if hasattr(self, "btn_quick_last_target"):
            if recent and len(recent) > 0:
                first = recent[0]
                lbl = first.get("label") or os.path.basename(first["path"].rstrip("\\/")) or first["path"]
                short_lbl = lbl if len(lbl) <= 16 else lbl[:14] + "…"
                pinned_sym = "📌 " if first.get("pinned") else ""
                self.btn_quick_last_target.update_style(
                    text=f"⚡ {pinned_sym}Zuletzt: {short_lbl}",
                    bg="#eff6ff",
                    fg="#1d4ed8",
                    hover_bg="#dbeafe",
                    border_color="#93c5fd",
                )
                attach_tooltip(
                    self.btn_quick_last_target,
                    f"1-Klick: Zielordner sofort auf „{lbl}“ umstellen\n(gilt für markierte Einträge – oder für alle, wenn nichts markiert ist)\n{first['path']}",
                )
            else:
                self.btn_quick_last_target.update_style(
                    text="⚡ Zuletzt: —",
                    bg="#f8fafc",
                    fg="#94a3b8",
                    hover_bg="#f8fafc",
                    border_color="#e2e8f0",
                )
                attach_tooltip(self.btn_quick_last_target, "Noch kein letzter Zielordner gemerkt")

    def quick_apply_last_target(self):
        """Wendet den zuletzt genutzten Zielordner mit 1 Klick auf alle (oder markierte) Zeilen an."""
        recent = getattr(self, "recent_target_folders", [])
        if not recent:
            self.assign_target_folder_selected()
            return
        first = recent[0]
        self.quick_assign_target_to_preview(first["path"], first.get("label", ""))

    def open_assign_target_menu(self):
        """Öffnet ein modernes Fluent-Menü mit den letzten 5 Zielordnern sowie Dialog-Aufruf."""
        recent = getattr(self, "recent_target_folders", [])
        all_active = [r for r, it in self.preview_items.items() if it.get("action") != "Gelöscht"]
        sel_rids = [r for r in self.tree.selection() if r in self.preview_items and self.preview_items[r].get("action") != "Gelöscht"]
        scope_hint = f"{len(sel_rids)} markierte" if (sel_rids and len(sel_rids) < len(all_active)) else f"alle {len(all_active)}"

        menu_items = [
            {
                "icon": "folder",
                "label": "Zielordner & Schnellauswahl öffnen…",
                "command": self.assign_target_folder_selected,
            },
            {"kind": "sep"},
            {
                "kind": "header",
                "label": f"SCHNELLAUSWAHL · 1-KLICK FÜR {scope_hint.upper()}",
            },
        ]

        if not recent:
            menu_items.append({
                "icon": "info",
                "label": "Noch keine Ordner gespeichert – Dialog öffnen…",
                "command": self.assign_target_folder_selected,
            })
        else:
            for idx, slot in enumerate(recent[:5]):
                p = slot["path"]
                lbl = slot.get("label") or os.path.basename(p.rstrip("\\/")) or p
                pinned = bool(slot.get("pinned", False))
                ico = "pin" if pinned else ("clock" if idx == 0 else "folder")
                tag_right = "Zuletzt" if idx == 0 else ("Fixiert" if pinned else "")
                disp_p = p if len(p) <= 30 else p[:12] + "…" + p[-15:]
                item_label = f"{lbl}  ·  {disp_p}" if lbl.lower() != disp_p.lower() else disp_p
                menu_items.append({
                    "icon": ico,
                    "label": item_label,
                    "shortcut": tag_right,
                    "command": lambda sp=p, sl=lbl: self.quick_assign_target_to_preview(sp, sl),
                })

        menu_items.append({"kind": "sep"})
        menu_items.append({
            "kind": "header",
            "label": "ZIELORDNER IM DIALOG ÄNDERN",
        })
        menu_items.append({
            "icon": "check",
            "label": f"Für alle {len(all_active)} Einträge festlegen…",
            "command": lambda: self.assign_target_folder_selected(list(all_active)),
        })
        if sel_rids and len(sel_rids) < len(all_active):
            menu_items.append({
                "icon": "check",
                "label": f"Nur für {len(sel_rids)} markierte Einträge festlegen…",
                "command": lambda: self.assign_target_folder_selected(list(sel_rids)),
            })

        btn = getattr(self, "btn_assign_target", None)
        if btn is not None and btn.winfo_exists():
            x = btn.winfo_rootx()
            y = btn.winfo_rooty() + btn.winfo_height() + 2
        else:
            x = self.winfo_rootx() + 100
            y = self.winfo_rooty() + 100
        self._open_fluent_popup(menu_items, x, y)

    def quick_assign_target_to_preview(self, target_path: str, label: str = "", apply_to_all: bool | None = None):
        """Weist allen oder markierten Zeilen in Schritt 2 sofort einen Zielordner zu (1-Klick ohne Dialog)."""
        all_active_rids = [r for r, it in self.preview_items.items() if it.get("action") != "Gelöscht"]
        sel_rids = [r for r in self.tree.selection() if r in self.preview_items and self.preview_items[r].get("action") != "Gelöscht"]

        if not all_active_rids:
            self.show_notice("Keine Dateien", "Es sind keine aktiven Dateien in der Vorschau vorhanden.", kind="info")
            return

        if apply_to_all is None:
            target_rids = all_active_rids if not sel_rids else sel_rids
        elif apply_to_all:
            target_rids = all_active_rids
        else:
            target_rids = sel_rids or all_active_rids

        clean_target = os.path.normpath(target_path) if os.path.isabs(target_path) else target_path.replace("\\", "/")
        clean_label = label or os.path.basename(clean_target.rstrip("/\\")) or clean_target

        self.add_recent_target_folder(clean_target, label=clean_label)

        run_rids: list[str] = []
        updated_count = self._apply_target_assignment(
            target_rids,
            target_sub=clean_target,
            label=clean_label,
            save_rule=False,
            keyword="",
            apply_to_preview=False,
            forced_action_mode=None,
            out_rids=run_rids,
        )
        self._save_app_state()

        scope_str = f"alle {updated_count} Einträge" if len(target_rids) == len(all_active_rids) else f"{updated_count} markierte Einträge"
        self.show_notice(
            "Zielordner zugewiesen",
            f"Zielordner für {scope_str} umgestellt auf:",
            clean_target,
            kind="success",
            auto_close_ms=2500,
        )

    def open_step1_recent_targets_menu(self):
        """Öffnet in Schritt 1 ein modernes Fluent-Menü mit den letzten Zielordnern."""
        recent = getattr(self, "recent_target_folders", [])
        menu_items = [
            {
                "kind": "header",
                "label": "SCHNELLAUSWAHL · LETZTE ZIELORDNER",
            }
        ]
        if not recent:
            menu_items.append({
                "icon": "info",
                "label": "Noch keine Zielordner in Historie",
                "command": self.browse_target,
            })
        else:
            for idx, slot in enumerate(recent[:5]):
                p = slot["path"]
                if not os.path.isabs(p):
                    continue
                lbl = slot.get("label") or os.path.basename(p.rstrip("\\/")) or p
                pinned = bool(slot.get("pinned", False))
                ico = "pin" if pinned else ("clock" if idx == 0 else "folder")
                tag_right = "Zuletzt" if idx == 0 else ("Fixiert" if pinned else "")
                disp_p = p if len(p) <= 34 else p[:14] + "…" + p[-17:]
                item_label = f"{lbl}  ·  {disp_p}" if lbl.lower() != disp_p.lower() else disp_p
                menu_items.append({
                    "icon": ico,
                    "label": item_label,
                    "shortcut": tag_right,
                    "command": lambda sp=p: self._set_target_dir_from_recent(sp),
                })
            if len(menu_items) == 1:
                menu_items.append({
                    "icon": "folder",
                    "label": "Zielordner auf PC wählen…",
                    "command": self.browse_target,
                })

        btn = getattr(self, "btn_target_recent", None)
        if btn is not None and btn.winfo_exists():
            x = btn.winfo_rootx()
            y = btn.winfo_rooty() + btn.winfo_height() + 2
        else:
            x = self.winfo_rootx() + 100
            y = self.winfo_rooty() + 100
        self._open_fluent_popup(menu_items, x, y)

    def _set_target_dir_from_recent(self, path: str):
        """Setzt das Zielverzeichnis in Schritt 1 direkt aus der Schnellauswahl."""
        self.target_dir.set(os.path.normpath(path))
        self.target_mode = "custom"
        self.update_target_mode_ui()
        self.add_recent_target_folder(path)
        self._save_app_state()

    def assign_target_folder_selected(self, row_ids: list[str] | None = None):
        """Oeffnet den Zielordner-Dialog fuer eine oder mehrere Zeilen in Schritt 2."""
        all_active_rids = [rid for rid, it in self.preview_items.items() if it.get("action") != "Gelöscht"]
        if not all_active_rids:
            self.show_notice("Keine Dateien", "Es sind keine aktiven Dateien in der Vorschau vorhanden.", kind="info")
            return

        target_rids = list(row_ids) if row_ids is not None else list(self.tree.selection())
        valid_rids = []
        for rid in target_rids:
            item = self.preview_items.get(rid)
            if item and item.get("action") != "Gelöscht":
                valid_rids.append(rid)

        # Wenn nichts markiert war, standardmässig ALLE aktiven Zeilen vorschlagen!
        if not valid_rids:
            valid_rids = list(all_active_rids)
            is_all_default = True
        else:
            is_all_default = (len(valid_rids) == len(all_active_rids))

        active_items = [self.preview_items[r] for r in valid_rids if r in self.preview_items]
        if not active_items:
            active_items = [self.preview_items[all_active_rids[0]]]

        dlg = TargetFolderDialog(self, active_items, total_active_count=len(all_active_rids))
        self.wait_window(dlg)
        if not dlg.result:
            return

        res = dlg.result
        exec_mode = res.get("execute_mode")
        apply_all = bool(res.get("apply_to_all", is_all_default))
        target_rids_to_use = all_active_rids if apply_all else valid_rids

        clean_target = res["target_sub"]
        clean_label = res["label"]
        self.add_recent_target_folder(clean_target, label=clean_label)

        run_rids: list[str] = []
        updated_count = self._apply_target_assignment(
            target_rids_to_use,
            target_sub=clean_target,
            label=clean_label,
            save_rule=res["save_rule"],
            keyword=res["keyword"],
            apply_to_preview=res["apply_to_preview"],
            forced_action_mode=exec_mode,
            out_rids=run_rids,
        )
        self._save_app_state()

        if exec_mode in ("copy", "move") and run_rids:
            self.execute_sort(target_row_ids=run_rids, scope_label="aus Auswahl", override_action_mode=exec_mode)
        else:
            scope_str = f"alle {updated_count} Einträge" if len(target_rids_to_use) == len(all_active_rids) else f"{updated_count} markierte Einträge"
            self.show_notice(
                "Zielordner zugewiesen",
                f"Zielordner für {scope_str} erfolgreich umgestellt auf:",
                clean_target,
                kind="success",
                auto_close_ms=2500,
            )

    def delete_single_file(self, row_id: str):
        """Loescht eine einzelne Datei."""
        self.tree.selection_set(row_id)
        self.delete_selected_files()

    def _move_path_to_trash(self, path: Path) -> None:
        """Verschiebt eine Datei in den Papierkorb oder loescht sie direkt als Fallback."""
        if HAS_SEND2TRASH:
            send2trash(str(path))
        else:
            path.unlink()

    def _has_network_delete_blocker(self, item_list: list[dict]) -> tuple[bool, bool]:
        """Prueft, ob Netzwerk-Dateien geloescht werden sollen -> (abbrechen, hat_netzlaufwerk)."""
        has_net = any(is_network_path(it.get("src_path", "")) for it in item_list)
        if has_net and self.network_guard_var.get():
            self.show_notice(
                "NetzwerkSchutz: Löschen auf Netzlaufwerk gestoppt",
                "Auf Netzlaufwerken bzw. Firmenservern gibt es keinen Windows-Papierkorb.",
                "Gelöschte Dateien wären für Sie und Kollegen sofort unwiderruflich weg.\n\n"
                "Empfehlung: Nutzen Sie stattdessen „⛔ Überspringen“ (die Datei bleibt unangetastet) "
                "oder schalten Sie den „🛡️ NetzwerkSchutz“ in Schritt 1 nur aus, wenn Sie wirklich endgültig löschen möchten.",
                kind="warning",
            )
            return True, True
        return False, has_net

    def delete_selected_files(self):
        """Verschiebt die ausgewaehlten Dateien in den Windows-Papierkorb (unterstuetzt Mehrfachauswahl)."""
        selected = self.tree.selection()
        if not selected:
            self.show_notice(
                "Auswahl erforderlich",
                "Bitte wählen Sie mindestens eine Datei in der Tabelle aus.",
                "Mit Strg oder Umschalt lassen sich mehrere Zeilen gleichzeitig markieren.",
                kind="info",
            )
            return

        items_to_delete = []
        for rid in selected:
            item = self.preview_items.get(rid)
            if item and item.get("action") != "Gelöscht":
                items_to_delete.append((rid, item))

        if not items_to_delete:
            self.show_notice(
                "Bereits gelöscht",
                "Alle ausgewählten Dateien befinden sich bereits im Papierkorb.",
                kind="info",
            )
            return

        blocked, has_net = self._has_network_delete_blocker([it for _, it in items_to_delete])
        if blocked:
            return

        count = len(items_to_delete)
        if has_net:
            file_preview = items_to_delete[0][1]["name"] if count == 1 else f"{count} Dateien auf Netzlaufwerk"
            title_txt = "ACHTUNG: Endgültig löschen (Netzlaufwerk)"
            headline = f"⚠️ {file_preview} SOFORT und ENDGÜLTIG löschen?"
            subline = (
                "Auf Netzlaufwerken gibt es KEINEN Windows-Papierkorb!\n"
                "Die Dateien sind für alle Benutzer unwiderruflich verloren."
            )
            confirm_btn = "Endgültig löschen"
        elif count == 1:
            rid, item = items_to_delete[0]
            title_txt = "In Papierkorb verschieben"
            headline = f"Möchten Sie „{item['name']}“ in den Papierkorb verschieben?"
            subline = f"{item['src_path']}\nDie Datei kann später aus dem Windows-Papierkorb wiederhergestellt werden."
            confirm_btn = "In Papierkorb verschieben"
        else:
            file_list = "\n".join([f"• {it['name']}" for _, it in items_to_delete[:6]])
            if count > 6:
                file_list += f"\n… und {count - 6} weitere Dateien"
            title_txt = "In Papierkorb verschieben"
            headline = f"Möchten Sie {count} Dateien in den Papierkorb verschieben?"
            subline = f"{file_list}\nAlle Dateien können später aus dem Papierkorb wiederhergestellt werden."
            confirm_btn = "In Papierkorb verschieben"

        confirmed = self.confirm_action(
            title=title_txt,
            headline=headline,
            subline=subline,
            confirm_text=confirm_btn,
        )
        if not confirmed:
            return

        deleted_count = 0
        deleted_paths = set()
        failed_names = []
        for rid, item in items_to_delete:
            try:
                self._move_path_to_trash(item["src_path"])
                item["action"] = "Gelöscht"
                self.update_row_display(rid)
                deleted_count += 1
                deleted_paths.add(str(item["src_path"]))
            except Exception as e:
                failed_names.append(item["name"])
                print(f"Fehler beim Löschen von {item['src_path']}: {e}")

        if deleted_count > 0:
            self._finalize_in_memory_deletions(deleted_paths)
        else:
            self.update_dup_counter()

        if failed_names and deleted_count == 0:
            self.show_notice(
                "Löschen nicht möglich",
                "Die gewählten Dateien konnten nicht gelöscht werden.",
                "Mögliche Ursache: Keine Löschrechte, Datei wird von einem Kollegen/Programm verwendet oder Netzlaufwerk ohne Papierkorb.",
                kind="warning",
            )
            return

        del_lbl = "1 Datei wurde" if deleted_count == 1 else f"{deleted_count} Dateien wurden"
        extra_warn = (
            f"\n⚠️ {len(failed_names)} Datei(en) konnten wegen fehlender Rechte oder Sperre nicht gelöscht werden."
            if failed_names
            else ""
        )
        dest_word = "unwiderruflich vom Netzlaufwerk gelöscht" if has_net else "in den Windows-Papierkorb verschoben"
        self.show_notice(
            "Löschvorgang abgeschlossen",
            f"{del_lbl} {dest_word}.{extra_warn}",
            kind="warning" if failed_names else "success",
        )

    def delete_all_duplicates(self):
        active_dups = [rid for rid in self.duplicate_ids if self.preview_items[rid]["action"] != "Gelöscht"]
        if not active_dups:
            self.show_notice("Keine Duplikate", "Es sind keine ungelöschten Duplikate vorhanden.", kind="info")
            return

        blocked, has_net = self._has_network_delete_blocker([self.preview_items[rid] for rid in active_dups])
        if blocked:
            return

        n_dups = len(active_dups)
        dup_lbl = "1 Duplikat" if n_dups == 1 else f"alle {n_dups} Duplikate"
        if has_net:
            headline = f"⚠️ {dup_lbl} auf dem Netzlaufwerk ENDGÜLTIG löschen?"
            subline = "Auf Netzlaufwerken gibt es KEINEN Papierkorb – nur die Originaldateien bleiben erhalten."
        else:
            headline = f"Möchten Sie {dup_lbl} in den Papierkorb verschieben?"
            subline = "Alle Originaldateien im Quellordner bleiben vollständig erhalten."
        confirmed = self.confirm_action(
            title="Duplikate löschen",
            headline=headline,
            subline=subline,
            confirm_text="Endgültig löschen" if has_net else "Duplikate löschen",
        )
        if not confirmed:
            return

        deleted_count = 0
        deleted_paths = set()
        failed_cnt = 0
        for rid in active_dups:
            item = self.preview_items[rid]
            try:
                self._move_path_to_trash(item["src_path"])
                item["action"] = "Gelöscht"
                self.update_row_display(rid)
                deleted_count += 1
                deleted_paths.add(str(item["src_path"]))
            except Exception as e:
                failed_cnt += 1
                print(f"Fehler beim Loeschen von {item['src_path']}: {e}")

        if deleted_count > 0:
            self._finalize_in_memory_deletions(deleted_paths)
        else:
            self.update_dup_counter()
        del_lbl = "1 Duplikat wurde" if deleted_count == 1 else f"{deleted_count} Duplikate wurden"
        if failed_cnt and deleted_count == 0:
            self.show_notice("Löschen nicht möglich", "Keine Duplikate konnten gelöscht werden (Berechtigung oder Dateisperre).", kind="warning")
            return
        dest_word = "endgültig gelöscht" if has_net else "in den Windows-Papierkorb verschoben"
        self.show_notice(
            "Duplikate gelöscht",
            f"{del_lbl} {dest_word}.",
            kind="warning" if failed_cnt else "success",
        )

    def _finalize_in_memory_deletions(self, deleted_paths: set[str]):
        """Aktualisiert Status, Snapshot, Filter-Chips und Vorschau direkt im Speicher (ohne langsamen Komplett-Rescan)."""
        if self._last_scan_snapshot is not None and deleted_paths:
            self._last_scan_snapshot = tuple(
                entry for entry in self._last_scan_snapshot if entry[0] not in deleted_paths
            )
        self.update_dup_counter()
        self.apply_step2_filters()

    def _update_step2_summary(self, refresh_tag: str = "", visible_count: int | None = None,
                              total_count: int | None = None, is_filtered: bool | None = None):
        all_vals = list(self.preview_items.values())
        active_vals = [it for it in all_vals if it.get("action") != "Gelöscht"]
        matched_count = sum(1 for it in active_vals if not it.get("is_duplicate") and not it["target_sub"].startswith("01_Eingang_Inbox"))
        inbox_count = sum(1 for it in active_vals if not it.get("is_duplicate") and it["target_sub"].startswith("01_Eingang_Inbox"))
        dup_count = sum(1 for it in active_vals if it.get("is_duplicate"))
        dir_count = sum(1 for it in active_vals if it.get("is_dir"))
        doc_count = len(active_vals) - dir_count
        found_str = f"{doc_count} Dokumente, {dir_count} Ordner" if dir_count > 0 else f"{len(active_vals)} Dokumente"
        filter_tag = getattr(self, "_last_filter_tag", "")
        if is_filtered is None:
            is_filtered = self._has_active_step2_filters()
        if visible_count is None:
            visible_count = len(self.tree.get_children(""))
        if total_count is None:
            total_count = len(all_vals)
        view_tag = f"  |  Ansicht: {visible_count} von {total_count}" if is_filtered else ""
        self.lbl_summary.config(
            text=f"Gefunden: {found_str}  |  Zugeordnet (02-10): {matched_count}  |  Unbekannt (-> Inbox): {inbox_count}  |  Erkannte Duplikate: {dup_count}{filter_tag}{view_tag}{refresh_tag}"
        )

    def compare_selected_duplicate(self):
        selected = self.tree.selection()
        if not selected:
            self.show_notice("Auswahl erforderlich", "Bitte wählen Sie zuerst eine Zeile in der Tabelle aus.", kind="info")
            return
        self.open_compare_dialog(selected[0])

    def open_compare_dialog(self, row_id: str):
        item = self.preview_items.get(row_id)
        if not item:
            return

        if not item.get("is_duplicate") and not item.get("has_duplicates"):
            self.show_notice(
                "Kein Duplikat",
                f"„{item['name']}“ ist kein Duplikat und besitzt keine weiteren Dubletten.",
                kind="info",
            )
            return

        orig_item = item if item.get("has_duplicates") else item.get("original_item")
        orig_rid = None
        if orig_item:
            for rid, it in self.preview_items.items():
                if it is orig_item or it == orig_item:
                    orig_rid = rid
                    break

        # Alle aktiven Geschwister-Duplikate zum selben Original sammeln
        sibling_rids: list[str] = []
        if item.get("is_duplicate") and item.get("action") != "Gelöscht":
            sibling_rids.append(row_id)
        for rid in self.duplicate_ids:
            it = self.preview_items.get(rid)
            if not it or it.get("action") == "Gelöscht":
                continue
            if (it.get("original_item") is orig_item or it.get("original_item") == orig_item) and rid not in sibling_rids:
                sibling_rids.append(rid)

        if not orig_item or not sibling_rids:
            self.show_notice("Hinweis", "Das zugehörige Zwillings-Dokument konnte nicht ermittelt werden.", kind="info")
            return

        state = {"pos": 0, "dup_rid": sibling_rids[0], "dup_item": self.preview_items[sibling_rids[0]]}

        # Dialog-Fenster erstellen
        dlg = tk.Toplevel(self)
        dlg.title("Duplikate im Detail vergleichen & Rollen tauschen")
        self.update_idletasks()
        dw, dh = 920, 650
        px = self.winfo_rootx() + max(0, (self.winfo_width() - dw) // 2)
        py = self.winfo_rooty() + max(0, (self.winfo_height() - dh) // 2)
        dlg.geometry(f"{dw}x{dh}+{px}+{py}")
        dlg.minsize(820, 560)
        dlg.configure(bg="#f8fafc")
        dlg.transient(self)
        dlg.grab_set()
        dlg._preview_photos = []
        dlg._sibling_rids = sibling_rids
        dlg._current_dup_rid = state["dup_rid"]

        top_hdr = tk.Frame(dlg, bg="#f8fafc", padx=16, pady=12)
        top_hdr.pack(fill="x")

        hdr_left = tk.Frame(top_hdr, bg="#f8fafc")
        hdr_left.pack(side="left", fill="x", expand=True)
        tk.Label(hdr_left, text="🔍 Duplikate-Vergleich: Inhalt zu 100 % identisch (gleicher MD5-Fingerabdruck)", font=("Segoe UI", 11, "bold"), bg="#f8fafc", fg="#0f172a").pack(anchor="w")
        tk.Label(hdr_left, text="Beide Dateien besitzen denselben Inhalt. Prüfen Sie die Live-Vorschau und entscheiden Sie, welche Datei Original bleibt.", font=("Segoe UI", 9), bg="#f8fafc", fg="#64748b").pack(anchor="w", pady=(2, 0))

        nav_box = tk.Frame(top_hdr, bg="#f8fafc")
        dlg._sibling_label = tk.Label(nav_box, text="", font=("Segoe UI", 9, "bold"), bg="#f8fafc", fg="#334155")
        dlg._btn_prev_dup = None
        dlg._btn_next_dup = None
        if len(sibling_rids) > 1:
            nav_box.pack(side="right", padx=(12, 0))
            dlg._btn_prev_dup = ModernRoundedButton(nav_box, "◀", command=lambda: select_sibling(state["pos"] - 1), bg="#ffffff", fg="#334155", hover_bg="#f1f5f9", border_color="#cbd5e1", font=("Segoe UI", 9, "bold"), r=6, padx=8, pady=3)
            dlg._btn_prev_dup.pack(side="left", padx=(0, 6))
            dlg._sibling_label.pack(side="left", padx=4)
            dlg._btn_next_dup = ModernRoundedButton(nav_box, "▶", command=lambda: select_sibling(state["pos"] + 1), bg="#ffffff", fg="#334155", hover_bg="#f1f5f9", border_color="#cbd5e1", font=("Segoe UI", 9, "bold"), r=6, padx=8, pady=3)
            dlg._btn_next_dup.pack(side="left", padx=(6, 0))

        def _populate_card_preview(stage_frame: tk.Frame, target_item: dict, badge_lbl: tk.Label):
            for child in stage_frame.winfo_children():
                child.destroy()
            if target_item.get("is_cloud_only"):
                info = {"kind": "cloud", "badge": "☁️ Cloud", "image": None, "text": ""}
            else:
                info = extract_file_preview_data(
                    Path(target_item["src_path"]),
                    is_dir=bool(target_item.get("is_dir")),
                    max_img_size=(340, 175),
                )
            badge_txt = info.get("badge", "")
            if badge_txt:
                badge_lbl.config(text=badge_txt)
                badge_lbl.pack(side="right")
            else:
                badge_lbl.pack_forget()

            kind = info.get("kind")
            if kind == "image" and info.get("image") is not None:
                photo = ImageTk.PhotoImage(info["image"])
                dlg._preview_photos.append(photo)
                lbl_img = tk.Label(stage_frame, image=photo, bg="#f8fafc", bd=0)
                lbl_img.image = photo
                lbl_img.pack(fill="both", expand=True, padx=4, pady=4)
            elif kind in ("text", "dir"):
                txt_wrap = tk.Frame(stage_frame, bg="#ffffff")
                txt_wrap.pack(fill="both", expand=True)
                txt = tk.Text(
                    txt_wrap,
                    wrap="word",
                    font=("Consolas", 8),
                    bg="#ffffff",
                    fg="#1e293b",
                    relief="flat",
                    bd=0,
                    padx=8,
                    pady=6,
                )
                scr = ttk.Scrollbar(txt_wrap, orient="vertical", command=txt.yview)
                txt.configure(yscrollcommand=scr.set)
                txt.pack(side="left", fill="both", expand=True)
                scr.pack(side="right", fill="y")
                txt.insert("1.0", info.get("text", ""))
                txt.config(state="disabled")
            else:
                msg = (
                    "☁️ Reine Cloud-Datei\n(Kein automatischer Download für Vorschau)"
                    if kind == "cloud"
                    else info.get("text", "Keine direkte Vorschau verfügbar.")
                )
                tk.Label(stage_frame, text=msg, font=("Segoe UI", 9), bg="#f8fafc", fg="#475569", wraplength=340, justify="center").pack(fill="both", expand=True, padx=10, pady=10)
            return info

        content_box = tk.Frame(dlg, bg="#f8fafc", padx=16)
        content_box.pack(fill="both", expand=True)

        # Linke Box: Original
        box_orig = tk.Frame(content_box, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1", padx=14, pady=12)
        box_orig.pack(side="left", fill="both", expand=True, padx=(0, 8))

        orig_hdr = tk.Frame(box_orig, bg="#ffffff")
        orig_hdr.pack(fill="x", pady=(0, 4))
        tk.Label(orig_hdr, text="1. Original-Dokument (bleibt erhalten)", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#0f172a").pack(side="left")
        orig_badge = tk.Label(orig_hdr, text="", font=("Segoe UI", 8, "bold"), bg="#eff6ff", fg="#1d4ed8", padx=7, pady=2)

        orig_preview_stage = tk.Frame(box_orig, bg="#f8fafc", height=188, highlightthickness=1, highlightbackground="#e2e8f0")
        orig_preview_stage.pack(fill="x", pady=(4, 8))
        orig_preview_stage.pack_propagate(False)
        dlg._orig_preview_stage = orig_preview_stage
        dlg._orig_preview_info = _populate_card_preview(orig_preview_stage, orig_item, orig_badge)

        tk.Label(box_orig, text=f"📄 {orig_item['name']}", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#1e40af", wraplength=390, justify="left").pack(anchor="w", pady=(0, 4))
        tk.Label(box_orig, text=f"• Quellpfad:\n  {orig_item['source_path']}", font=("Segoe UI", 9), bg="#ffffff", fg="#334155", wraplength=390, justify="left").pack(anchor="w", pady=1)
        tk.Label(box_orig, text=f"• Größe / Datum: {orig_item['size_str']}  ·  {orig_item['date']}", font=("Segoe UI", 9), bg="#ffffff", fg="#334155").pack(anchor="w", pady=1)
        tk.Label(box_orig, text=f"• Zielordner: {orig_item['target_sub']}", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#059669", wraplength=390, justify="left").pack(anchor="w", pady=(3, 0))

        orig_btn_frame = tk.Frame(box_orig, bg="#ffffff")
        orig_btn_frame.pack(anchor="w", pady=(10, 0))
        ModernRoundedButton(orig_btn_frame, "📄 Standard-App", command=lambda: self.open_file_directly(orig_item["src_path"]), bg="#e0f2fe", fg="#0369a1", hover_bg="#bae6fd", border_color="#7dd3fc", font=("Segoe UI", 9, "bold"), r=6, padx=10, pady=4).pack(side="left", padx=(0, 6))
        ModernRoundedButton(orig_btn_frame, "📂 Im Explorer", command=lambda: self.open_file_folder(orig_item["src_path"]), bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"), r=6, padx=10, pady=4).pack(side="left")

        # Rechte Box: Duplikat (aktualisierbar für Geschwister-Navigation)
        box_dup = tk.Frame(content_box, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1", padx=14, pady=12)
        box_dup.pack(side="right", fill="both", expand=True, padx=(8, 0))

        dup_hdr = tk.Frame(box_dup, bg="#ffffff")
        dup_hdr.pack(fill="x", pady=(0, 4))
        tk.Label(dup_hdr, text="2. Erkanntes Duplikat", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#0f172a").pack(side="left")
        dup_badge = tk.Label(dup_hdr, text="", font=("Segoe UI", 8, "bold"), bg="#fff7ed", fg="#c2410c", padx=7, pady=2)

        dup_preview_stage = tk.Frame(box_dup, bg="#f8fafc", height=188, highlightthickness=1, highlightbackground="#e2e8f0")
        dup_preview_stage.pack(fill="x", pady=(4, 8))
        dup_preview_stage.pack_propagate(False)
        dlg._dup_preview_stage = dup_preview_stage

        lbl_dup_name = tk.Label(box_dup, text="", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#ea580c", wraplength=390, justify="left")
        lbl_dup_name.pack(anchor="w", pady=(0, 4))
        lbl_dup_path = tk.Label(box_dup, text="", font=("Segoe UI", 9), bg="#ffffff", fg="#334155", wraplength=390, justify="left")
        lbl_dup_path.pack(anchor="w", pady=1)
        lbl_dup_meta = tk.Label(box_dup, text="", font=("Segoe UI", 9), bg="#ffffff", fg="#334155")
        lbl_dup_meta.pack(anchor="w", pady=1)
        lbl_dup_target = tk.Label(box_dup, text="", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#059669", wraplength=390, justify="left")
        lbl_dup_target.pack(anchor="w", pady=(3, 0))
        lbl_dup_status = tk.Label(box_dup, text="", font=("Segoe UI", 9, "italic"), bg="#ffffff", fg="#475569")
        lbl_dup_status.pack(anchor="w", pady=(2, 0))

        dup_btn_frame = tk.Frame(box_dup, bg="#ffffff")
        dup_btn_frame.pack(anchor="w", pady=(10, 0))
        ModernRoundedButton(dup_btn_frame, "📄 Standard-App", command=lambda: self.open_file_directly(state["dup_item"]["src_path"]), bg="#e0f2fe", fg="#0369a1", hover_bg="#bae6fd", border_color="#7dd3fc", font=("Segoe UI", 9, "bold"), r=6, padx=10, pady=4).pack(side="left", padx=(0, 6))
        ModernRoundedButton(dup_btn_frame, "📂 Im Explorer", command=lambda: self.open_file_folder(state["dup_item"]["src_path"]), bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"), r=6, padx=10, pady=4).pack(side="left")

        def select_sibling(new_idx: int):
            if not sibling_rids:
                return
            pos = new_idx % len(sibling_rids)
            state["pos"] = pos
            cur_rid = sibling_rids[pos]
            cur_it = self.preview_items[cur_rid]
            state["dup_rid"] = cur_rid
            state["dup_item"] = cur_it
            dlg._current_dup_rid = cur_rid
            if len(sibling_rids) > 1:
                dlg._sibling_label.config(text=f"Duplikat {pos + 1} von {len(sibling_rids)}")
            dlg._dup_preview_info = _populate_card_preview(dup_preview_stage, cur_it, dup_badge)
            lbl_dup_name.config(text=f"📄 {cur_it['name']}")
            lbl_dup_path.config(text=f"• Quellpfad:\n  {cur_it['source_path']}")
            lbl_dup_meta.config(text=f"• Größe / Datum: {cur_it['size_str']}  ·  {cur_it['date']}")
            lbl_dup_target.config(text=f"• Zielordner (wie Original): {cur_it['target_sub']}")
            lbl_dup_status.config(text=f"• Aktueller Status: {cur_it['action']}")

        dlg._select_sibling = select_sibling
        select_sibling(0)

        # Buttons unter Dialog
        btn_bar = tk.Frame(dlg, bg="#f8fafc", padx=16, pady=14)
        btn_bar.pack(fill="x")

        def on_swap_roles():
            target_dup_rid = state["dup_rid"]
            if target_dup_rid:
                self.swap_roles_for_item(target_dup_rid)
                dlg.destroy()
                self.open_compare_dialog(target_dup_rid)

        def on_dlg_copy():
            if state["dup_rid"]:
                self.set_duplicate_action(state["dup_rid"], "Mitkopieren")
            dlg.destroy()

        def on_dlg_skip():
            if state["dup_rid"]:
                self.set_duplicate_action(state["dup_rid"], "Überspringen")
            dlg.destroy()

        def on_dlg_del():
            target_dup_rid = state["dup_rid"]
            dlg.destroy()
            if target_dup_rid:
                self.delete_single_file(target_dup_rid)

        ModernRoundedButton(btn_bar, "⇄ Rollen tauschen (Dieses als Original behalten)", command=on_swap_roles, bg="#eff6ff", fg="#1d4ed8", hover_bg="#dbeafe", border_color="#93c5fd", font=("Segoe UI", 9, "bold"), r=8, padx=12, pady=6).pack(side="left", padx=(0, 8))
        ModernRoundedButton(btn_bar, "📋 Mitkopieren", command=on_dlg_copy, bg="#059669", fg="#ffffff", hover_bg="#047857", font=("Segoe UI", 9, "bold"), r=8, padx=12, pady=6).pack(side="left", padx=(0, 6))
        ModernRoundedButton(btn_bar, "⛔ Überspringen", command=on_dlg_skip, bg="#ea580c", fg="#ffffff", hover_bg="#c2410c", font=("Segoe UI", 9, "bold"), r=8, padx=12, pady=6).pack(side="left", padx=(0, 6))
        ModernRoundedButton(btn_bar, "🗑 In Papierkorb löschen", command=on_dlg_del, bg="#dc2626", fg="#ffffff", hover_bg="#b91c1c", font=("Segoe UI", 9, "bold"), r=8, padx=12, pady=6).pack(side="left", padx=(0, 6))
        ModernRoundedButton(btn_bar, "Schließen", command=dlg.destroy, bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"), r=8, padx=12, pady=6).pack(side="right")

    def open_selected_file(self):
        """Oeffnet die in der Baum-Tabelle markierte(n) Datei(en) in der Windows-Standard-App."""
        selected = self.tree.selection()
        if not selected:
            self.show_notice("Auswahl erforderlich", "Bitte wählen Sie zuerst eine Zeile in der Tabelle aus.", kind="info")
            return

        if len(selected) > 5:
            confirmed = self.confirm_action(
                title="Mehrere Dateien öffnen",
                headline=f"Möchten Sie alle {len(selected)} ausgewählten Dateien gleichzeitig öffnen?",
                subline="Jede Datei wird dabei in ihrer Windows-Standard-App geöffnet.",
                confirm_text="Alle öffnen",
            )
            if not confirmed:
                return

        for rid in selected:
            item = self.preview_items.get(rid)
            if item and item.get("src_path"):
                self.open_file_directly(item["src_path"])

    def open_file_folder(self, path: Path):
        """Oeffnet den Windows Explorer mit markierter Datei."""
        if os.name != 'nt':
            return
        try:
            abs_path = os.path.normpath(os.path.abspath(str(path)))
            if not os.path.exists(abs_path):
                self.show_notice("Datei nicht gefunden", "Der gewählte Pfad existiert nicht mehr.", str(abs_path), kind="warning")
                return

            self._watch_fs_after_explorer = True

            # Native Windows-Shell-API umgeht explorer.exe-Quoting-Bugs bei Leerzeichen, Kommas und Sonderzeichen
            try:
                import ctypes
                ole32 = ctypes.windll.ole32
                shell32 = ctypes.windll.shell32
                ole32.CoInitialize(None)
                shell32.ILCreateFromPathW.argtypes = [ctypes.c_wchar_p]
                shell32.ILCreateFromPathW.restype = ctypes.c_void_p
                shell32.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong]
                shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long
                shell32.ILFree.argtypes = [ctypes.c_void_p]

                pidl = shell32.ILCreateFromPathW(abs_path)
                if pidl:
                    try:
                        if shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0) == 0:
                            return
                    finally:
                        shell32.ILFree(pidl)
            except Exception:
                pass

            # Fallback: Schalter /select, ausserhalb der Anfuehrungszeichen halten
            subprocess.Popen(f'explorer.exe /select,"{abs_path}"')
        except Exception as e:
            self.show_notice("Fehler", "Der Explorer konnte nicht geöffnet werden.", str(e), kind="error")

    def open_file_directly(self, path: Path):
        """Oeffnet eine Datei direkt in der jeweiligen Windows-Standard-App."""
        if os.name == 'nt' and path.exists():
            try:
                self._watch_fs_after_explorer = True
                os.startfile(os.path.abspath(str(path)))
            except Exception as e:
                self.show_notice("Fehler beim Öffnen", "Die Datei konnte nicht geöffnet werden.", str(e), kind="error")

    # -------------------------------------------------------------
    # SCHRITT 3: Ergebnis & Abschluss
    # -------------------------------------------------------------
    def build_step3(self):
        title = tk.Label(
            self.frame_step3,
            text="Sortierung abgeschlossen",
            font=("Segoe UI", 16, "bold"),
            bg="#f8fafc",
            fg="#059669",
        )
        title.pack(anchor="w", pady=(0, 4))

        self.lbl_step3_subtitle = tk.Label(
            self.frame_step3,
            text="Alle ausgewählten Elemente wurden geordnet in das 7-Ordner-System übernommen.",
            font=("Segoe UI", 10),
            bg="#f8fafc",
            fg="#475569",
        )
        self.lbl_step3_subtitle.pack(anchor="w", pady=(0, 14))

        card_res = tk.Frame(
            self.frame_step3,
            bg="#ffffff",
            highlightbackground="#cbd5e1",
            highlightthickness=1,
            padx=20,
            pady=18,
        )
        card_res.pack(fill="x", pady=(0, 14))

        self.lbl_result = tk.Label(
            card_res,
            text="",
            font=("Segoe UI", 11, "bold"),
            bg="#ffffff",
            fg="#0f172a",
            justify="left",
            anchor="w",
        )
        self.lbl_result.pack(fill="x", pady=(0, 12))

        tk.Frame(card_res, bg="#e2e8f0", height=1).pack(fill="x", pady=(0, 12))

        src_row = tk.Frame(card_res, bg="#ffffff")
        src_row.pack(fill="x", pady=(0, 6))
        tk.Label(src_row, text="Quellordner:", width=12, anchor="w", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155").pack(side="left", padx=(0, 8))
        self.lbl_step3_src = tk.Label(src_row, text="", anchor="w", font=("Segoe UI", 9), bg="#ffffff", fg="#475569")
        self.lbl_step3_src.pack(side="left", fill="x", expand=True)

        dest_row = tk.Frame(card_res, bg="#ffffff")
        dest_row.pack(fill="x", pady=(0, 12))
        tk.Label(dest_row, text="Zielordner:", width=12, anchor="w", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155").pack(side="left", padx=(0, 8))
        self.lbl_step3_dest = tk.Label(dest_row, text="", anchor="w", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#0f172a")
        self.lbl_step3_dest.pack(side="left", fill="x", expand=True)

        self.lbl_step3_hint = tk.Label(
            card_res,
            text="",
            font=("Segoe UI", 9),
            bg="#f0f9ff",
            fg="#0369a1",
            justify="left",
            anchor="w",
            padx=12,
            pady=8,
        )
        self.lbl_step3_hint.pack(fill="x")

        self.step3_btn_box = tk.Frame(self.frame_step3, bg="#f8fafc")
        self.step3_btn_box.pack(fill="x", pady=(8, 0))
        self.step3_btn_box.columnconfigure(0, weight=1, uniform="s3side")
        self.step3_btn_box.columnconfigure(1, weight=0)
        self.step3_btn_box.columnconfigure(2, weight=1, uniform="s3side")

        step3_nav_h = tkfont.Font(font=("Segoe UI", 9, "bold")).metrics("linespace") * 2 + 5 * 2 + 4

        self.step3_left = tk.Frame(self.step3_btn_box, bg="#f8fafc")
        self.step3_left.grid(row=0, column=0, sticky="w")

        self.btn_step3_back_to_table = ModernRoundedButton(
            self.step3_left,
            "⬅ Zurück zur Vorschau",
            command=self.show_step2,
            bg="#eff6ff",
            fg="#1d4ed8",
            hover_bg="#dbeafe",
            border_color="#93c5fd",
            font=("Segoe UI", 9, "bold"),
            r=10,
            padx=16,
            pady=8,
            min_height=step3_nav_h,
        )
        self.btn_step3_back_to_table.pack(side="left", padx=(0, 10))

        self.btn_step3_undo = ModernRoundedButton(
            self.step3_left,
            "↺ Letzte Aktion rückgängig",
            command=self.revert_last_transaction,
            bg="#eff6ff",
            fg="#1d4ed8",
            hover_bg="#dbeafe",
            border_color="#93c5fd",
            font=("Segoe UI", 9, "bold"),
            r=10,
            padx=16,
            pady=8,
            min_height=step3_nav_h,
        )
        self.btn_step3_undo.pack(side="left")

        self.btn_step3_again = ModernRoundedButton(
            self.step3_btn_box,
            "⬅ Zurück zur Ordnerauswahl",
            command=self.show_step1,
            bg="#059669",
            fg="#ffffff",
            hover_bg="#047857",
            border_color=None,
            font=("Segoe UI", 10, "bold"),
            r=10,
            padx=22,
            pady=8,
            min_height=step3_nav_h,
        )
        self.btn_step3_again.grid(row=0, column=1, padx=14)

        self.step3_right = tk.Frame(self.step3_btn_box, bg="#f8fafc")
        self.step3_right.grid(row=0, column=2, sticky="e")

        self.btn_step3_search_app = ModernRoundedButton(
            self.step3_right,
            "🔍 In Datei-Suchmaschine öffnen",
            command=lambda: self.open_in_suchmaschine(getattr(self, "_last_explorer_target", "") or self.target_dir.get()),
            bg="#0f766e",
            fg="#ffffff",
            hover_bg="#115e59",
            font=("Segoe UI", 9, "bold"),
            r=10,
            padx=16,
            pady=8,
            min_height=step3_nav_h,
        )
        self.btn_step3_search_app.pack(side="left", padx=(0, 10))
        attach_tooltip(self.btn_step3_search_app, "Zielordner als Favorit speichern & direkt in Datei-Suchmaschine Pro öffnen (startet bei Bedarf automatisch)")

        self.btn_step3_open = ModernRoundedButton(
            self.step3_right,
            "Zielordner im Explorer öffnen",
            command=self.open_target_explorer,
            bg="#2563eb",
            fg="#ffffff",
            hover_bg="#1d4ed8",
            font=("Segoe UI", 10, "bold"),
            r=10,
            padx=18,
            pady=10,
            min_height=step3_nav_h,
        )
        self.btn_step3_open.pack(side="left")

    # -------------------------------------------------------------
    # Logik & Navigation
    # -------------------------------------------------------------
    def show_step1(self):
        self._close_active_popup_menu()
        self.frame_step2.pack_forget()
        self.frame_step3.pack_forget()
        self.frame_step1.pack(fill="both", expand=True)

    def show_step2(self):
        self.frame_step1.pack_forget()
        self.frame_step3.pack_forget()
        self.frame_step2.pack(fill="both", expand=True)

    def show_step3(self):
        self._close_active_popup_menu()
        self.frame_step1.pack_forget()
        self.frame_step2.pack_forget()
        self.frame_step3.pack(fill="both", expand=True)

    def set_target_adjacent(self):
        self.target_mode = "adjacent"
        src = self.source_dir.get().strip()
        if src:
            p = Path(src)
            self.target_dir.set(str(p.parent / f"{p.name}_01"))
        self.update_target_mode_ui()
        self._save_app_state()

    def set_target_subfolder(self):
        self.target_mode = "subfolder"
        src = self.source_dir.get().strip()
        if src:
            p = Path(src)
            self.target_dir.set(str(p / f"{p.name}_01"))
        self.update_target_mode_ui()
        self._save_app_state()

    def set_target_same(self):
        self.target_mode = "same"
        src = self.source_dir.get().strip()
        if src:
            self.target_dir.set(os.path.normpath(src))
        self.update_target_mode_ui()
        self._save_app_state()

    def set_target_usb(self):
        usb_dir = github_sync.find_usb_stick_data_dir()
        if not usb_dir:
            self.show_notice(
                "USB_Stick nicht gefunden",
                "Kein eingesteckter USB-Stick mit Ordner 'USB_Stick' gefunden (E:, F:, G: ...).",
                kind="warning",
                auto_close_ms=4000,
            )
            return
        self.target_mode = "usb"
        self.target_dir.set(usb_dir)
        self.update_target_mode_ui()
        self.add_recent_target_folder(usb_dir, label="USB_Stick")
        self._save_app_state()

    def set_target_onedrive(self):
        roots = get_onedrive_roots()
        accounts = get_onedrive_accounts()
        od_path = None
        if accounts.get("personal"):
            od_path = accounts["personal"][0][1]
        elif roots:
            od_path = roots[0]
        elif accounts.get("business"):
            od_path = accounts["business"][0][1]

        if not od_path or not Path(od_path).exists():
            self.show_notice(
                "OneDrive nicht gefunden",
                "Auf diesem PC wurde kein aktiver Microsoft OneDrive-Ordner gefunden.",
                kind="warning",
                auto_close_ms=4000,
            )
            return

        self.target_mode = "onedrive"
        self.target_dir.set(str(od_path))
        self.update_target_mode_ui()
        self._save_app_state()

    def sync_target_mode_ui(self):
        src = self.source_dir.get().strip()
        dest = self.target_dir.get().strip()
        if dest and os.path.basename(os.path.normpath(dest)).lower() == "usb_stick":
            self.target_mode = "usb"
            self.update_target_mode_ui()
            return
        if dest:
            dest_norm = os.path.normpath(dest).lower()
            roots = get_onedrive_roots()
            if any(dest_norm == os.path.normpath(str(r)).lower() for r in roots):
                self.target_mode = "onedrive"
                self.update_target_mode_ui()
                return
        if src and dest:
            try:
                p_src = Path(src)
                adj = str(p_src.parent / f"{p_src.name}_01")
                sub = str(p_src / f"{p_src.name}_01")
                dest_norm = os.path.normpath(dest).lower()
                if dest_norm == os.path.normpath(adj).lower():
                    self.target_mode = "adjacent"
                elif dest_norm == os.path.normpath(sub).lower():
                    self.target_mode = "subfolder"
                elif dest_norm == os.path.normpath(src).lower():
                    self.target_mode = "same"
                else:
                    self.target_mode = "custom"
            except Exception:
                pass
        self.update_target_mode_ui()

    def update_target_mode_ui(self):
        if not hasattr(self, 'btn_target_adjacent') or not hasattr(self, 'btn_target_subfolder'):
            return
        neutral_kw = dict(bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"))
        active_kw = dict(bg="#2563eb", fg="#ffffff", hover_bg="#1d4ed8", border_color=None, font=("Segoe UI", 9, "bold"))

        buttons = {
            "adjacent": self.btn_target_adjacent,
            "subfolder": self.btn_target_subfolder,
        }
        if hasattr(self, "btn_target_same"):
            buttons["same"] = self.btn_target_same
        if hasattr(self, "btn_target_usb"):
            buttons["usb"] = self.btn_target_usb
        if hasattr(self, "btn_target_onedrive"):
            buttons["onedrive"] = self.btn_target_onedrive

        for key, btn in buttons.items():
            btn.update_style(**(active_kw if self.target_mode == key else neutral_kw))

    def toggle_file_filter(self, category: str):
        if category == "all":
            self.filter_all_var.set(True)
            self.filter_images_var.set(False)
            self.filter_text_docs_var.set(False)
            self.filter_office_var.set(False)
            self.filter_media_var.set(False)
            self.filter_installers_var.set(False)
        else:
            var_map = {
                "images": self.filter_images_var,
                "text_docs": self.filter_text_docs_var,
                "office": self.filter_office_var,
                "media": self.filter_media_var,
                "installers": self.filter_installers_var,
            }
            if category in var_map:
                cur = var_map[category].get()
                var_map[category].set(not cur)

                # Wenn mindestens ein spezifischer Filter aktiv ist, "all" ausschalten
                if any(v.get() for v in var_map.values()):
                    self.filter_all_var.set(False)
                else:
                    # Wenn alle abgewählt wurden, "all" automatisch wieder einschalten
                    self.filter_all_var.set(True)

        # Legacy-Variablen für Abwärtskompatibilität synchronisieren
        if self.filter_all_var.get():
            self.file_filter_mode.set("all")
            self.include_installers_var.set(False)
            self.include_media_var.set(False)
        else:
            self.include_installers_var.set(self.filter_installers_var.get())
            self.include_media_var.set(self.filter_media_var.get())
            if self.filter_images_var.get() and not (self.filter_text_docs_var.get() or self.filter_office_var.get() or self.filter_media_var.get() or self.filter_installers_var.get()):
                self.file_filter_mode.set("only_images")
            else:
                self.file_filter_mode.set("custom")

        self.update_file_filter_ui()
        self._save_app_state()

    def set_file_filter_mode(self, mode: str):
        self.toggle_file_filter(mode)

    def update_file_filter_ui(self):
        if not hasattr(self, "filter_buttons"):
            return
        neutral_kw = dict(bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"))
        active_kw = dict(bg="#2563eb", fg="#ffffff", hover_bg="#1d4ed8", border_color=None, font=("Segoe UI", 9, "bold"))

        is_all = self.filter_all_var.get()
        if "all" in self.filter_buttons:
            self.filter_buttons["all"].update_style(**(active_kw if is_all else neutral_kw))

        var_map = {
            "images": self.filter_images_var,
            "text_docs": self.filter_text_docs_var,
            "office": self.filter_office_var,
            "media": self.filter_media_var,
            "installers": self.filter_installers_var,
        }
        for k, var in var_map.items():
            if k in self.filter_buttons:
                is_act = (not is_all) and var.get()
                self.filter_buttons[k].update_style(**(active_kw if is_act else neutral_kw))

    def set_subfolder_mode(self, mode: str):
        if mode not in ("split", "whole", "ignore"):
            mode = "split"
        self.subfolder_mode.set(mode)
        self.recursive_var.set(mode == "split")
        self.update_subfolder_mode_ui()
        self._save_app_state()

    def update_subfolder_mode_ui(self):
        if not hasattr(self, "subfolder_buttons"):
            return
        active_mode = self.subfolder_mode.get()
        neutral_kw = dict(bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"))
        active_kw = dict(bg="#2563eb", fg="#ffffff", hover_bg="#1d4ed8", border_color=None, font=("Segoe UI", 9, "bold"))
        for mode_key, btn in self.subfolder_buttons.items():
            btn.update_style(**(active_kw if mode_key == active_mode else neutral_kw))

    def toggle_include_installers(self):
        self.include_installers_var.set(not self.include_installers_var.get())
        self.update_extra_type_ui()
        self._save_app_state()

    def toggle_include_media(self):
        self.include_media_var.set(not self.include_media_var.get())
        self.update_extra_type_ui()
        self._save_app_state()

    def toggle_include_onedrive(self):
        self.include_onedrive_var.set(not self.include_onedrive_var.get())
        self.update_extra_type_ui()
        self._save_app_state()

    def _request_it_authorization(self, guard_label: str) -> bool:
        """Fragt vor dem Deaktivieren geschützter Sicherheitsfunktionen nach dem IT-Passwort."""
        dlg = AdminPasswordDialog(self, guard_label=guard_label)
        self.wait_window(dlg)
        return bool(dlg.result)

    def open_change_admin_password_dialog(self):
        """Öffnet den vorbereiteten Dialog zur Änderung des IT-Passworts."""
        dlg = ChangeAdminPasswordDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            self.show_notice(
                "IT-Passwort gespeichert",
                "Das neue IT-Passwort für NetzwerkSchutz & Systemschutz ist ab sofort aktiv.",
                kind="success",
            )

    def toggle_exclude_system_dirs(self, force_prompt: bool = True):
        if self.exclude_system_dirs_var.get() and force_prompt:
            if not self._request_it_authorization("Systemschutz"):
                return
        self.exclude_system_dirs_var.set(not self.exclude_system_dirs_var.get())
        self.update_extra_type_ui()
        self._save_app_state()

    def toggle_network_guard(self, force_prompt: bool = True):
        if self.network_guard_var.get() and force_prompt:
            if not self._request_it_authorization("NetzwerkSchutz"):
                return
        self.network_guard_var.set(not self.network_guard_var.get())
        self.update_extra_type_ui()
        self._save_app_state()

    def update_extra_type_ui(self):
        neutral_kw = dict(bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"))
        active_kw = dict(bg="#2563eb", fg="#ffffff", hover_bg="#1d4ed8", border_color=None, font=("Segoe UI", 9, "bold"))
        is_all = bool(self.filter_all_var.get()) if hasattr(self, "filter_all_var") else False
        if hasattr(self, "btn_extra_installers"):
            is_act = (not is_all) and bool(self.filter_installers_var.get() if hasattr(self, "filter_installers_var") else self.include_installers_var.get())
            self.btn_extra_installers.update_style(**(active_kw if is_act else neutral_kw))
        if hasattr(self, "btn_extra_media"):
            is_act = (not is_all) and bool(self.filter_media_var.get() if hasattr(self, "filter_media_var") else self.include_media_var.get())
            self.btn_extra_media.update_style(**(active_kw if is_act else neutral_kw))
        if hasattr(self, "btn_extra_onedrive"):
            self.btn_extra_onedrive.update_style(**(active_kw if self.include_onedrive_var.get() else neutral_kw))
        if hasattr(self, "btn_extra_sysguard"):
            self.btn_extra_sysguard.update_style(**(active_kw if self.exclude_system_dirs_var.get() else neutral_kw))
        if hasattr(self, "btn_extra_firmguard"):
            self.btn_extra_firmguard.update_style(**(active_kw if self.network_guard_var.get() else neutral_kw))

    @staticmethod
    def _compute_date_preset_range(preset_key: str, today: datetime.date | None = None):
        ref = today or datetime.date.today()
        if preset_key == "today":
            return ref, ref
        if preset_key == "week":
            start = ref - datetime.timedelta(days=ref.weekday())
            return start, start + datetime.timedelta(days=6)
        if preset_key == "month":
            last_day = calendar.monthrange(ref.year, ref.month)[1]
            return ref.replace(day=1), ref.replace(day=last_day)
        if preset_key == "year":
            return datetime.date(ref.year, 1, 1), datetime.date(ref.year, 12, 31)
        return None, None

    def set_date_preset(self, preset_key: str):
        d_start, d_end = self._compute_date_preset_range(preset_key)
        if d_start is None or d_end is None:
            return
        s_from = d_start.strftime("%d.%m.%Y")
        s_to = d_end.strftime("%d.%m.%Y")
        if self.date_from_var.get().strip() == s_from and self.date_to_var.get().strip() == s_to:
            self.date_from_var.set("")
            self.date_to_var.set("")
        else:
            self.date_from_var.set(s_from)
            self.date_to_var.set(s_to)
        self.update_date_preset_ui()
        self._save_app_state()

    def update_date_preset_ui(self):
        if not hasattr(self, "date_preset_buttons"):
            return
        cur_from = self.date_from_var.get().strip()
        cur_to = self.date_to_var.get().strip()
        parsed_from = self._parse_filter_date(cur_from, is_end=False)
        parsed_to = self._parse_filter_date(cur_to, is_end=True)

        neutral_kw = dict(bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"))
        active_kw = dict(bg="#2563eb", fg="#ffffff", hover_bg="#1d4ed8", border_color=None, font=("Segoe UI", 9, "bold"))
        for key, btn in self.date_preset_buttons.items():
            d1, d2 = self._compute_date_preset_range(key)
            is_match = (
                parsed_from is not None
                and parsed_to is not None
                and parsed_from.date() == d1
                and parsed_to.date() == d2
            )
            btn.update_style(**(active_kw if is_match else neutral_kw))

    def open_date_picker(self, anchor_widget, target_var: tk.StringVar):
        if getattr(self, "_calendar_popup", None) is not None:
            try:
                self._calendar_popup.close()
            except Exception:
                pass
            self._calendar_popup = None

        def _after_pick(_day):
            self.update_date_preset_ui()
            self._save_app_state()

        self._calendar_popup = CalendarPopup(anchor_widget, target_var, on_select=_after_pick)
        return self._calendar_popup

    def set_size_preset(self, size_label: str):
        if self.min_size_var.get().strip().upper() == size_label.upper():
            self.min_size_var.set("")
        else:
            self.min_size_var.set(size_label)
        self.update_size_preset_ui()
        self._save_app_state()

    def update_size_preset_ui(self):
        if not hasattr(self, "size_preset_buttons"):
            return
        try:
            cur_bytes = self._parse_min_file_size(self.min_size_var.get())
        except ValueError:
            cur_bytes = None
        neutral_kw = dict(bg="#ffffff", fg="#1e293b", hover_bg="#f1f5f9", border_color="#94a3b8", font=("Segoe UI", 9, "bold"))
        active_kw = dict(bg="#2563eb", fg="#ffffff", hover_bg="#1d4ed8", border_color=None, font=("Segoe UI", 9, "bold"))
        for key, btn in self.size_preset_buttons.items():
            target_b = self._parse_min_file_size(key)
            is_match = (cur_bytes is not None and target_b is not None and cur_bytes == target_b)
            btn.update_style(**(active_kw if is_match else neutral_kw))

    def clear_extra_filters(self):
        self.date_from_var.set("")
        self.date_to_var.set("")
        self.keyword_filter_var.set("")
        self.min_size_var.set("")
        self.update_date_preset_ui()
        self.update_size_preset_ui()
        self._save_app_state()

    @staticmethod
    def _parse_min_file_size(raw: str) -> int | None:
        text = str(raw or "").strip()
        if not text:
            return None
        m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*(b|byte|bytes|k|kb|m|mb|g|gb)?\s*$", text, re.IGNORECASE)
        if not m:
            raise ValueError(f"Ungültige Dateigröße '{text}'. Bitte z. B. 100 KB, 500 KB oder 2 MB eingeben.")
        num = float(m.group(1).replace(",", "."))
        unit = (m.group(2) or "kb").lower()
        if num <= 0:
            return None
        mult_map = {
            "b": 1, "byte": 1, "bytes": 1,
            "k": 1024, "kb": 1024,
            "m": 1024 * 1024, "mb": 1024 * 1024,
            "g": 1024 * 1024 * 1024, "gb": 1024 * 1024 * 1024,
        }
        return int(num * mult_map.get(unit, 1024))

    @staticmethod
    def _parse_filter_date(raw: str, is_end: bool = False):
        text = str(raw or "").strip()
        if not text:
            return None
        for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
            try:
                d = datetime.datetime.strptime(text, fmt)
                return d.replace(hour=23, minute=59, second=59, microsecond=999999) if is_end else d.replace(hour=0, minute=0, second=0, microsecond=0)
            except ValueError:
                pass
        for fmt in ("%m.%Y", "%Y-%m"):
            try:
                d = datetime.datetime.strptime(text, fmt)
                if is_end:
                    last_day = calendar.monthrange(d.year, d.month)[1]
                    return datetime.datetime(d.year, d.month, last_day, 23, 59, 59, 999999)
                return datetime.datetime(d.year, d.month, 1, 0, 0, 0, 0)
            except ValueError:
                pass
        if re.fullmatch(r"\d{4}", text):
            y = int(text)
            if 1900 <= y <= 2100:
                if is_end:
                    return datetime.datetime(y, 12, 31, 23, 59, 59, 999999)
                return datetime.datetime(y, 1, 1, 0, 0, 0, 0)
        raise ValueError(f"Ungültiges Datum '{text}'. Bitte z. B. TT.MM.JJJJ, MM.JJJJ oder JJJJ eingeben.")

    _WIN_INVALID_FOLDER_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
    _WIN_RESERVED_NAMES = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }

    @classmethod
    def _sanitize_subfolder_name(cls, raw_name: str) -> str:
        """Bereinigt einen Suchbegriff für einen sicheren Windows-Unterordnernamen."""
        clean = cls._WIN_INVALID_FOLDER_CHARS.sub("_", str(raw_name or "").strip())
        clean = re.sub(r"\s+", " ", clean).strip().rstrip(". ")
        if not clean or clean in (".", ".."):
            return ""
        if clean.upper() in cls._WIN_RESERVED_NAMES:
            clean = f"{clean}_Ordner"
        return clean

    @classmethod
    def _parse_keyword_terms(cls, raw: str) -> list[tuple[str, str]]:
        """Liefert je Suchbegriff das kleingeschriebene Suchwort sowie den bereinigten Ordnernamen."""
        parts = re.split(r"[,;]+", str(raw or ""))
        terms = []
        for part in parts:
            stripped = part.strip()
            if not stripped:
                continue
            terms.append((stripped.lower(), cls._sanitize_subfolder_name(stripped)))
        return terms

    @classmethod
    def _parse_keywords(cls, raw: str) -> list[str]:
        return [kw for kw, _ in cls._parse_keyword_terms(raw)]

    def _apply_keyword_image_subfolder(
        self,
        p: Path,
        src: Path,
        target_sub: str,
        keyword_terms: list[tuple[str, str]],
    ) -> str:
        """Leitet Bilder-&-Fotos-Einträge bei aktivem Suchfilter in 07_Archiv_Historie/04_Bilder_Fotos/<Suchfilter> um."""
        if target_sub != "07_Archiv_Historie/04_Bilder_Fotos" or not keyword_terms:
            return target_sub

        valid_terms = [(kw, safe) for kw, safe in keyword_terms if safe]
        if not valid_terms:
            return target_sub

        if len(valid_terms) == 1:
            return f"07_Archiv_Historie/04_Bilder_Fotos/{valid_terms[0][1]}"

        try:
            rel_str = str(p.relative_to(src)).lower()
        except Exception:
            rel_str = p.name.lower()

        for kw_low, safe_name in valid_terms:
            if kw_low in rel_str:
                return f"07_Archiv_Historie/04_Bilder_Fotos/{safe_name}"

        if p.is_dir():
            sample = []
            try:
                for root, dirs, files in os.walk(p):
                    dirs[:] = [d for d in dirs if d.lower() not in IGNORED_DIRS and not d.startswith(".")]
                    sample.extend(dirs)
                    sample.extend(files)
                    if len(sample) >= 80:
                        break
            except Exception:
                pass
            blob = " ".join(sample).lower()
            for kw_low, safe_name in valid_terms:
                if kw_low in blob:
                    return f"07_Archiv_Historie/04_Bilder_Fotos/{safe_name}"

        return f"07_Archiv_Historie/04_Bilder_Fotos/{valid_terms[0][1]}"

    def _matches_extra_filters(self, p: Path, src: Path, dt_from, dt_to, keywords: list[str], min_bytes: int | None = None) -> bool:
        if min_bytes is not None and not p.is_dir():
            try:
                if p.stat().st_size < min_bytes:
                    return False
            except Exception:
                return False

        if dt_from is not None or dt_to is not None:
            try:
                mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime)
            except Exception:
                return False
            if dt_from is not None and mtime < dt_from:
                return False
            if dt_to is not None and mtime > dt_to:
                return False

        if keywords:
            try:
                rel_str = str(p.relative_to(src)).lower()
            except Exception:
                rel_str = p.name.lower()
            if any(kw in rel_str for kw in keywords):
                return True
            if p.is_dir():
                try:
                    with os.scandir(p) as it:
                        for entry in it:
                            if any(kw in entry.name.lower() for kw in keywords):
                                return True
                except Exception:
                    pass
                return False
            # Cloud-Only-Dateien bei der Suche NIEMALS oeffnen (verhindert OneDrive-Download)
            if is_cloud_only_file(p):
                return False
            ext_low = p.suffix.lower()
            if ext_low in (".txt", ".csv", ".json", ".log", ".html", ".htm"):
                try:
                    txt = p.read_text(encoding="utf-8", errors="ignore")[:2000].lower()
                    return any(kw in txt for kw in keywords)
                except Exception:
                    return False
            if ext_low == ".pdf":
                txt = extract_pdf_keywords(p).lower()
                return any(kw in txt for kw in keywords)
            return False

        return True

    def auto_fit_columns(self):
        """Berechnet Spaltenbreiten passend zur aktuellen Fensterbreite, damit standardmäßig
        kein lästiges horizontales Scrollen nötig ist. Breite bleibt am Kopf manuell anpassbar."""
        f_item = tkfont.Font(font=("Segoe UI", 9))
        f_head = tkfont.Font(font=("Segoe UI", 9, "bold"))

        min_base_widths = {
            "file": 150,
            "ext": 46,
            "size": 74,
            "items_count": 70,
            "dup_status": 96,
            "category": 120,
            "target_folder": 130,
            "date": 112,
            "source_sub": 135,
            "action": 86
        }
        caps = {
            "file": 280,
            "ext": 68,
            "size": 105,
            "items_count": 100,
            "dup_status": 150,
            "category": 210,
            "target_folder": 240,
            "date": 130,
            "source_sub": 260,
            "action": 108
        }

        desired = {}
        rows = self.tree.get_children()
        for col in self.col_keys:
            head_text = self.col_titles.get(col, col)
            max_w = max(f_head.measure(head_text + " ▼") + 18, min_base_widths.get(col, 80))
            for row_id in rows[:250]:
                val = str(self.tree.set(row_id, col))
                if val:
                    w = f_item.measure(val) + 20
                    if w > max_w:
                        max_w = w
            cap = caps.get(col, 240)
            if max_w > cap:
                max_w = cap
            desired[col] = max_w

        # Verfuegbare Breite im Treeview ermitteln
        try:
            self.tree.update_idletasks()
            tree_w = self.tree.winfo_width()
            win_w = self.winfo_width()
        except Exception:
            tree_w, win_w = 0, 0
        avail_w = (tree_w - 4) if tree_w > 200 else max(980, win_w - 64)

        total_w = sum(desired.values())
        if total_w > avail_w:
            # Überschuss anteilig aus den breiteren Spalten herausnehmen, bis alles sauber ins Fenster passt
            overflow = total_w - avail_w
            flex_cols = ["source_sub", "target_folder", "file", "category", "dup_status"]
            slacks = {c: max(0, desired[c] - min_base_widths[c]) for c in flex_cols}
            total_slack = sum(slacks.values())
            if total_slack > 0:
                scale = min(1.0, overflow / total_slack)
                for c in flex_cols:
                    desired[c] -= int(slacks[c] * scale)
                # Eventuellen Rundungsrest auf source_sub / file anrechnen
                rem = sum(desired.values()) - avail_w
                for c in ("source_sub", "target_folder", "file"):
                    if rem <= 0:
                        break
                    cut = min(rem, max(0, desired[c] - min_base_widths[c]))
                    desired[c] -= cut
                    rem -= cut
        elif total_w < avail_w and avail_w - total_w > 8:
            # Freien Restraum sinnvoll auf Pfad, Zielordner und Dateiname verteilen
            extra = avail_w - total_w
            weights = {"file": 0.35, "target_folder": 0.25, "source_sub": 0.40}
            given = 0
            for c, wt in weights.items():
                add = int(extra * wt)
                desired[c] += add
                given += add
            desired["source_sub"] += max(0, extra - given)

        for col in self.col_keys:
            w = int(desired[col])
            self.col_widths[col] = w
            self.tree.column(col, width=w, minwidth=35, stretch=False)

    def check_write_permission(self, target_path: Path, create_if_missing: bool = False) -> tuple[bool, str]:
        """Prueft Schreibrechte am Zielverzeichnis (oder naechstgelegenen Elternordner), ohne leere Zielordner vorab anzulegen."""
        try:
            probe_dir = Path(target_path)
            if create_if_missing:
                probe_dir.mkdir(parents=True, exist_ok=True)
            else:
                while not probe_dir.exists() and probe_dir.parent != probe_dir:
                    probe_dir = probe_dir.parent
                if not probe_dir.exists() or not probe_dir.is_dir():
                    return False, f"Zielpfad oder übergeordneter Ordner existiert nicht:\n'{target_path}'"
            test_file = probe_dir / f".perm_check_{os.getpid()}_{datetime.datetime.now().strftime('%f')}.tmp"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
            return True, ""
        except PermissionError:
            return False, (
                f"Zugriff verweigert auf Zielverzeichnis:\n'{target_path}'\n\n"
                f"Ursache: Dieser Ordner ist durch Windows-Rechte geschützt.\n\n"
                f"Lösung:\n"
                f"• Nutzen Sie einen Ordner in Ihrem Benutzerbereich (z. B. 'C:\\Users\\01\\...').\n"
                f"• Oder starten Sie das Programm per Rechtsklick 'Als Administrator ausführen'."
            )
        except Exception as e:
            return False, f"Fehler beim Zugriff auf Zielverzeichnis:\n{e}"

    def browse_source(self):
        init_dir = self.source_dir.get().strip()
        if not init_dir or not os.path.exists(init_dir):
            init_dir = os.path.expanduser("~")
        init_dir = os.path.abspath(init_dir)
        folder = ask_folder_with_files(self, "Quellordner auswählen", initialdir=init_dir)
        if folder:
            folder_clean = os.path.normpath(folder)
            self.source_dir.set(folder_clean)
            p = Path(folder_clean)
            if is_onedrive_dir(p) and not self.include_onedrive_var.get():
                self.include_onedrive_var.set(True)
                self.update_extra_type_ui()
            if self.target_mode == "subfolder":
                self.target_dir.set(str(p / f"{p.name}_01"))
            elif self.target_mode == "same":
                self.target_dir.set(folder_clean)
            else:
                self.target_mode = "adjacent"
                self.target_dir.set(str(p.parent / f"{p.name}_01"))
            self.update_target_mode_ui()
            self._save_app_state()

    def browse_target(self):
        init_dir = self.target_dir.get().strip()
        if not init_dir or not os.path.exists(init_dir):
            src = self.source_dir.get().strip()
            init_dir = src if (src and os.path.exists(src)) else os.path.expanduser("~")
        init_dir = os.path.abspath(init_dir)
        folder = ask_folder_with_files(self, "Zielordner des 7-Ordner-Systems auswählen", initialdir=init_dir)
        if folder:
            p_norm = os.path.normpath(folder)
            self.target_dir.set(p_norm)
            self.target_mode = "custom"
            self.update_target_mode_ui()
            self.add_recent_target_folder(p_norm)
            self._save_app_state()

    def _is_allowed_file(
        self,
        p: Path,
        mode=None,
        inc_installers=None,
        inc_media=None,
        filter_all: bool | None = None,
        filter_images: bool | None = None,
        filter_text_docs: bool | None = None,
        filter_office: bool | None = None,
        filter_media: bool | None = None,
        filter_installers: bool | None = None,
    ) -> bool:
        """Prueft anhand des gewaehlten Dateifilters, ob eine Datei einbezogen wird."""
        name = p.name
        if name.startswith("~$") or name.startswith("."):
            return False
        if name.lower() in PROTECTED_APP_FILES:
            return False
        if getattr(sys, "frozen", False):
            try:
                if p.resolve() == Path(sys.executable).resolve():
                    return False
            except Exception:
                pass
        ext = p.suffix.lower()

        # Thread-sichere Ermittlung der Filter-Booleans (ohne Tkinter-Aufruf im Hintergrundthread)
        if filter_all is not None:
            f_all = bool(filter_all)
            f_img = bool(filter_images)
            f_txt = bool(filter_text_docs)
            f_off = bool(filter_office)
            f_med = bool(filter_media)
            f_inst = bool(filter_installers)
        else:
            try:
                f_all = bool(self.filter_all_var.get())
                f_img = bool(self.filter_images_var.get())
                f_txt = bool(self.filter_text_docs_var.get())
                f_off = bool(self.filter_office_var.get())
                f_med = bool(self.filter_media_var.get())
                f_inst = bool(self.filter_installers_var.get())
            except Exception:
                f_all = True
                f_img = f_txt = f_off = f_med = f_inst = False

        # Wenn "Alle Dateien" aktiv ist (Standard)
        if f_all:
            return ext in ALL_EXTENSIONS

        # Einzeln oder zusammen wählbare Dateifilter:
        if f_img and ext in IMAGE_EXTENSIONS:
            return True
        if f_txt and ext in TEXT_DOC_EXTENSIONS:
            return True
        if f_off and ext in OFFICE_EXTENSIONS:
            return True
        if f_med and ext in MEDIA_EXTENSIONS:
            return True
        if f_inst and ext in INSTALLER_EXTENSIONS:
            return True

        return False

    def _scan_source_files(
        self,
        src: Path,
        dest_base: Path,
        dt_from=None,
        dt_to=None,
        keywords=None,
        sub_mode=None,
        filter_mode=None,
        inc_installers=None,
        inc_media=None,
        inc_onedrive=None,
        exc_sys=None,
        min_bytes=None,
        filter_all=None,
        filter_images=None,
        filter_text_docs=None,
        filter_office=None,
        filter_media=None,
        filter_installers=None,
        cancel_event=None,
        progress_cb=None,
        from_ui: bool = True,
    ) -> list[Path]:
        """Sammelt alle gueltigen Quelldateien (und im Modus 'whole' ganze Unterordner) im Quellordner."""
        found_items = []
        if not src.exists():
            return found_items
        if from_ui:
            if dt_from is None and self.date_from_var.get().strip():
                try:
                    dt_from = self._parse_filter_date(self.date_from_var.get(), is_end=False)
                except ValueError:
                    dt_from = None
            if dt_to is None and self.date_to_var.get().strip():
                try:
                    dt_to = self._parse_filter_date(self.date_to_var.get(), is_end=True)
                except ValueError:
                    dt_to = None
            if keywords is None:
                keywords = self._parse_keywords(self.keyword_filter_var.get())
            if min_bytes is None and hasattr(self, "min_size_var") and self.min_size_var.get().strip():
                try:
                    min_bytes = self._parse_min_file_size(self.min_size_var.get())
                except ValueError:
                    min_bytes = None
            sub_mode = self.subfolder_mode.get() if sub_mode is None else sub_mode
            filter_mode = self.file_filter_mode.get() if filter_mode is None else filter_mode
            inc_installers = bool(self.include_installers_var.get()) if inc_installers is None else bool(inc_installers)
            inc_media = bool(self.include_media_var.get()) if inc_media is None else bool(inc_media)
            inc_onedrive = bool(self.include_onedrive_var.get()) if inc_onedrive is None else bool(inc_onedrive)
            exc_sys = bool(self.exclude_system_dirs_var.get()) if exc_sys is None else bool(exc_sys)
            f_all = bool(self.filter_all_var.get()) if filter_all is None else bool(filter_all)
            f_img = bool(self.filter_images_var.get()) if filter_images is None else bool(filter_images)
            f_txt = bool(self.filter_text_docs_var.get()) if filter_text_docs is None else bool(filter_text_docs)
            f_off = bool(self.filter_office_var.get()) if filter_office is None else bool(filter_office)
            f_med = bool(self.filter_media_var.get()) if filter_media is None else bool(filter_media)
            f_inst = bool(self.filter_installers_var.get()) if filter_installers is None else bool(filter_installers)
        else:
            if keywords is None:
                keywords = []
            sub_mode = "split" if sub_mode is None else sub_mode
            filter_mode = "all" if filter_mode is None else filter_mode
            inc_installers = False if inc_installers is None else bool(inc_installers)
            inc_media = False if inc_media is None else bool(inc_media)
            inc_onedrive = False if inc_onedrive is None else bool(inc_onedrive)
            exc_sys = True if exc_sys is None else bool(exc_sys)
            f_all = True if filter_all is None else bool(filter_all)
            f_img = False if filter_images is None else bool(filter_images)
            f_txt = False if filter_text_docs is None else bool(filter_text_docs)
            f_off = False if filter_office is None else bool(filter_office)
            f_med = False if filter_media is None else bool(filter_media)
            f_inst = False if filter_installers is None else bool(filter_installers)

        src_resolved = src.resolve()
        dest_resolved = dest_base.resolve()
        same_root = (dest_resolved == src_resolved)
        onedrive_roots = [] if inc_onedrive else get_onedrive_roots()

        def _is_cancelled() -> bool:
            return cancel_event is not None and cancel_event.is_set()

        def _report():
            if progress_cb is not None:
                progress_cb(len(found_items))

        def _skip_cloud_file(file_path: Path) -> bool:
            return (not inc_onedrive) and is_cloud_only_file(file_path)

        def _is_valid_top_dir(dir_path: Path) -> bool:
            name_low = dir_path.name.lower()
            if name_low in IGNORED_DIRS or dir_path.name.startswith(".") or dir_path.name.endswith("_Backup_"):
                return False
            if exc_sys and is_system_noise_dir(dir_path):
                return False
            if (not inc_onedrive) and is_onedrive_dir(dir_path, onedrive_roots):
                return False
            try:
                d_res = dir_path.resolve()
            except Exception:
                return False
            if same_root:
                if d_res.parent == dest_resolved and name_low in SEVEN_SYSTEM_ROOTS:
                    return False
            elif d_res == dest_resolved or dest_resolved in d_res.parents:
                return False
            if is_7_folder_archive(d_res):
                return False
            return True

        if sub_mode == "split":
            for root, dirs, files in os.walk(src):
                if _is_cancelled():
                    return found_items
                valid_dirs = []
                for d in dirs:
                    if _is_cancelled():
                        return found_items
                    d_raw = Path(root) / d
                    if d.lower() in IGNORED_DIRS or d.startswith(".") or d.endswith("_Backup_"):
                        continue
                    if exc_sys and is_system_noise_dir(d_raw):
                        continue
                    if (not inc_onedrive) and is_onedrive_dir(d_raw, onedrive_roots):
                        continue
                    try:
                        d_path = d_raw.resolve()
                    except Exception:
                        continue
                    if same_root:
                        if d_path.parent == dest_resolved and d.lower() in SEVEN_SYSTEM_ROOTS:
                            continue
                    elif d_path == dest_resolved or dest_resolved in d_path.parents:
                        continue
                    if is_7_folder_archive(d_path):
                        continue
                    valid_dirs.append(d)
                dirs[:] = valid_dirs

                for f in files:
                    if _is_cancelled():
                        return found_items
                    p = Path(root) / f
                    if (
                        not _skip_cloud_file(p)
                        and self._is_allowed_file(
                            p,
                            filter_all=f_all,
                            filter_images=f_img,
                            filter_text_docs=f_txt,
                            filter_office=f_off,
                            filter_media=f_med,
                            filter_installers=f_inst,
                        )
                        and self._matches_extra_filters(p, src, dt_from, dt_to, keywords, min_bytes=min_bytes)
                    ):
                        found_items.append(p)
                        _report()
        elif sub_mode == "whole":
            for p in src.iterdir():
                if _is_cancelled():
                    return found_items
                if p.is_file():
                    if (
                        not _skip_cloud_file(p)
                        and self._is_allowed_file(
                            p,
                            filter_all=f_all,
                            filter_images=f_img,
                            filter_text_docs=f_txt,
                            filter_office=f_off,
                            filter_media=f_med,
                            filter_installers=f_inst,
                        )
                        and self._matches_extra_filters(p, src, dt_from, dt_to, keywords, min_bytes=min_bytes)
                    ):
                        found_items.append(p)
                        _report()
                elif p.is_dir() and not p.is_symlink() and _is_valid_top_dir(p):
                    # 1. ZUERST Stichwort-, Datums- & Groessenfilter pruefen (verhindert teure Ordner-Scans!)
                    if not self._matches_extra_filters(p, src, dt_from, dt_to, keywords, min_bytes=min_bytes):
                        continue

                    # 2. Wenn "Alle Dateien" aktiv ist: Ordner sofort uebernehmen
                    if f_all:
                        found_items.append(p)
                        _report()
                        continue

                    # 3. Wenn spezifische Typfilter aktiv sind (z. B. Videos & Audio):
                    cat_matched = False
                    p_name_low = p.name.lower()
                    if f_med and (
                        any(ext in p_name_low for ext in (".mkv", ".mp4", ".avi", ".ts", "bdrip", "720p", "1080p", "web", "dl", "german", "serie", "season", "staffel"))
                        or "09_videos" in str(p).lower()
                    ):
                        cat_matched = True

                    if not cat_matched:
                        target_cat = find_folder_target(p)[0]
                        if f_img and target_cat == "07_Archiv_Historie/04_Bilder_Fotos":
                            cat_matched = True
                        elif f_inst and target_cat == "08_Programme_Installer":
                            cat_matched = True
                        elif f_med and target_cat == "09_Videos_Medien":
                            cat_matched = True
                        elif (f_txt or f_off) and target_cat not in ("07_Archiv_Historie/04_Bilder_Fotos", "08_Programme_Installer", "09_Videos_Medien"):
                            cat_matched = True
                        elif target_cat in ("10_Ordner_Projekte", "01_Eingang_Inbox"):
                            cat_matched = True

                    if cat_matched:
                        found_items.append(p)
                        _report()
        else:
            for p in src.iterdir():
                if _is_cancelled():
                    return found_items
                if (
                    p.is_file()
                    and not _skip_cloud_file(p)
                    and self._is_allowed_file(
                        p,
                        filter_all=f_all,
                        filter_images=f_img,
                        filter_text_docs=f_txt,
                        filter_office=f_off,
                        filter_media=f_med,
                        filter_installers=f_inst,
                    )
                    and self._matches_extra_filters(p, src, dt_from, dt_to, keywords, min_bytes=min_bytes)
                ):
                    found_items.append(p)
                    _report()
        return found_items

    def _build_scan_snapshot(self, files: list[Path]) -> tuple:
        snap = []
        for p in files:
            try:
                mtime_ns = p.stat().st_mtime_ns
                if p.is_dir():
                    child_cnt = sum(1 for _ in p.iterdir())
                    snap.append((str(p), mtime_ns, child_cnt))
                else:
                    snap.append((str(p), mtime_ns))
            except Exception:
                snap.append((str(p), 0))
        snap.sort()
        return tuple(snap)

    def _on_app_focus_in(self, event):
        """Prueft bei Rueckkehr vom Explorer automatisch, ob Dateien umbenannt oder geloescht wurden."""
        if event.widget is self and self._watch_fs_after_explorer and self.frame_step2.winfo_manager():
            self._watch_fs_after_explorer = False
            self.after(250, self._check_and_auto_refresh)

    def _check_and_auto_refresh(self):
        if not self.frame_step2.winfo_manager():
            return
        src = Path(self.source_dir.get().strip())
        dest_base = Path(self.target_dir.get().strip())
        current_files = self._scan_source_files(src, dest_base)
        current_snap = self._build_scan_snapshot(current_files)
        if self._last_scan_snapshot is not None and current_snap != self._last_scan_snapshot:
            self.refresh_preview()

    def refresh_preview(self):
        """Aktualisiert die Vorschautabelle nach Umbenennungen/Loeschungen im Explorer."""
        self.generate_preview(is_refresh=True)

    def generate_preview(self, is_refresh: bool = False, explicit_files: list[Path] | None = None, handoff_label: str = ""):
        if getattr(self, "_is_scanning", False):
            return

        if explicit_files is not None:
            self._handoff_explicit_files = [Path(p) for p in explicit_files]
        elif not is_refresh:
            self._handoff_explicit_files = None

        active_explicit = explicit_files if explicit_files is not None else (getattr(self, "_handoff_explicit_files", None) if is_refresh else None)

        src = Path(self.source_dir.get().strip())
        dest_base = Path(self.target_dir.get().strip())

        if active_explicit is None and not src.exists():
            self.show_notice("Quellordner fehlt", "Der gewählte Quellordner existiert nicht.", f"{src}\nBitte wählen Sie einen vorhandenen Ordner aus.", kind="error")
            return

        try:
            dt_from = self._parse_filter_date(self.date_from_var.get(), is_end=False)
            dt_to = self._parse_filter_date(self.date_to_var.get(), is_end=True)
        except ValueError as err:
            self.show_notice("Ungültiger Zeitraum", "Das eingegebene Datum ist ungültig.", str(err), kind="error")
            return
        if dt_from and dt_to and dt_from > dt_to:
            self.show_notice("Ungültiger Zeitraum", "Das Startdatum liegt nach dem Enddatum.", "Bitte prüfen Sie die Eingaben in „Von“ und „Bis“.", kind="error")
            return
        keyword_terms = self._parse_keyword_terms(self.keyword_filter_var.get())
        keywords = [kw for kw, _ in keyword_terms]
        try:
            min_bytes = self._parse_min_file_size(self.min_size_var.get())
        except ValueError as err:
            self.show_notice("Ungültige Dateigröße", "Die eingegebene Mindestgröße ist ungültig.", str(err), kind="error")
            return
        min_size_txt = self.min_size_var.get().strip()

        prev_actions = {}
        prev_scroll = 0.0
        prev_selected_paths = set()
        if is_refresh:
            try:
                prev_scroll = self.tree.yview()[0]
            except Exception:
                prev_scroll = 0.0
            for rid in self.tree.selection():
                it = self.preview_items.get(rid)
                if it and it.get("src_path"):
                    prev_selected_paths.add(str(it["src_path"]))
            for it in self.preview_items.values():
                if it.get("src_path"):
                    prev_actions[str(it["src_path"])] = it.get("action")

        if not is_refresh:
            self._save_app_state()

        # Alle Tkinter-Optionen vor Start des Worker-Threads als reine Python-Werte sichern
        sub_mode = self.subfolder_mode.get()
        filter_mode = self.file_filter_mode.get()
        f_all = bool(self.filter_all_var.get())
        f_img = bool(self.filter_images_var.get())
        f_txt = bool(self.filter_text_docs_var.get())
        f_off = bool(self.filter_office_var.get())
        f_med = bool(self.filter_media_var.get())
        f_inst = bool(self.filter_installers_var.get())
        inc_installers = f_inst
        inc_media = f_med
        inc_onedrive = bool(self.include_onedrive_var.get())
        exc_sys = bool(self.exclude_system_dirs_var.get())
        act = "Kopieren" if self.action_mode.get() == "copy" else "Verschieben"

        self._is_scanning = True
        cancel_event = threading.Event()
        self._scan_cancel_event = cancel_event
        done_event = threading.Event()
        progress_state = {"phase": "scan", "found": 0, "done": 0, "total": 0}
        worker_out = {"found_files": [], "snapshot": (), "all_items": [], "error": None}

        overlay = None
        try:
            try:
                self.config(cursor="wait")
                overlay = SearchBusyOverlay(self, on_cancel=cancel_event.set)
                overlay.place(relx=0.5, rely=0.5, anchor="center")
                overlay.lift()
                self.update_idletasks()
            except Exception:
                overlay = None

            def _scan_worker():
                try:
                    if active_explicit is not None:
                        found_files = [Path(p) for p in active_explicit if Path(p).exists()]
                        progress_state["found"] = len(found_files)
                    else:
                        found_files = self._scan_source_files(
                            src,
                            dest_base,
                            dt_from=dt_from,
                            dt_to=dt_to,
                            keywords=keywords,
                            sub_mode=sub_mode,
                            filter_mode=filter_mode,
                            inc_installers=inc_installers,
                            inc_media=inc_media,
                            inc_onedrive=inc_onedrive,
                            exc_sys=exc_sys,
                            min_bytes=min_bytes,
                            filter_all=f_all,
                            filter_images=f_img,
                            filter_text_docs=f_txt,
                            filter_office=f_off,
                            filter_media=f_med,
                            filter_installers=f_inst,
                            cancel_event=cancel_event,
                            progress_cb=lambda cnt: progress_state.__setitem__("found", cnt),
                            from_ui=False,
                        )
                    if cancel_event.is_set():
                        return

                    worker_out["found_files"] = found_files
                    worker_out["snapshot"] = self._build_scan_snapshot(found_files)
                    if not found_files:
                        return

                    progress_state["phase"] = "analyze"
                    progress_state["total"] = len(found_files)

                    hash_groups = {}
                    size_counts = {}
                    all_items = []
                    for idx, p in enumerate(found_files, 1):
                        if cancel_event.is_set():
                            return
                        is_dir = p.is_dir()
                        try:
                            st_meta = p.stat()
                            mtime_ts = float(st_meta.st_mtime)
                            mtime = datetime.datetime.fromtimestamp(mtime_ts)
                            file_date = mtime.strftime("%d.%m.%Y %H:%M")
                        except Exception:
                            mtime_ts = 0.0
                            file_date = "—"

                        is_cloud_only = False
                        size_bytes = -1
                        if is_dir:
                            file_ext = "ORDNER"
                            target_sub, label, inner_count, inner_bytes = find_folder_target(p)
                            size_bytes = inner_bytes
                            size_str = format_file_size(inner_bytes)
                            items_count = inner_count
                            items_count_str = "1 Datei" if inner_count == 1 else f"{inner_count} Dateien"
                        else:
                            file_ext = p.suffix.upper().lstrip(".") or "DATEI"
                            try:
                                size_bytes = p.stat().st_size
                                size_str = format_file_size(size_bytes)
                            except Exception:
                                size_str = "—"
                            items_count = 1
                            items_count_str = "—"

                            is_cloud_only = is_cloud_only_file(p)
                            if is_cloud_only:
                                # Vorschau: Reine Cloud-Datei NIEMALS lesen oder hashen (verhindert Download!)
                                target_sub, label = find_target(p.name, "", p.suffix)
                            else:
                                content = ""
                                if p.suffix.lower() in [".txt", ".csv", ".json", ".log", ".html", ".htm"]:
                                    try:
                                        content = p.read_text(encoding="utf-8", errors="ignore")[:2000]
                                    except Exception:
                                        pass
                                elif p.suffix.lower() == ".pdf":
                                    content = extract_pdf_keywords(p)

                                target_sub, label = find_target(p.name, content, p.suffix)
                                if size_bytes >= 0:
                                    size_counts[size_bytes] = size_counts.get(size_bytes, 0) + 1

                        target_sub = self._apply_keyword_image_subfolder(p, src, target_sub, keyword_terms)

                        item = {
                            "src_path": p,
                            "name": p.name,
                            "orig_name": p.name,
                            "is_dir": is_dir,
                            "ext": file_ext,
                            "date": file_date,
                            "mtime_ts": mtime_ts,
                            "size_str": size_str,
                            "size_bytes": size_bytes,
                            "inner_count": items_count,
                            "items_count_str": items_count_str,
                            "source_path": str(p.parent),
                            "hash": "",
                            "is_cloud_only": is_cloud_only,
                            "is_duplicate": False,
                            "has_duplicates": False,
                            "original_item": None,
                            "target_sub": target_sub,
                            "label": label,
                            "action": act,
                        }
                        all_items.append(item)
                        progress_state["done"] = idx

                    # Groessen-Vorfilter: MD5 nur fuer lokale Dateien berechnen, deren Byte-Groesse mindestens 2x vorkommt
                    hash_candidates = [
                        it for it in all_items
                        if (
                            not it["is_dir"]
                            and not it["is_cloud_only"]
                            and it["size_bytes"] >= 0
                            and size_counts.get(it["size_bytes"], 0) > 1
                        )
                    ]
                    if hash_candidates:
                        progress_state["total"] = len(all_items) + len(hash_candidates)
                    for h_idx, it in enumerate(hash_candidates, 1):
                        if cancel_event.is_set():
                            return
                        fhash = file_hash(it["src_path"])
                        it["hash"] = fhash
                        if fhash:
                            hash_groups.setdefault(fhash, []).append(it)
                        progress_state["done"] = len(all_items) + h_idx

                    dup_strategy = getattr(self, "dup_original_strategy", "clean_name")
                    for fhash, group in hash_groups.items():
                        if cancel_event.is_set():
                            return
                        if len(group) > 1:
                            group.sort(key=lambda it: duplicate_priority_key(it, dup_strategy))
                            original = group[0]
                            original["has_duplicates"] = True
                            for dup in group[1:]:
                                dup["is_duplicate"] = True
                                dup["original_item"] = original
                                dup["target_sub"] = original["target_sub"]
                                prev_dup_act = prev_actions.get(str(dup["src_path"]))
                                dup["action"] = prev_dup_act if prev_dup_act in ("Überspringen", "Mitkopieren") else "Überspringen"

                    worker_out["all_items"] = all_items
                except Exception as exc:
                    worker_out["error"] = exc
                finally:
                    done_event.set()

            threading.Thread(target=_scan_worker, daemon=True).start()

            last_anim = 0.0
            while not done_event.is_set():
                now = time.monotonic()
                if overlay is not None and (now - last_anim) >= 0.09:
                    last_anim = now
                    try:
                        overlay.step_animation(
                            phase=progress_state["phase"],
                            found_count=progress_state["found"],
                            done_count=progress_state["done"],
                            total_count=progress_state["total"],
                        )
                    except tk.TclError:
                        break
                try:
                    self.update()
                except tk.TclError:
                    cancel_event.set()
                    break
                done_event.wait(0.03)
        finally:
            self._is_scanning = False
            self._scan_cancel_event = None
            if overlay is not None:
                try:
                    overlay.destroy()
                except Exception:
                    pass
            try:
                self.config(cursor="")
            except Exception:
                pass

        try:
            if not self.winfo_exists():
                return
        except Exception:
            return

        if cancel_event.is_set():
            if worker_out.get("all_items"):
                self.show_notice(
                    "Suche vorzeitig beendet",
                    f"Die Suche wurde abgebrochen. Es wurden bisher {len(worker_out['all_items'])} passende Elemente gefunden.",
                    "Diese werden nun in der Vorschau angezeigt.",
                    kind="info",
                    auto_close_ms=3000,
                )
            else:
                self.show_notice(
                    "Suche abgebrochen",
                    "Die Ordnersuche wurde auf Ihren Wunsch hin abgebrochen.",
                    kind="info",
                    auto_close_ms=3000,
                )
                return

        if worker_out["error"] is not None:
            self.show_notice("Suchfehler", "Beim Durchsuchen des Quellordners ist ein Fehler aufgetreten.", str(worker_out["error"]), kind="error")
            return

        # Tabelle leeren und Ergebnisse einsetzen
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.preview_items = {}
        self.duplicate_ids = []
        self._preview_row_order = []
        if not is_refresh:
            self.step2_filter_chip = "all"
            self.step2_file_filter_mode = "all"
            self.step2_only_onedrive = False
            self._suppress_step2_filter_trace = True
            try:
                for var in (
                    self.step2_search_var,
                    self.step2_date_from_var,
                    self.step2_date_to_var,
                    self.step2_min_size_var,
                ):
                    if var.get():
                        var.set("")
            finally:
                self._suppress_step2_filter_trace = False
            self.update_step2_detail_filter_ui()
            if self._step2_filter_after_id is not None:
                try:
                    self.after_cancel(self._step2_filter_after_id)
                except Exception:
                    pass
                self._step2_filter_after_id = None

        found_files = worker_out["found_files"]
        self._last_scan_snapshot = worker_out["snapshot"]
        all_items = worker_out["all_items"]

        if not found_files:
            self.apply_step2_filters()
            if is_refresh:
                can_write, _ = self.check_write_permission(dest_base)
                perm_tag = "✓ Berechtigung OK" if can_write else "⚠️ Keine Schreibrechte!"
                self.lbl_target_info.config(text=f"📂 Zielordner: {dest_base}  [{perm_tag}]")
                refresh_tag = f"  |  ✓ Aktualisiert {datetime.datetime.now().strftime('%H:%M:%S')}"
                self.lbl_summary.config(text=f"Gefunden: 0 Dokumente  |  Zugeordnet (02-07): 0  |  Unbekannt (-> Inbox): 0  |  Erkannte Duplikate: 0{refresh_tag}")
                self.update_dup_counter()
            else:
                has_extra_filter = bool(dt_from or dt_to or keywords or min_bytes is not None)
                if has_extra_filter:
                    self.show_notice(
                        "Keine passenden Einträge",
                        "Mit den gewählten Filtern (Zeitraum, Stichwort oder Dateigröße) wurden keine Einträge gefunden.",
                        "Passen Sie die Filter an oder klicken Sie auf „Filter leeren“.",
                        kind="info",
                    )
                else:
                    self.show_notice(
                        "Keine Dateien gefunden",
                        "Im ausgewählten Quellordner wurden keine passenden Dokumente gefunden.",
                        kind="info",
                    )
            return

        # 3. Pass: In Tabelle einfuegen
        matched_count = 0
        inbox_count = 0
        dup_count = 0
        restored_selection = []

        empty_vals = tuple([""] * len(self.col_keys))
        for item in all_items:
            item["orig_name"] = item.get("orig_name", item.get("name", ""))
            item["orig_target_sub"] = item.get("target_sub", "")
            item["orig_label"] = item.get("label", "")
            item["orig_action"] = item.get("action", "")
            row_id = self.tree.insert("", "end", values=empty_vals)
            self._preview_row_order.append(row_id)
            self.preview_items[row_id] = item
            if is_refresh and str(item["src_path"]) in prev_selected_paths:
                restored_selection.append(row_id)

            if item["is_duplicate"]:
                dup_count += 1
                self.duplicate_ids.append(row_id)
            else:
                if item["target_sub"].startswith("01_Eingang_Inbox"):
                    inbox_count += 1
                else:
                    matched_count += 1

            self.update_row_display(row_id)

        # Berechtigungs- & NetzwerkSchutz-Anzeige
        can_write, _ = self.check_write_permission(dest_base)
        perm_tag = "✓ Berechtigung OK" if can_write else "⚠️ Keine Schreibrechte!"
        net_src_or_dest = is_network_path(src) or is_network_path(dest_base)
        net_tag = ""
        if net_src_or_dest:
            net_tag = "  |  🌐 Netzlaufwerk (🛡️ NetzwerkSchutz aktiv)" if self.network_guard_var.get() else "  |  🌐 Netzlaufwerk (⚠️ NetzwerkSchutz AUS)"
        self.lbl_target_info.config(
            text=f"📂 Zielordner: {dest_base}  [{perm_tag}]{net_tag}"
        )
        refresh_tag = f"  |  ✓ Aktualisiert {datetime.datetime.now().strftime('%H:%M:%S')}" if is_refresh else ""
        filter_parts = []
        if self.date_from_var.get().strip() or self.date_to_var.get().strip():
            filter_parts.append(f"{self.date_from_var.get().strip() or '…'}–{self.date_to_var.get().strip() or '…'}")
        if keyword_terms:
            disp_terms = [safe or kw for kw, safe in keyword_terms]
            filter_parts.append(f"„{', '.join(disp_terms)}“")
        if min_bytes is not None and min_size_txt:
            filter_parts.append(f"Ab {min_size_txt}")
        filter_tag = f"  |  Filter: {' · '.join(filter_parts)}" if filter_parts else ""
        self._last_filter_tag = filter_tag
        self.update_dup_counter()
        self.apply_step2_filters()
        if refresh_tag:
            self._update_step2_summary(refresh_tag=refresh_tag)
        if not is_refresh:
            self.show_step2()
            self.update_idletasks()
            if not self._custom_col_widths:
                self.auto_fit_columns()
            self._on_tree_selection_changed()
        else:
            if not self._custom_col_widths:
                self.auto_fit_columns()
            if restored_selection:
                self.tree.selection_set(restored_selection)
            self.update_idletasks()
            try:
                self.tree.yview_moveto(prev_scroll)
            except Exception:
                pass
            self._on_tree_selection_changed()

    def show_app_info(self):
        dlg = AppInfoDialog(self)
        self.wait_window(dlg)

    def refresh_git_badge(self):
        is_dark = (getattr(self, "theme_mode", "light") == "dark")
        bg = "#312e81" if is_dark else "#eef2ff"
        fg = "#c7d2fe" if is_dark else "#3730a3"
        hbg = "#3730a3" if is_dark else "#e0e7ff"
        bd = "#4f46e5" if is_dark else "#a5b4fc"
        for attr in ("btn_git_step1", "btn_git_step2"):
            btn = getattr(self, attr, None)
            if btn is not None:
                try:
                    btn.update_style(text=f"⬆ GitHub {APP_VERSION}", bg=bg, fg=fg, hover_bg=hbg, border_color=bd)
                except Exception:
                    pass

    def open_github_sync_dialog(self):
        dlg = GitHubSyncDialog(self)
        self.wait_window(dlg)

    def _restart_after_git_update(self, spawn_new=True):
        try:
            self._save_app_state()
        except Exception:
            pass
        if spawn_new:
            try:
                if getattr(sys, "frozen", False):
                    subprocess.Popen([sys.executable], cwd=os.path.dirname(sys.executable))
                else:
                    subprocess.Popen([sys.executable, os.path.abspath(__file__)], cwd=os.path.dirname(os.path.abspath(__file__)))
            except Exception:
                pass
        try:
            self.destroy()
        finally:
            os._exit(0)

    def show_notice(self, title: str, headline: str, subline: str = "",
                    kind: str = "info", ok_text: str = "OK",
                    auto_close_ms: int | None = None,
                    extra_button_text: str = "",
                    extra_button_command=None):
        full_msg = f"{headline}\n{subline}" if subline else headline
        if kind == "error" and messagebox.showerror is not _ORIG_SHOWERROR:
            messagebox.showerror(title, full_msg)
            return
        if kind == "warning" and messagebox.showwarning is not _ORIG_SHOWWARNING:
            messagebox.showwarning(title, full_msg)
            return
        if kind in ("info", "success") and messagebox.showinfo is not _ORIG_SHOWINFO:
            messagebox.showinfo(title, full_msg)
            return
        dlg = ModernNoticeDialog(
            self,
            title=title,
            headline=headline,
            subline=subline,
            kind=kind,
            confirm=False,
            ok_text=ok_text,
            auto_close_ms=auto_close_ms,
            extra_button_text=extra_button_text,
            extra_button_command=extra_button_command,
        )
        self.wait_window(dlg)

    def confirm_action(self, title: str, headline: str, subline: str = "",
                       confirm_text: str = "Bestätigen", cancel_text: str = "Abbrechen") -> bool:
        if messagebox.askyesno is not _ORIG_ASKYESNO:
            msg = f"{headline}\n{subline}" if subline else headline
            return bool(messagebox.askyesno(title, msg))
        dlg = ModernConfirmDialog(
            self,
            title=title,
            headline=headline,
            subline=subline,
            confirm_text=confirm_text,
            cancel_text=cancel_text,
        )
        self.wait_window(dlg)
        return bool(dlg.result)

    def _get_scope_execution_targets(self) -> tuple[list[str] | None, str, str]:
        """Liefert (row_ids_or_None, mode, button_text) strikt anhand der aktuellen Markierung."""
        tree = getattr(self, "tree", None)
        items = getattr(self, "preview_items", None) or {}
        active_rids = [rid for rid, it in items.items() if it and it.get("action") != "Gelöscht"]
        total_active = len(active_rids)
        if tree is None or total_active == 0:
            return [], "none", "Keine Auswahl"

        sel = [rid for rid in tree.selection() if rid in items and items[rid].get("action") != "Gelöscht"]
        if not sel:
            return [], "none", "Keine Auswahl"

        if len(sel) >= total_active:
            return None, "all", f"Auf alle {total_active} anwenden ➔"

        lbl = "Auf 1 markierte anwenden ➔" if len(sel) == 1 else f"Auf {len(sel)} markierte anwenden ➔"
        return sel, "selection", lbl

    def _update_execute_scope_button(self):
        btn_exec = getattr(self, "btn_execute", None)
        if btn_exec is not None:
            try:
                _, mode, lbl = self._get_scope_execution_targets()
                if mode == "none":
                    btn_exec.update_style(
                        text=lbl,
                        bg="#e2e8f0",
                        fg="#64748b",
                        hover_bg="#e2e8f0",
                        border_color="#cbd5e1",
                    )
                else:
                    btn_exec.update_style(
                        text=lbl,
                        bg="#2563eb",
                        fg="#ffffff",
                        hover_bg="#1d4ed8",
                        border_color=None,
                    )
            except Exception:
                pass
        btn_all = getattr(self, "btn_toggle_select_all", None)
        tree = getattr(self, "tree", None)
        if btn_all is not None and tree is not None:
            try:
                vis = list(tree.get_children(""))
                sel = set(tree.selection())
                all_selected = bool(vis) and all(rid in sel for rid in vis)
                if all_selected:
                    btn_all.update_style(
                        text="☐ Auswahl aufheben",
                        bg="#dbeafe",
                        fg="#1d4ed8",
                        hover_bg="#bfdbfe",
                        border_color="#60a5fa",
                    )
                else:
                    btn_all.update_style(
                        text="✓ Alle wählen",
                        bg="#eff6ff",
                        fg="#1d4ed8",
                        hover_bg="#dbeafe",
                        border_color="#93c5fd",
                    )
            except Exception:
                pass

    def execute_selected_or_filtered_sort(self):
        rids, mode, _ = self._get_scope_execution_targets()
        if mode == "none":
            self.show_notice(
                "Keine Einträge ausgewählt",
                "Bitte markieren Sie zuerst die gewünschten Zeilen in der Tabelle – oder klicken Sie auf „✓ Alle wählen“.",
                kind="info",
            )
            return
        if mode == "all":
            self.execute_sort()
            return
        if not rids:
            self.show_notice(
                "Keine passenden Einträge",
                "Die ausgewählten Einträge können aktuell nicht ausgeführt werden.",
                kind="info",
            )
            return
        self.execute_sort(target_row_ids=rids, scope_label="aus Markierung")

    def execute_sort(self, target_row_ids: list[str] | None = None, scope_label: str = "", override_action_mode: str | None = None):
        items_to_process = []
        dest_base = Path(self.target_dir.get().strip())
        uses_dest_base = False
        custom_roots = []
        is_partial = (target_row_ids is not None)
        eff_mode = override_action_mode if override_action_mode in ("copy", "move") else self.action_mode.get()

        if is_partial:
            seen_rids = set()
            row_pairs = []
            for rid in target_row_ids:
                if rid not in seen_rids and rid in self.preview_items:
                    seen_rids.add(rid)
                    row_pairs.append((rid, self.preview_items[rid]))
        else:
            row_pairs = list(self.preview_items.items())

        for row_id, item in row_pairs:
            act = item.get("action", "")
            is_dir = bool(item.get("is_dir", False))
            if act in ["Kopieren", "Verschieben", "Mitkopieren"]:
                raw_sub = str(item.get("target_sub") or "01_Eingang_Inbox").strip()
                if os.path.isabs(raw_sub):
                    target_dir = Path(os.path.normpath(raw_sub))
                    if target_dir not in custom_roots:
                        custom_roots.append(target_dir)
                else:
                    target_dir = dest_base / raw_sub
                    uses_dest_base = True
                if act == "Mitkopieren":
                    p_name = Path(item["name"])
                    stem = p_name.stem
                    suffix = p_name.suffix
                    target_file = target_dir / f"{stem}_01{suffix}"
                    items_to_process.append({
                        "rid": row_id,
                        "src_path": item["src_path"],
                        "dest_dir": target_dir,
                        "dest_file": target_file,
                        "action": "copy",
                        "is_dup": True,
                        "is_dir": is_dir,
                        "size_bytes": max(0, item.get("size_bytes", 0)),
                        "is_cloud_only": bool(item.get("is_cloud_only", False)),
                    })
                else:
                    target_file = target_dir / item["name"]
                    item_action = (
                        ("copy" if override_action_mode == "copy" else "move")
                        if override_action_mode in ("copy", "move")
                        else ("copy" if "Kopieren" in act else "move")
                    )
                    items_to_process.append({
                        "rid": row_id,
                        "src_path": item["src_path"],
                        "dest_dir": target_dir,
                        "dest_file": target_file,
                        "action": item_action,
                        "is_dup": False,
                        "is_dir": is_dir,
                        "size_bytes": max(0, item.get("size_bytes", 0)),
                        "is_cloud_only": bool(item.get("is_cloud_only", False)),
                    })

        if not items_to_process:
            if is_partial:
                self.show_notice(
                    "Keine aktiven Aktionen",
                    "Unter den markierten bzw. gefilterten Einträgen steht kein Eintrag auf „Kopieren“, „Verschieben“ oder „Mitkopieren“.",
                    "Prüfen Sie in der Spalte „Aktion“, ob die Zeilen aktuell auf „Überspringen“ oder „Gelöscht“ stehen.",
                    kind="info",
                )
            else:
                self.show_notice("Keine Aktionen", "Es sind keine Dokumente zum Kopieren oder Verschieben markiert.", kind="info")
            return

        # NetzwerkSchutz: Versehentliches Verschieben aus gemeinsamem Netzlaufwerk abfangen
        if eff_mode == "move" and self.network_guard_var.get() and any(is_network_path(it["src_path"]) for it in items_to_process):
            safe_switch = self.confirm_action(
                title="NetzwerkSchutz: Gemeinsamen Ordner schützen",
                headline="Quelldateien liegen auf einem Netzlaufwerk – „Verschieben“ würde Originale vom Server entfernen!",
                subline=(
                    "Damit Kollegen keine gemeinsamen Dateien verlieren, empfiehlt der NetzwerkSchutz „Nur Kopieren“ "
                    "(Originale bleiben unangetastet auf dem Server).\n\n"
                    "Möchten Sie sicherheitshalber auf „Kopieren“ umschalten?"
                ),
                confirm_text="Sicher nur kopieren",
            )
            if not safe_switch:
                return
            eff_mode = "copy"
            if not override_action_mode:
                self.action_mode.set("copy")
            for it in items_to_process:
                if it["action"] == "move":
                    it["action"] = "copy"
            for rid, p_it in self.preview_items.items():
                if p_it.get("action") == "Verschieben":
                    p_it["action"] = "Kopieren"
                    self.update_row_display(rid)

        check_targets = ([dest_base] if uses_dest_base else []) + custom_roots
        for chk_dir in check_targets:
            can_write, err_msg = self.check_write_permission(chk_dir)
            if not can_write:
                self.show_notice("Fehlende Schreibrechte", f"In das gewählte Zielverzeichnis ({chk_dir}) kann nicht geschrieben werden.", str(err_msg), kind="error")
                return

        is_copy = (eff_mode == "copy")
        has_dirs = any(it.get("is_dir") for it in items_to_process)
        n_items = len(items_to_process)
        if has_dirs:
            count_label = "1 Eintrag" if n_items == 1 else f"{n_items} Einträge"
        else:
            count_label = "1 Dokument" if n_items == 1 else f"{n_items} Dokumente"

        dlg_title = "Kopieren bestätigen" if is_copy else "Verschieben bestätigen"
        action_verb = "kopieren" if is_copy else "verschieben"
        action_name = "kopiert" if is_copy else "verschoben"
        confirm_btn = "Jetzt kopieren" if is_copy else "Jetzt verschieben"

        cloud_cnt = sum(1 for it in items_to_process if it.get("is_cloud_only"))
        sub_msg = (
            "Nur diese Auswahl wird jetzt ausgeführt; alle übrigen Zeilen bleiben in der Tabelle erhalten."
            if is_partial
            else "Sie können dies später über den Rückgängig-Knopf zurücksetzen."
        )
        if cloud_cnt > 0:
            cloud_word = "1 Cloud-Datei" if cloud_cnt == 1 else f"{cloud_cnt} Cloud-Dateien"
            cloud_hint = f"Hinweis: Enthält {cloud_word} aus OneDrive (werden jetzt heruntergeladen)."
            sub_msg = f"{cloud_hint}\n{sub_msg}" if is_partial else cloud_hint

        scope_suffix = f" ({scope_label})" if is_partial and scope_label else ""
        headline_txt = (
            f"Möchten Sie jetzt nur {count_label}{scope_suffix} {action_verb}?"
            if is_partial
            else f"Möchten Sie jetzt {count_label} {action_verb}?"
        )
        confirmed = self.confirm_action(
            title=dlg_title,
            headline=headline_txt,
            subline=sub_msg,
            confirm_text=confirm_btn,
        )
        if not confirmed:
            return

        # Asynchrone, nicht-blockierende Ausfuehrung mit TransferBusyOverlay (Byte-genauer Live-Fortschritt)
        total_transfer_bytes = sum(max(0, it.get("size_bytes", 0)) for it in items_to_process)
        cancel_event = threading.Event()
        done_event = threading.Event()
        progress_state = {
            "item_idx": 0,
            "total_items": len(items_to_process),
            "current_item": "",
            "current_subfile": "",
            "file_copied": 0,
            "file_total": 0,
            "copied_bytes": 0,
            "total_bytes": total_transfer_bytes,
            "speed_mb_s": 0.0,
            "success": 0,
            "failed": [],
            "cancelled": False,
            "executed_rids_copy": [],
            "executed_rids_move": [],
            "transaction_items": [],
        }

        last_speed_t = time.monotonic()
        last_speed_b = 0

        def _on_chunk(n_bytes, file_copied, file_total):
            nonlocal last_speed_t, last_speed_b
            progress_state["copied_bytes"] += n_bytes
            progress_state["file_copied"] = file_copied
            progress_state["file_total"] = file_total
            now = time.monotonic()
            dt = now - last_speed_t
            if dt >= 0.35:
                diff = progress_state["copied_bytes"] - last_speed_b
                progress_state["speed_mb_s"] = max(0.0, (diff / (1024 * 1024)) / max(0.01, dt))
                last_speed_t = now
                last_speed_b = progress_state["copied_bytes"]

        def _on_subfile_start(fname, fsize):
            progress_state["current_subfile"] = fname
            progress_state["file_copied"] = 0
            progress_state["file_total"] = fsize

        overlay = None
        try:
            self.config(cursor="wait")
            overlay = TransferBusyOverlay(
                self,
                is_copy=is_copy,
                total_items=len(items_to_process),
                on_cancel=cancel_event.set,
            )
            overlay.place(relx=0.5, rely=0.5, anchor="center")
            overlay.lift()
            self.update_idletasks()
        except Exception:
            overlay = None

        def _transfer_worker():
            try:
                for idx, it in enumerate(items_to_process):
                    if cancel_event.is_set():
                        progress_state["cancelled"] = True
                        break

                    src = it["src_path"]
                    dest_dir = it["dest_dir"]
                    dest_file = it["dest_file"]

                    progress_state["item_idx"] = idx
                    progress_state["current_item"] = src.name
                    progress_state["current_subfile"] = ""
                    progress_state["file_copied"] = 0
                    progress_state["file_total"] = 0

                    try:
                        dest_dir.mkdir(parents=True, exist_ok=True)
                    except Exception as exc:
                        print(f"Fehler beim Erstellen von {dest_dir}: {exc}")
                        progress_state["failed"].append((src.name, str(exc)))
                        continue

                    final_dest = dest_file
                    counter = 1
                    while final_dest.exists():
                        if it.get("is_dir"):
                            final_dest = dest_dir / f"{dest_file.name}_{counter}"
                        else:
                            stem = dest_file.stem
                            suffix = dest_file.suffix
                            tag = f"_{counter:02d}" if it["is_dup"] else f"_{counter}"
                            final_dest = dest_dir / f"{stem}{tag}{suffix}"
                        counter += 1

                    try:
                        if it["action"] == "copy":
                            if it.get("is_dir"):
                                ok = _copy_directory_worker(
                                    src,
                                    final_dest,
                                    cancel_ev=cancel_event,
                                    file_start_cb=_on_subfile_start,
                                    byte_cb=_on_chunk,
                                )
                                if not ok and cancel_event.is_set():
                                    progress_state["cancelled"] = True
                                    break
                            else:
                                _on_subfile_start(src.name, src.stat().st_size if src.exists() else 0)
                                ok = _copy_file_chunked(
                                    src,
                                    final_dest,
                                    cancel_ev=cancel_event,
                                    byte_cb=_on_chunk,
                                )
                                if not ok and cancel_event.is_set():
                                    progress_state["cancelled"] = True
                                    break
                            if ok:
                                progress_state["executed_rids_copy"].append(it["rid"])
                            else:
                                progress_state["failed"].append((src.name, "Kopiervorgang unvollständig"))
                        else:
                            if it.get("is_dir"):
                                ok = _move_directory_worker(
                                    src,
                                    final_dest,
                                    cancel_ev=cancel_event,
                                    file_start_cb=_on_subfile_start,
                                    byte_cb=_on_chunk,
                                )
                                if not ok and cancel_event.is_set():
                                    progress_state["cancelled"] = True
                                    break
                            else:
                                _on_subfile_start(src.name, src.stat().st_size if src.exists() else 0)
                                moved_ok = False
                                ok = False
                                try:
                                    os.rename(str(src), str(final_dest))
                                    moved_ok = True
                                    ok = True
                                    progress_state["copied_bytes"] += it.get("size_bytes", 0)
                                except OSError:
                                    pass
                                if not moved_ok:
                                    ok = _copy_file_chunked(
                                        src,
                                        final_dest,
                                        cancel_ev=cancel_event,
                                        byte_cb=_on_chunk,
                                    )
                                    if not ok and cancel_event.is_set():
                                        progress_state["cancelled"] = True
                                        break
                                    if ok and not cancel_event.is_set():
                                        try:
                                            src.unlink()
                                        except Exception:
                                            pass
                            if ok:
                                progress_state["executed_rids_move"].append(it["rid"])
                            else:
                                progress_state["failed"].append((src.name, "Verschieben unvollständig"))

                        if ok:
                            progress_state["transaction_items"].append({
                                "src": str(src),
                                "dest": str(final_dest),
                                "is_dir": bool(it.get("is_dir", False)),
                            })
                            progress_state["success"] += 1
                    except Exception as e:
                        print(f"Fehler bei {src}: {e}")
                        progress_state["failed"].append((src.name, str(e)))
            finally:
                done_event.set()

        threading.Thread(target=_transfer_worker, daemon=True).start()

        last_anim = 0.0
        while not done_event.is_set():
            now = time.monotonic()
            if overlay is not None and (now - last_anim) >= 0.04:
                last_anim = now
                try:
                    overlay.update_progress(
                        item_idx=progress_state["item_idx"],
                        total_items=progress_state["total_items"],
                        current_item=progress_state["current_item"],
                        current_subfile=progress_state["current_subfile"],
                        copied_bytes=progress_state["copied_bytes"],
                        total_bytes=progress_state["total_bytes"],
                        file_copied=progress_state["file_copied"],
                        file_total=progress_state["file_total"],
                        speed_mb_s=progress_state["speed_mb_s"],
                    )
                except tk.TclError:
                    break
            try:
                self.update()
            except tk.TclError:
                cancel_event.set()
                break
            done_event.wait(0.02)

        if overlay is not None:
            try:
                overlay.destroy()
            except Exception:
                pass
        try:
            self.config(cursor="")
        except Exception:
            pass

        # Transaktion speichern
        if progress_state["transaction_items"]:
            transaction = {
                "timestamp": datetime.datetime.now().isoformat(),
                "action": eff_mode,
                "items": progress_state["transaction_items"],
            }
            try:
                history = []
                if HISTORY_FILE.exists():
                    history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                history.append(transaction)
                HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                print(f"Fehler beim Schreiben der Historie: {e}")

        # Tabelle aktualisieren (fuer kopierte / verschobene Zeilen)
        executed_rids_copy = progress_state["executed_rids_copy"]
        executed_rids_move = progress_state["executed_rids_move"]
        if is_partial and (executed_rids_copy or executed_rids_move):
            moved_set = set(executed_rids_move)
            for rid in executed_rids_copy:
                item = self.preview_items.get(rid)
                if item:
                    item["action"] = "Überspringen"
                    self.update_row_display(rid)
            if moved_set:
                for rid in moved_set:
                    self.preview_items.pop(rid, None)
                    try:
                        if self.tree.exists(rid):
                            self.tree.delete(rid)
                    except Exception:
                        pass
                self._preview_row_order = [r for r in self._preview_row_order if r not in moved_set]
                self.duplicate_ids = [r for r in self.duplicate_ids if r not in moved_set]
            self.update_dup_counter()
            self.apply_step2_filters()
            self._update_execute_scope_button()

        has_dirs = any(it.get("is_dir") for it in items_to_process)
        if has_dirs:
            unit_label = "Einträge (Dateien & Ordner)"
        elif self.file_filter_mode.get() == "only_images":
            unit_label = "Bilddateien"
        else:
            unit_label = "Dateien"

        explorer_target = self._resolve_explorer_target_dir(dest_base, progress_state["transaction_items"])
        self._last_explorer_target = str(explorer_target)

        # Abbruch abfangen
        if progress_state["cancelled"]:
            self.show_notice(
                "Vorgang abgebrochen",
                f"Es wurden {progress_state['success']} von {len(items_to_process)} {unit_label} {action_name}.",
                "Die restlichen Vorgänge wurden gestoppt. Bereits ausgeführte Elemente wurden in der Tabelle aktualisiert.",
                kind="warning",
                extra_button_text="📂 Im Explorer zeigen",
                extra_button_command=lambda: self.open_target_explorer(explorer_target),
            )
            return

        # Bei Teilausführung (Nur markierte/gefilterte ausführen):
        # In Schritt 2 bleiben, Zeilen aktualisiert anzeigen und klare Rückmeldung mit Direkt-Link zum Zielordner geben!
        if is_partial:
            if progress_state["success"] > 0:
                dlg_title = "Ausführung erfolgreich" if not progress_state["failed"] else "Teilweise ausgeführt"
                dlg_kind = "success" if not progress_state["failed"] else "warning"
                headline_txt = f"✓ {progress_state['success']} von {len(items_to_process)} {unit_label} erfolgreich {action_name}."
                subline_txt = f"Zielordner: {explorer_target}\n\nDie ausgeführten Zeilen wurden in der Tabelle auf „Überspringen“ gesetzt."
                if progress_state["failed"]:
                    subline_txt += f"\n⚠️ Bei {len(progress_state['failed'])} Element(en) trat ein Fehler auf (bleiben in der Tabelle erhalten)."
            else:
                dlg_title = "Ausführung fehlgeschlagen"
                dlg_kind = "error"
                headline_txt = f"⚠️ Keines der {len(items_to_process)} {unit_label} konnte {action_name} werden."
                err_details = "\n".join(f"• {name}: {err}" for name, err in progress_state["failed"][:3])
                subline_txt = f"Zielordner: {explorer_target}\n\nUrsache:\n{err_details}\n\nDie Tabellenzeilen bleiben unverändert erhalten."

            self.show_notice(
                dlg_title,
                headline_txt,
                subline=subline_txt,
                kind=dlg_kind,
                extra_button_text="📂 Im Explorer öffnen",
                extra_button_command=lambda: self.open_target_explorer(explorer_target),
            )
            if eff_mode == "move" and progress_state["transaction_items"]:
                self._prompt_cleanup_empty_source_dirs(progress_state["transaction_items"], explorer_target)
            return

        # Vollständige Ausführung: Übergang zu Schritt 3 & Zielordner öffnen
        self.lbl_result.config(
            text=f"✓  Es wurden {progress_state['success']} von {len(items_to_process)} {unit_label} erfolgreich {action_name}."
        )
        executed_set = set(executed_rids_copy) | set(executed_rids_move)
        unprocessed_cnt = sum(
            1 for rid, it in self.preview_items.items()
            if rid not in executed_set and it.get("action") not in ("Gelöscht", "Überspringen")
        )
        has_remaining = unprocessed_cnt > 0
        if hasattr(self, "lbl_step3_subtitle"):
            self.lbl_step3_subtitle.config(
                text=(
                    "Die ausgewählten Elemente wurden übernommen. Übrige Einträge stehen weiter in der Tabelle bereit."
                    if has_remaining
                    else "Alle ausgewählten Elemente wurden geordnet in das 7-Ordner-System übernommen."
                )
            )
        if hasattr(self, "btn_step3_back_to_table"):
            if not self.btn_step3_back_to_table.winfo_manager():
                self.btn_step3_back_to_table.pack(side="left", before=self.btn_step3_undo, padx=(0, 10))
            if has_remaining:
                self.btn_step3_back_to_table.update_style(text=f"⬅ Zurück zur Vorschau – {unprocessed_cnt} übrig")
            else:
                self.btn_step3_back_to_table.update_style(text="⬅ Zurück zur Vorschau")
        self.lbl_step3_src.config(text=self.source_dir.get().strip())
        self.lbl_step3_dest.config(text=str(explorer_target))
        if eff_mode == "copy":
            self.lbl_step3_hint.config(
                text="Ihre Originaldateien im Quellordner wurden nicht verändert. Mit „Letzte Aktion rückgängig“ können Sie den Vorgang jederzeit zurücknehmen."
            )
        else:
            self.lbl_step3_hint.config(
                text="Die ausgewählten Elemente wurden direkt in die neuen Ordner verschoben. Bei Bedarf können Sie den Vorgang einfach rückgängig machen."
            )
        self.show_step3()
        if eff_mode == "move" and progress_state["transaction_items"]:
            self._prompt_cleanup_empty_source_dirs(progress_state["transaction_items"], explorer_target)
        self.open_target_explorer(explorer_target)

    def _is_dir_effectively_empty(self, folder: Path) -> bool:
        """Prüft, ob ein Ordner komplett leer ist (oder nur desktop.ini / thumbs.db enthält)."""
        try:
            if not folder.is_dir():
                return False
            with os.scandir(folder) as it:
                for ent in it:
                    if ent.name.lower() in ("desktop.ini", "thumbs.db"):
                        continue
                    return False
            return True
        except Exception:
            return False

    def _prompt_cleanup_empty_source_dirs(self, tx_items: list[dict], explorer_target: Path | str | None = None):
        """Prüft nach dem Verschieben, ob Quellordner jetzt leer sind, und bietet an, sie sicher in den Papierkorb zu legen."""
        protected_names = {
            "desktop", "documents", "dokumente", "downloads", "pictures", "bilder",
            "videos", "music", "musik", "onedrive", "users", "benutzer", "windows",
            "program files", "program files (x86)", "serien", "filme", "plex",
        }
        try:
            tgt_norm = os.path.normcase(os.path.abspath(str(explorer_target))) if explorer_target else ""
        except Exception:
            tgt_norm = ""

        candidates: dict[str, Path] = {}
        for rec in (tx_items or []):
            src_str = rec.get("src") or ""
            if not src_str:
                continue
            parent = Path(src_str).parent
            if not parent or parent == parent.parent:
                continue
            if is_network_path(parent):
                continue
            norm_p = os.path.normcase(os.path.abspath(str(parent)))
            if norm_p in candidates:
                continue
            if tgt_norm and (norm_p == tgt_norm or tgt_norm.startswith(norm_p + os.sep)):
                continue
            if parent.name.lower() in protected_names:
                continue
            if self._is_dir_effectively_empty(parent):
                candidates[norm_p] = parent

        empty_dirs = sorted(candidates.values(), key=lambda p: str(p).lower())
        if not empty_dirs:
            return

        cnt = len(empty_dirs)
        preview_lines = "\n".join(f"• {p}" for p in empty_dirs[:8])
        if cnt > 8:
            preview_lines += f"\n… und {cnt - 8} weitere"

        headline = (
            "1 Quellordner ist jetzt leer – in den Papierkorb verschieben?"
            if cnt == 1
            else f"{cnt} Quellordner sind jetzt leer – in den Papierkorb verschieben?"
        )
        subline = (
            f"{preview_lines}\n\n"
            "Hinweis: Es werden nur wirklich leere Ordner sicher in den Windows-Papierkorb verschoben."
        )
        ok = self.confirm_action(
            title="Leere Quellordner aufräumen",
            headline=headline,
            subline=subline,
            confirm_text="In Papierkorb verschieben",
            cancel_text="Behalten",
        )
        if not ok:
            return

        removed = 0
        for p in empty_dirs:
            try:
                if self._is_dir_effectively_empty(p):
                    self._move_path_to_trash(p)
                    removed += 1
            except Exception as exc:
                print(f"Leerer Ordner konnte nicht entfernt werden ({p}): {exc}")

        if removed > 0:
            self.show_notice(
                "Leere Ordner aufgeräumt",
                f"✓ {removed} leere{'r' if removed == 1 else ''} Quellordner in den Papierkorb verschoben.",
                kind="success",
                auto_close_ms=2500,
            )

    def revert_last_transaction(self):
        if not HISTORY_FILE.exists():
            self.show_notice("Rückgängig", "Es ist noch keine gespeicherte Aktion zum Zurücksetzen vorhanden.", kind="info")
            return

        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if not history:
                self.show_notice("Rückgängig", "Es sind keine weiteren Aktionen zum Zurücksetzen vorhanden.", kind="info")
                return

            last_tx = history.pop()
            action = last_tx.get("action", "copy")
            items = last_tx.get("items", [])

            if not items:
                self.show_notice("Rückgängig", "Die letzte Aktion enthielt keine Dateien oder Ordner.", kind="info")
                return

            n_rev = len(items)
            rev_label = "1 Eintrag" if n_rev == 1 else f"{n_rev} Einträge"
            act_label = "Kopieren" if action == "copy" else "Verschieben"
            confirmed = self.confirm_action(
                title="Rückgängig machen",
                headline=f"Möchten Sie das letzte {act_label} für {rev_label} rückgängig machen?",
                subline="Alle betroffenen Dateien und Ordner werden in den vorherigen Zustand versetzt.",
                confirm_text="Jetzt rückgängig",
            )
            if not confirmed:
                return

            reverted = 0
            for it in items:
                src_orig = Path(it["src"])
                dest_sorted = Path(it["dest"])

                if action == "copy":
                    if dest_sorted.exists():
                        if dest_sorted.is_dir():
                            shutil.rmtree(str(dest_sorted))
                        else:
                            dest_sorted.unlink()
                        reverted += 1
                elif action == "move":
                    if dest_sorted.exists() and not src_orig.exists():
                        src_orig.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(dest_sorted), str(src_orig))
                        reverted += 1

            HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
            self._last_explorer_target = ""
            done_lbl = "1 Eintrag wurde" if reverted == 1 else f"{reverted} Einträge wurden"
            self.show_notice(
                "Rückgängig abgeschlossen",
                f"{done_lbl} erfolgreich in den vorherigen Zustand zurückgesetzt.",
                kind="success",
            )
            self.show_step1()

        except Exception as e:
            self.show_notice("Fehler beim Zurücksetzen", "Die Aktion konnte nicht rückgängig gemacht werden.", str(e), kind="error")

    @staticmethod
    def _resolve_explorer_target_dir(dest_base: Path, created_items: list[dict]) -> Path:
        """Ermittelt den konkreten Zielordner (z. B. 07_Archiv_Historie/04_Bilder_Fotos/Spencer) für Explorer-Start & Anzeige."""
        dest_dirs = [Path(it["dest"]).parent for it in created_items if Path(it["dest"]).parent.exists()]
        if not dest_dirs:
            return dest_base
        unique_dirs = list(dict.fromkeys(dest_dirs))
        if len(unique_dirs) == 1:
            return unique_dirs[0]
        try:
            common = Path(os.path.commonpath([str(d) for d in unique_dirs]))
            base_res = dest_base.resolve()
            common_res = common.resolve()
            if (base_res == common_res or base_res in common_res.parents) and common.exists():
                return common
        except Exception:
            pass
        return dest_base

    def open_target_explorer(self, path: str | Path | None = None):
        target = str(path or getattr(self, "_last_explorer_target", "") or self.target_dir.get()).strip()
        if not target or not os.path.exists(target):
            target = self.target_dir.get().strip()
        if not target or not os.path.exists(target):
            try:
                p_parent = str(Path(target).parent)
                if os.path.exists(p_parent):
                    target = p_parent
            except Exception:
                pass
        if os.name == "nt" and target and os.path.exists(target):
            norm_target = os.path.normpath(os.path.abspath(target))
            opened = False
            try:
                import ctypes
                rc = ctypes.windll.shell32.ShellExecuteW(None, "explore", norm_target, None, None, 1)
                if rc and int(rc) > 32:
                    opened = True
            except Exception:
                opened = False
            if not opened:
                try:
                    subprocess.Popen(f'explorer.exe "{norm_target}"')
                except Exception as _err:
                    print(f"Explorer-Fehler: {_err}")

    def _apply_startup_source(self, src_path: str, tgt_path: str | None = None):
        norm_src = os.path.normpath(str(src_path).strip())
        if not norm_src:
            return
        self.source_dir.set(norm_src)
        if tgt_path and str(tgt_path).strip():
            self.target_mode = "custom"
            self.target_dir.set(os.path.normpath(str(tgt_path).strip()))
            self.update_target_mode_ui()
        else:
            p = Path(norm_src)
            if getattr(self, "target_mode", "adjacent") == "subfolder":
                self.set_target_subfolder()
            elif getattr(self, "target_mode", "adjacent") == "same":
                self.set_target_same()
            else:
                self.target_mode = "adjacent"
                self.target_dir.set(str(p.parent / f"{p.name}_01"))
                self.update_target_mode_ui()
        if is_onedrive_dir(Path(norm_src)):
            self.include_onedrive_var.set(True)

    def load_handoff_file(self, handoff_path: str | Path):
        """Lädt übergebene Dateien aus Datei-Suchmaschine Pro direkt in Schritt 2 (Vorschau)."""
        hf = Path(handoff_path)
        if not hf.is_file():
            self.show_notice("Übergabedatei fehlt", "Die Übergabeliste konnte nicht gelesen werden.", str(hf), kind="error")
            return
        try:
            payload = json.loads(hf.read_text(encoding="utf-8"))
        except Exception as exc:
            self.show_notice("Übergabefehler", "Die Übergabedatei ist ungültig.", str(exc), kind="error")
            return

        raw_paths = payload.get("paths") or []
        valid_paths = [Path(p) for p in raw_paths if p and os.path.exists(str(p))]
        if not valid_paths:
            self.show_notice("Keine Dateien vorhanden", "Keine der übergebenen Dateien wurde auf dem Datenträger gefunden.", kind="warning")
            return

        src_dir = str(payload.get("source_dir") or "").strip()
        if not src_dir:
            src_dir = str(valid_paths[0].parent)
        tgt_dir = str(payload.get("target_dir") or "").strip()
        self._apply_startup_source(src_dir, tgt_dir or None)
        if any(is_onedrive_dir(p if p.is_dir() else p.parent) for p in valid_paths):
            self.include_onedrive_var.set(True)
        handoff_theme = str(payload.get("theme") or "").strip().lower()
        if handoff_theme in ("light", "dark") and handoff_theme != self.theme_mode:
            self.apply_theme(handoff_theme, save=True)
        self.generate_preview(explicit_files=valid_paths, handoff_label="Datei-Suchmaschine")
        try:
            self.after(60, lambda: bring_window_to_front(self))
        except Exception:
            pass

    def _start_live_handoff_watcher(self):
        """Registriert dieses offene Fenster für erneute Übergaben aus Datei-Suchmaschine Pro (kein doppeltes Fenster)."""
        import tempfile
        import atexit
        tmp_dir = Path(tempfile.gettempdir())
        self._live_pid_file = tmp_dir / "7ordner_pro_live_pid.json"
        self._live_handoff_file = tmp_dir / "7ordner_pro_live_handoff.json"
        self._my_pid = os.getpid()

        def _write_pid():
            try:
                self._live_pid_file.write_text(
                    json.dumps({"pid": self._my_pid, "ts": time.time()}),
                    encoding="utf-8",
                )
            except Exception:
                pass

        def _cleanup_pid():
            try:
                if self._live_pid_file.exists():
                    data = json.loads(self._live_pid_file.read_text(encoding="utf-8"))
                    if data.get("pid") == self._my_pid:
                        self._live_pid_file.unlink(missing_ok=True)
            except Exception:
                pass

        _write_pid()
        atexit.register(_cleanup_pid)
        self._last_pid_heartbeat = time.monotonic()
        self.after(350, self._poll_live_handoff)

    def _poll_live_handoff(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        try:
            now = time.monotonic()
            if (now - getattr(self, "_last_pid_heartbeat", 0.0)) >= 5.0:
                self._last_pid_heartbeat = now
                self._live_pid_file.write_text(
                    json.dumps({"pid": self._my_pid, "ts": time.time()}),
                    encoding="utf-8",
                )
            hf = getattr(self, "_live_handoff_file", None)
            if hf and hf.is_file():
                claimed = hf.with_name(f"7ordner_pro_claimed_{int(time.time() * 1000)}.json")
                try:
                    os.replace(str(hf), str(claimed))
                except OSError:
                    claimed = None
                if claimed and claimed.is_file():
                    try:
                        payload = json.loads(claimed.read_text(encoding="utf-8"))
                        if payload.get("paths"):
                            self.load_handoff_file(claimed)
                        elif payload.get("folder_path"):
                            f_dir = str(payload.get("folder_path") or "").strip()
                            t_dir = str(payload.get("target_dir") or "").strip()
                            if f_dir and os.path.isdir(f_dir):
                                self._apply_startup_source(f_dir, t_dir or None)
                                handoff_theme = str(payload.get("theme") or "").strip().lower()
                                if handoff_theme in ("light", "dark") and handoff_theme != self.theme_mode:
                                    self.apply_theme(handoff_theme, save=True)
                                self.generate_preview()
                                self.after(60, lambda: bring_window_to_front(self))
                    finally:
                        try:
                            claimed.unlink(missing_ok=True)
                        except Exception:
                            pass
        except Exception:
            pass
        try:
            self.after(350, self._poll_live_handoff)
        except Exception:
            pass

    def open_in_suchmaschine(self, target_path: str | Path | None = None):
        """Startet Datei-Suchmaschine Pro bei Bedarf im Hintergrund und öffnet optional den Zielordner."""
        raw_target = str(target_path).strip() if target_path else ""
        if raw_target and not os.path.exists(raw_target):
            raw_target = ""

        def _worker():
            res = open_path_in_suchmaschine(raw_target or None, auto_start=True)
            if not res.get("success"):
                err_msg = res.get("error") or "Datei-Suchmaschine konnte nicht geöffnet werden."
                try:
                    self.after(0, lambda m=err_msg: self.show_notice("Datei-Suchmaschine nicht gefunden", m, kind="error"))
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    def _centered_geometry(self, width: int, height: int) -> str:
        """Berechnet eine mittig auf dem Bildschirm zentrierte Fenster-Geometrie."""
        sw = max(self.winfo_screenwidth(), 1024)
        sh = max(self.winfo_screenheight(), 720)
        w = max(980, min(width, max(980, sw - 40)))
        h = max(600, min(height, max(600, sh - 80)))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 20)
        return f"{w}x{h}+{x}+{y}"

    def _restore_window_geometry(self):
        """Startet beim ersten Start mittig und in voller Groesse; stellt sonst gespeicherte Groesse/Position wieder her."""
        sw = max(self.winfo_screenwidth(), 1024)
        sh = max(self.winfo_screenheight(), 720)
        default_geo = self._centered_geometry(min(int(sw * 0.88), 1480), min(int(sh * 0.88), 920))
        self._last_normal_geometry = default_geo

        if WINDOW_STATE_FILE.exists():
            try:
                data = json.loads(WINDOW_STATE_FILE.read_text(encoding="utf-8"))
                saved_geo = data.get("geometry") if isinstance(data, dict) else None
                saved_state = data.get("state") if isinstance(data, dict) else "zoomed"

                m = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", str(saved_geo or "").strip())
                if m:
                    w = max(980, min(int(m.group(1)), sw))
                    h = max(600, min(int(m.group(2)), sh))
                    x = int(m.group(3))
                    y = int(m.group(4))
                    # Nur gespeicherte Position uebernehmen, wenn Fenster noch sichtbar auf dem Bildschirm liegt
                    if -w + 150 <= x <= sw - 150 and 0 <= y <= sh - 100:
                        resolved_geo = f"{w}x{h}+{x}+{y}"
                    else:
                        resolved_geo = self._centered_geometry(w, h)
                else:
                    resolved_geo = default_geo

                self._last_normal_geometry = resolved_geo
                self.geometry(resolved_geo)
                if saved_state == "zoomed":
                    self.after(0, lambda: self.state("zoomed"))
                return
            except Exception:
                pass

        # Erster Start: Mittige Basisgroesse + volle Bildschirmgroesse (maximiert)
        self.geometry(default_geo)
        self.after(0, lambda: self.state("zoomed"))

    def _on_window_configure(self, event):
        """Merkt sich laufend die letzte normale Fenstergroesse und -position."""
        if event.widget is self:
            try:
                if self.state() == "normal":
                    self._last_normal_geometry = self.geometry()
            except Exception:
                pass

    def _restore_app_state(self):
        """Stellt beim Start zuletzt genutzte Ordner, Filter-Optionen und Tabellen-Spalten wieder her."""
        if not WINDOW_STATE_FILE.exists():
            return
        self._restoring_state = True
        try:
            data = json.loads(WINDOW_STATE_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            saved_src = str(data.get("source_dir") or "").strip()
            saved_dest = str(data.get("target_dir") or "").strip()
            if saved_src:
                self.source_dir.set(saved_src)
            if saved_dest:
                self.target_dir.set(saved_dest)
            saved_tmode = data.get("target_mode")
            if saved_tmode in ("adjacent", "subfolder", "same", "usb", "onedrive", "custom"):
                self.target_mode = saved_tmode
            saved_act = data.get("action_mode")
            if saved_act in ("copy", "move"):
                self.action_mode.set(saved_act)
            # Dateifilter-Zustand wiederherstellen (falls spezifischer Filter gewählt war, diesen wiederherstellen - niemals beide gleichzeitig!)
            saved_f_img = bool(data.get("filter_images", False))
            saved_f_txt = bool(data.get("filter_text_docs", False))
            saved_f_off = bool(data.get("filter_office", False))
            saved_f_med = bool(data.get("filter_media", data.get("include_media", False)))
            saved_f_inst = bool(data.get("filter_installers", data.get("include_installers", False)))
            saved_f_all = data.get("filter_all")

            has_specific = (saved_f_img or saved_f_txt or saved_f_off or saved_f_med or saved_f_inst)
            if has_specific and saved_f_all is not True:
                self.filter_all_var.set(False)
                self.filter_images_var.set(saved_f_img)
                self.filter_text_docs_var.set(saved_f_txt)
                self.filter_office_var.set(saved_f_off)
                self.filter_media_var.set(saved_f_med)
                self.filter_installers_var.set(saved_f_inst)
                self.include_media_var.set(saved_f_med)
                self.include_installers_var.set(saved_f_inst)
                self.file_filter_mode.set("custom")
            else:
                self.filter_all_var.set(True)
                self.filter_images_var.set(False)
                self.filter_text_docs_var.set(False)
                self.filter_office_var.set(False)
                self.filter_media_var.set(False)
                self.filter_installers_var.set(False)
                self.include_media_var.set(False)
                self.include_installers_var.set(False)
                self.file_filter_mode.set("all")
            self.update_file_filter_ui()
            saved_submode = data.get("subfolder_mode")
            if saved_submode in ("split", "whole", "ignore"):
                self.subfolder_mode.set(saved_submode)
            elif "recursive" in data:
                self.subfolder_mode.set("split" if data.get("recursive") else "ignore")
            self.recursive_var.set(self.subfolder_mode.get() == "split")
            self.update_subfolder_mode_ui()
            # OneDrive soll beim Start NIEMALS aktiv sein (immer inaktiv/False)
            self.include_onedrive_var.set(False)
            if "exclude_system_dirs" in data or "network_guard" in data:
                saved_sys = bool(data.get("exclude_system_dirs", True))
                saved_net = bool(data.get("network_guard", True))
                sig = str(data.get("guard_sig") or "")
                if verify_guard_state(saved_sys, saved_net, sig):
                    self.exclude_system_dirs_var.set(saved_sys)
                    self.network_guard_var.set(saved_net)
                else:
                    # Schutz bei unbefugter manueller Änderung von window_state.json wieder erzwingen
                    self.exclude_system_dirs_var.set(True)
                    self.network_guard_var.set(True)
            self.update_extra_type_ui()
            if "date_from" in data:
                self.date_from_var.set(str(data.get("date_from") or "").strip())
            if "date_to" in data:
                self.date_to_var.set(str(data.get("date_to") or "").strip())
            if "keyword_filter" in data:
                self.keyword_filter_var.set(str(data.get("keyword_filter") or "").strip())
            if "min_file_size" in data:
                self.min_size_var.set(str(data.get("min_file_size") or "").strip())
            self.update_date_preset_ui()
            self.update_size_preset_ui()

            # Spaltenreihenfolge & individuelle Spaltenbreiten wiederherstellen
            saved_order = data.get("column_order")
            if isinstance(saved_order, list) and saved_order:
                valid_order = [c for c in saved_order if c in self.col_keys]
                if "items_count" not in valid_order:
                    if "size" in valid_order:
                        s_idx = valid_order.index("size")
                        valid_order.insert(s_idx + 1, "items_count")
                    else:
                        valid_order.append("items_count")
                for c in self.col_keys:
                    if c not in valid_order:
                        valid_order.append(c)
                if valid_order:
                    self.tree["displaycolumns"] = tuple(valid_order)

            self._custom_col_widths = bool(data.get("custom_col_widths", False))
            saved_widths = data.get("column_widths")
            if self._custom_col_widths and isinstance(saved_widths, dict):
                for c in self.col_keys:
                    w = saved_widths.get(c)
                    if isinstance(w, int) and 35 <= w <= 900:
                        self.col_widths[c] = w
                        self.tree.column(c, width=w, minwidth=35, stretch=False)

            if data.get("dup_original_strategy") in {k for k, _ in DUP_ORIGINAL_STRATEGIES}:
                self.dup_original_strategy = data["dup_original_strategy"]
                self._update_dup_strategy_button()

            saved_pw = data.get("preview_pane_width")
            if isinstance(saved_pw, int) and 280 <= saved_pw <= 1100:
                self._preview_pane_width = saved_pw

            if data.get("preview_visible"):
                self.toggle_preview_pane(True)
            saved_theme = str(data.get("theme") or "").strip().lower()
            if not self._startup_theme and saved_theme in ("light", "dark"):
                self.theme_mode = saved_theme

            saved_recent = data.get("recent_target_folders")
            if isinstance(saved_recent, list) and saved_recent:
                self.recent_target_folders = []
                seen_paths = set()
                for item in saved_recent:
                    if isinstance(item, dict) and item.get("path"):
                        p_norm = os.path.normpath(str(item["path"]).strip())
                        if p_norm.lower() not in seen_paths:
                            seen_paths.add(p_norm.lower())
                            self.recent_target_folders.append({
                                "path": p_norm,
                                "pinned": bool(item.get("pinned", False)),
                                "label": str(item.get("label") or os.path.basename(p_norm.rstrip("\\/")) or p_norm),
                                "timestamp": float(item.get("timestamp", 0.0)),
                            })
                    if len(self.recent_target_folders) >= 5:
                        break
            else:
                self.recent_target_folders = []
                t_init = self.target_dir.get().strip()
                if t_init:
                    self.add_recent_target_folder(t_init)
            self.update_recent_target_buttons()
        except Exception:
            pass
        finally:
            self._restoring_state = False

    def _save_app_state(self):
        """Speichert Fenstergroesse, Position, Spaltenlayout sowie zuletzt genutzte Ordner und Optionen."""
        try:
            cur_state = self.state()
            geo = self.geometry() if cur_state == "normal" else (self._last_normal_geometry or self.geometry())
            disp_cols = list(self.get_current_display_columns())
            cur_widths = {}
            for c in self.col_keys:
                try:
                    cur_widths[c] = int(self.tree.column(c, "width"))
                except Exception:
                    cur_widths[c] = int(self.col_widths.get(c, 100))
            payload = {
                "state": "zoomed" if cur_state == "zoomed" else "normal",
                "geometry": geo,
                "source_dir": self.source_dir.get().strip(),
                "target_dir": self.target_dir.get().strip(),
                "target_mode": self.target_mode,
                "action_mode": self.action_mode.get(),
                "file_filter_mode": self.file_filter_mode.get(),
                "filter_all": bool(self.filter_all_var.get()),
                "filter_images": bool(self.filter_images_var.get()),
                "filter_text_docs": bool(self.filter_text_docs_var.get()),
                "filter_office": bool(self.filter_office_var.get()),
                "filter_media": bool(self.filter_media_var.get()),
                "filter_installers": bool(self.filter_installers_var.get()),
                "subfolder_mode": self.subfolder_mode.get(),
                "recursive": bool(self.subfolder_mode.get() == "split"),
                "include_installers": bool(self.include_installers_var.get()),
                "include_media": bool(self.include_media_var.get()),
                "include_onedrive": bool(self.include_onedrive_var.get()),
                "exclude_system_dirs": bool(self.exclude_system_dirs_var.get()),
                "network_guard": bool(self.network_guard_var.get()),
                "guard_sig": sign_guard_state(
                    bool(self.exclude_system_dirs_var.get()),
                    bool(self.network_guard_var.get()),
                ),
                "date_from": self.date_from_var.get().strip(),
                "date_to": self.date_to_var.get().strip(),
                "keyword_filter": self.keyword_filter_var.get().strip(),
                "min_file_size": self.min_size_var.get().strip(),
                "column_order": disp_cols,
                "custom_col_widths": bool(self._custom_col_widths),
                "column_widths": cur_widths,
                "dup_original_strategy": getattr(self, "dup_original_strategy", "clean_name"),
                "preview_visible": bool(self.preview_visible),
                "preview_pane_width": int(getattr(self, "_preview_pane_width", 380) or 380),
                "recent_target_folders": getattr(self, "recent_target_folders", [])[:5],
                "theme": getattr(self, "theme_mode", "light"),
            }
            WINDOW_STATE_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _on_close(self):
        """Speichert Groesse, Position, Ordner und Optionen beim Schliessen."""
        if getattr(self, "_step2_filter_after_id", None) is not None:
            try:
                self.after_cancel(self._step2_filter_after_id)
            except Exception:
                pass
            self._step2_filter_after_id = None
        if getattr(self, "_scan_cancel_event", None) is not None:
            try:
                self._scan_cancel_event.set()
            except Exception:
                pass
        self._save_app_state()
        self.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source", dest="source", default=None)
    parser.add_argument("--target", dest="target", default=None)
    parser.add_argument("--handoff", dest="handoff", default=None)
    parser.add_argument("--theme", dest="theme", default=None)
    cli_args, _unknown = parser.parse_known_args()
    app = App(
        startup_source=cli_args.source,
        startup_target=cli_args.target,
        startup_handoff=cli_args.handoff,
        startup_theme=cli_args.theme,
    )
    app.mainloop()
