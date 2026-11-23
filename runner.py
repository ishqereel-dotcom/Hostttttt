"""Spawns approved bots as subprocesses and keeps them alive."""
import os, sys, shutil, zipfile, subprocess, threading, time, collections
from pathlib import Path
from . import config

_procs: dict[int, subprocess.Popen] = {}
_logs: dict[int, collections.deque] = {}
_lock = threading.Lock()

def log_lines(bot_id: int) -> list[str]:
    with _lock:
        return list(_logs.get(bot_id, []))

def _push(bot_id: int, line: str):
    with _lock:
        buf = _logs.setdefault(bot_id, collections.deque(maxlen=config.LOG_LINES))
        buf.append(line.rstrip())

def extract_upload(saved: Path, bot_id: int) -> Path:
    """Unzip into a per-bot folder if needed; return the folder to run from."""
    workdir = config.UPLOAD_DIR / f"bot_{bot_id}"
    workdir.mkdir(parents=True, exist_ok=True)
    if saved.suffix.lower() == ".zip":
        with zipfile.ZipFile(saved) as z:
            for m in z.namelist():
                if m.startswith("__MACOSX"):
                    continue
                z.extract(m, workdir)
        saved.unlink(missing_ok=True)
        # flatten single top folder
        entries = [e for e in workdir.iterdir()]
        if len(entries) == 1 and entries[0].is_dir():
            inner = entries[0]
            for f in inner.iterdir():
                shutil.move(str(f), str(workdir / f.name))
            inner.rmdir()
    else:
        shutil.copy(saved, workdir / saved.name)
        saved.unlink(missing_ok=True)
    return workdir

def pick_entry(workdir: Path) -> str | None:
    for name in ("main.py", "bot.py", "app.py", "run.py"):
        if (workdir / name).exists():
            return name
    pys = sorted(workdir.glob("*.py"))
    return pys[0].name if pys else None

def build_env(bot: dict) -> dict:
    env = os.environ.copy()
    env.update({
        "BOT_TOKEN": bot["bot_token"],
        "API_ID": bot["api_id"] or "",
        "API_HASH": bot["api_hash"] or "",
        "SESSION_STRING": bot["session_string"] or "",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    return env

def _pump(bot_id: int, proc: subprocess.Popen):
    for raw in iter(proc.stdout.readline, b""):
        _push(bot_id, raw.decode("utf-8", "replace"))
    proc.wait()
    _push(bot_id, f"[runner] stopped with exit code {proc.returncode}")
    with _lock:
        _procs.pop(bot_id, None)

def start(bot: dict) -> tuple[bool, str]:
    bid = bot["id"]
    with _lock:
        if bid in _procs and _procs[bid].poll() is None:
            return True, "already running"
    workdir = Path(bot["file_path"]).parent
    entry = bot["entry_file"]
    if not (workdir / entry).exists():
        return False, f"entry file {entry} missing"
    log = open(workdir / "bot.log", "ab")
    try:
        proc = subprocess.Popen(
            [sys.executable, entry],
            cwd=str(workdir),
            env=build_env(bot),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception as e:
        return False, str(e)
    with _lock:
        _procs[bid] = proc
    _push(bid, f"[runner] started pid {proc.pid} -> {entry}")
    threading.Thread(target=_pump, args=(bid, proc), daemon=True).start()
    return True, "started"

def stop(bot_id: int) -> bool:
    with _lock:
        proc = _procs.pop(bot_id, None)
    if not proc:
        return False
    for sig, wait in ((15, 6), (9, 3)):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except ProcessLookupError:
            return True
        try:
            proc.wait(timeout=wait)
            break
        except subprocess.TimeoutExpired:
            continue
    _push(bot_id, "[runner] stopped by admin")
    return True

def alive(bot_id: int) -> bool:
    with _lock:
        p = _procs.get(bot_id)
    return bool(p and p.poll() is None)

def stop_all():
    for bid in list(_procs.keys()):
        stop(bid)