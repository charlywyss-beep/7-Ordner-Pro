import os
import re
import sys
import subprocess
from datetime import datetime

DEFAULT_REMOTE_URL = "https://github.com/charlywyss-beep/7-Ordner-Pro.git"
DEFAULT_REPO_NAME = "7-Ordner-Pro"
EXE_FILENAME = "7-Ordner-Pro.exe"
STATE_FILENAME = "window_state.json"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def find_repo_dir():
    """Findet den lokalen Projektordner mit .git oder die App-Basis."""
    candidates = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(exe_dir)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(here)
    candidates.append(os.getcwd())
    candidates.append(r"C:\Users\01\.gemini\antigravity\scratch\Ordner-Struktur")

    for c in candidates:
        if c and os.path.exists(os.path.join(c, ".git")):
            return c
    for c in candidates:
        if c and os.path.exists(os.path.join(c, "app.pyw")):
            return c
    return here


def _run_git(repo_dir, args, timeout=30):
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    kwargs = {
        "cwd": repo_dir,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "env": env,
    }
    if _NO_WINDOW:
        kwargs["creationflags"] = _NO_WINDOW
    return subprocess.run(["git"] + list(args), **kwargs)


def extract_version_tag(text):
    if not text:
        return ""
    m = re.search(r"v\d+\.\d+\.\d+", str(text), re.IGNORECASE)
    return m.group(0).lower() if m else ""


def parse_version_tuple(ver_str):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(ver_str or ""))
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in m.groups())


def filter_meaningful_dirty_lines(porcelain_output):
    """Ignoriert Laufzeit-Dateien wie window_state.json und Cache für den Sync-Status."""
    ignored_prefixes = ("__pycache__/", "build/", "dist/")
    ignored_names = {STATE_FILENAME, ".DS_Store", "Thumbs.db"}
    dirty = []
    for raw in (porcelain_output or "").splitlines():
        if not raw.strip():
            continue
        path = raw[3:].strip().strip('"').replace("\\", "/")
        if " -> " in path:
            path = path.split(" -> ", 1)[-1].strip().strip('"')
        if any(path.startswith(p) for p in ignored_prefixes) or path.endswith((".pyc", ".pyo")):
            continue
        if path in ignored_names or os.path.basename(path) in ignored_names:
            continue
        dirty.append(path)
    return dirty


def derive_github_urls(remote_url, branch="main"):
    clean = (remote_url or DEFAULT_REMOTE_URL).strip()
    base = clean[:-4] if clean.endswith(".git") else clean
    base = base.rstrip("/")
    repo_name = base.split("/")[-1] if "/" in base else DEFAULT_REPO_NAME
    branch_name = (branch or "main").strip() or "main"
    return {
        "new_repo_url": f"https://github.com/new?name={repo_name}",
        "raw_exe_url": f"{base}/raw/{branch_name}/{EXE_FILENAME}",
    }


def get_git_info(app_version="v1.0.48"):
    """Liest Git-Status, Versionsvergleich, Historie und Download-Links aus."""
    repo_dir = find_repo_dir()
    exe_path = os.path.join(repo_dir, EXE_FILENAME)
    exe_size_mb = ""
    if os.path.exists(exe_path):
        try:
            exe_size_mb = f"{os.path.getsize(exe_path) / (1024 * 1024):.1f} MB"
        except Exception:
            exe_size_mb = ""

    urls = derive_github_urls(DEFAULT_REMOTE_URL, "main")
    info = {
        "version": app_version,
        "repo_dir": repo_dir,
        "is_git": os.path.exists(os.path.join(repo_dir, ".git")),
        "remote_url": DEFAULT_REMOTE_URL,
        "branch": "main",
        "last_commit": "",
        "git_version": "",
        "remote_commit": "",
        "remote_version": "",
        "has_remote_ref": False,
        "ahead": 0,
        "behind": 0,
        "dirty": False,
        "dirty_files": [],
        "exe_size_mb": exe_size_mb,
        "local_is_newer": False,
        "sync_state": "no_git",
        "sync_title": "Noch kein lokales Git-Repository eingerichtet",
        "sync_detail": "Lege einmal Git lokal/auf GitHub an, um Updates zu synchronisieren.",
        "history": [],
        "new_repo_url": urls["new_repo_url"],
        "raw_exe_url": urls["raw_exe_url"],
    }

    if not info["is_git"]:
        return info

    try:
        res = _run_git(repo_dir, ["remote", "get-url", "origin"])
        if res.returncode == 0 and res.stdout.strip():
            info["remote_url"] = res.stdout.strip()

        res = _run_git(repo_dir, ["branch", "--show-current"])
        if res.returncode == 0 and res.stdout.strip():
            info["branch"] = res.stdout.strip()
        branch_name = info["branch"] or "main"

        urls = derive_github_urls(info["remote_url"], branch_name)
        info["new_repo_url"] = urls["new_repo_url"]
        info["raw_exe_url"] = urls["raw_exe_url"]

        res = _run_git(repo_dir, ["log", "-1", "--format=%h - %s • %ad", "--date=format:%d.%m.%y %H:%M"])
        if res.returncode == 0:
            info["last_commit"] = res.stdout.strip()

        res_sub = _run_git(repo_dir, ["log", "-1", "--format=%s"])
        if res_sub.returncode == 0:
            info["git_version"] = extract_version_tag(res_sub.stdout.strip())

        remote_ref = f"origin/{branch_name}"
        res_rem = _run_git(repo_dir, ["log", "-1", remote_ref, "--format=%h - %s • %ad", "--date=format:%d.%m.%y %H:%M"])
        if res_rem.returncode == 0 and res_rem.stdout.strip():
            info["has_remote_ref"] = True
            info["remote_commit"] = res_rem.stdout.strip()
            info["remote_version"] = extract_version_tag(res_rem.stdout.strip())

            res_lr = _run_git(repo_dir, ["rev-list", "--left-right", "--count", f"HEAD...{remote_ref}"])
            if res_lr.returncode == 0:
                parts = res_lr.stdout.strip().split()
                if len(parts) == 2:
                    info["ahead"] = int(parts[0])
                    info["behind"] = int(parts[1])

        res_st = _run_git(repo_dir, ["status", "--porcelain"])
        dirty_files = filter_meaningful_dirty_lines(res_st.stdout)
        info["dirty_files"] = dirty_files
        info["dirty"] = bool(dirty_files)

        res_hist = _run_git(repo_dir, ["log", "-n", "10", "--format=%H|%h|%s|%ad", "--date=format:%d.%m.%y %H:%M"])
        if res_hist.returncode == 0:
            hist = []
            for line in res_hist.stdout.splitlines():
                cols = line.split("|", 3)
                if len(cols) == 4:
                    hist.append({
                        "hash": cols[0].strip(),
                        "short": cols[1].strip(),
                        "subject": cols[2].strip(),
                        "date": cols[3].strip(),
                        "version": extract_version_tag(cols[2]) or cols[1].strip(),
                    })
            info["history"] = hist

        local_tuple = parse_version_tuple(app_version)
        if not info["has_remote_ref"]:
            info["local_is_newer"] = True
            info["sync_state"] = "local_ahead"
            info["sync_title"] = f"Noch nicht auf GitHub hochgeladen ({app_version})"
            info["sync_detail"] = (
                "Klicke oben rechts auf „Auf GitHub anlegen“, erstelle das Repo auf GitHub "
                "und klicke anschließend auf „Stand nach GitHub hochladen“."
            )
        else:
            ref_ver = info["remote_version"] or info["git_version"]
            remote_tuple = parse_version_tuple(ref_ver)
            has_local_changes = bool(dirty_files) or info["ahead"] > 0

            if local_tuple > remote_tuple:
                info["local_is_newer"] = True
                info["sync_state"] = "local_newer"
                info["sync_title"] = f"Lokale App-Version ({app_version}) ist höher als auf GitHub ({ref_ver or '?'})"
                info["sync_detail"] = (
                    f"{len(dirty_files)} geänderte Datei(en) bzw. noch nicht auf GitHub hochgeladen. "
                    "Klicke auf „Stand nach GitHub hochladen“, um Code & EXE auf GitHub zu sichern."
                )
            elif has_local_changes:
                info["local_is_newer"] = True
                info["sync_state"] = "local_ahead"
                info["sync_title"] = f"Lokale Änderungen noch nicht auf GitHub hochgeladen ({app_version})"
                details = []
                if dirty_files:
                    details.append(f"{len(dirty_files)} lokal geänderte Datei(en)")
                if info["ahead"] > 0:
                    details.append(f"{info['ahead']} ungesicherte Commit(s)")
                info["sync_detail"] = " • ".join(details) + ". Mit „Stand nach GitHub hochladen“ sichern."
            elif info["behind"] > 0:
                info["sync_state"] = "remote_newer"
                info["sync_title"] = f"Neuerer Stand auf GitHub verfügbar ({info['remote_version'] or str(info['behind']) + ' Commit(s)'})"
                info["sync_detail"] = (
                    f"GitHub ist {info['behind']} Commit(s) voraus. "
                    "Klicke auf „Jetzt von GitHub aktualisieren“, um den Stand zu laden."
                )
            else:
                info["sync_state"] = "in_sync"
                info["sync_title"] = f"Auf dem neuesten Stand mit GitHub ({app_version})"
                info["sync_detail"] = "Lokale Version und GitHub-Repository sind synchron."
    except Exception as e:
        info["error"] = str(e)

    return info


def git_set_remote(remote_url, app_version="v1.0.48"):
    repo_dir = find_repo_dir()
    url = (remote_url or "").strip()
    if not url:
        return {"success": False, "error": "Bitte eine gültige GitHub-URL angeben."}
    try:
        _run_git(repo_dir, ["remote", "remove", "origin"])
        res = _run_git(repo_dir, ["remote", "add", "origin", url])
        if res.returncode == 0:
            return {"success": True, "remote_url": url, "git_info": get_git_info(app_version)}
        return {"success": False, "error": res.stderr.strip() or res.stdout.strip()}
    except Exception as e:
        return {"success": False, "error": str(e)}


def git_push_upload(app_version="v1.0.48"):
    """Erstellt bei Bedarf einen Commit (ohne window_state.json) und überträgt ihn nach GitHub."""
    repo_dir = find_repo_dir()
    logs = []
    try:
        res_branch = _run_git(repo_dir, ["branch", "--show-current"])
        branch = (res_branch.stdout or "").strip() or "main"

        _run_git(repo_dir, ["rm", "-r", "--cached", "--ignore-unmatch", "__pycache__"])

        add_res = _run_git(repo_dir, ["add", "-A"])
        if add_res.returncode != 0:
            return {
                "success": False,
                "output": (add_res.stdout + "\n" + add_res.stderr).strip(),
                "message": "Staging (git add) fehlgeschlagen.",
                "git_info": get_git_info(app_version),
            }
        _run_git(repo_dir, ["reset", "-q", "HEAD", "--", STATE_FILENAME])

        diff_res = _run_git(repo_dir, ["diff", "--cached", "--quiet"])
        if diff_res.returncode != 0:
            commit_msg = f"{app_version}: Sync & Build nach GitHub ({datetime.now().strftime('%d.%m.%Y %H:%M')})"
            c_res = _run_git(repo_dir, ["commit", "-m", commit_msg])
            c_out = (c_res.stdout + "\n" + c_res.stderr).strip()
            if c_out:
                logs.append(c_out)
            if c_res.returncode != 0:
                return {
                    "success": False,
                    "output": "\n".join(logs),
                    "message": "Commit fehlgeschlagen: " + (c_res.stderr.strip() or c_res.stdout.strip()),
                    "git_info": get_git_info(app_version),
                }

        res = _run_git(repo_dir, ["push", "-u", "origin", branch], timeout=90)
        p_out = (res.stdout + "\n" + res.stderr).strip()
        if p_out:
            logs.append(p_out)

        ok = res.returncode == 0
        msg = f"Stand ({app_version}) erfolgreich nach GitHub hochgeladen!" if ok else p_out
        if not ok and "Repository not found" in p_out:
            msg = "Das Repository existiert auf GitHub noch nicht. Bitte zuerst auf „Auf GitHub anlegen“ klicken und danach erneut hochladen."

        return {
            "success": ok,
            "output": "\n\n".join([x for x in logs if x]).strip() or msg,
            "message": msg,
            "git_info": get_git_info(app_version),
        }
    except Exception as e:
        return {"success": False, "error": str(e), "message": str(e), "git_info": get_git_info(app_version)}


def git_pull_update(app_version="v1.0.48"):
    """Lädt den neuesten Stand von GitHub herunter (window_state.json bleibt erhalten)."""
    repo_dir = find_repo_dir()
    state_path = os.path.join(repo_dir, STATE_FILENAME)
    saved_state = None
    if os.path.exists(state_path):
        with open(state_path, "rb") as f:
            saved_state = f.read()

    try:
        res_branch = _run_git(repo_dir, ["branch", "--show-current"])
        branch = (res_branch.stdout or "").strip() or "main"

        res = _run_git(repo_dir, ["pull", "origin", branch], timeout=45)
        output = (res.stdout + "\n" + res.stderr).strip()
        ok = res.returncode == 0

        if not ok:
            res2 = _run_git(repo_dir, ["pull"], timeout=45)
            output = (res2.stdout + "\n" + res2.stderr).strip()
            ok = res2.returncode == 0

        if saved_state is not None:
            with open(state_path, "wb") as f:
                f.write(saved_state)

        msg = "Update erfolgreich eingespielt!"
        if "Already up to date" in output or "Already up-to-date" in output:
            msg = "Bereits auf dem neuesten Stand! Keine neuen Änderungen auf GitHub vorhanden."
        elif not ok and "Repository not found" in output:
            msg = "Das Repository wurde auf GitHub noch nicht angelegt."

        return {
            "success": ok,
            "output": output or msg,
            "message": msg if ok else (output or msg),
            "git_info": get_git_info(app_version),
        }
    except Exception as e:
        return {"success": False, "error": str(e), "message": str(e), "git_info": get_git_info(app_version)}


def git_rollback_version(commit_hash, app_version="v1.0.48"):
    """Setzt sicher auf einen früheren Commit zurück (mit Git-Stash-Backup & window_state.json-Erhalt)."""
    repo_dir = find_repo_dir()
    commit_hash = (commit_hash or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit_hash):
        return {"success": False, "error": "Ungültige Commit-ID.", "message": "Ungültige Commit-ID."}

    try:
        chk = _run_git(repo_dir, ["cat-file", "-t", commit_hash])
        if chk.returncode != 0 or chk.stdout.strip() != "commit":
            return {"success": False, "error": f"Commit {commit_hash} nicht gefunden."}

        state_path = os.path.join(repo_dir, STATE_FILENAME)
        saved_state = None
        if os.path.exists(state_path):
            with open(state_path, "rb") as f:
                saved_state = f.read()

        logs = []
        dirty = filter_meaningful_dirty_lines(_run_git(repo_dir, ["status", "--porcelain"]).stdout)
        if dirty:
            st_msg = f"Sicherheitskopie vor Retour ({datetime.now().strftime('%d.%m.%Y %H:%M:%S')})"
            st_res = _run_git(repo_dir, ["stash", "push", "-u", "-m", st_msg])
            s_out = (st_res.stdout + "\n" + st_res.stderr).strip()
            if s_out:
                logs.append("Backup erstellt: " + s_out)

        rst_res = _run_git(repo_dir, ["reset", "--hard", commit_hash])
        r_out = (rst_res.stdout + "\n" + rst_res.stderr).strip()
        if r_out:
            logs.append(r_out)

        if saved_state is not None:
            with open(state_path, "wb") as f:
                f.write(saved_state)

        if rst_res.returncode != 0:
            return {"success": False, "output": "\n".join(logs), "error": "Zurücksetzen fehlgeschlagen."}

        return {
            "success": True,
            "output": "\n".join(logs),
            "message": f"Erfolgreich auf Version {commit_hash[:7]} zurückgesetzt (Einstellungen behalten).",
            "git_info": get_git_info(app_version),
        }
    except Exception as e:
        return {"success": False, "error": str(e), "message": str(e)}
