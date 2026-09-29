"""Persistent PTY shell sessions — a REAL interactive terminal.

Each session spawns the system shell (`/bin/bash`, fallback `sh`) on a
pseudo-terminal via `pty.fork()`. The shell is a genuine child process with a
TTY, so programs that need one behave exactly as on a local machine:
`ssh` prompts for host keys, `git clone` prompts for credentials,
`sudo` asks for a password, `vim` full-screens — everything works.

Threading model: a daemon thread reads the master fd continuously and appends
decoded bytes to a bounded scrollback buffer. Browser clients attach via SSE
(`GET /api/admin/terminal/pty/stream?session=…`) which streams everything from
`attach_offset` onward, and send keystrokes via
`POST /api/admin/terminal/pty/input {session, data}`.

All sessions live in one global registry and are cleaned up on app shutdown.
"""
from __future__ import annotations

import fcntl
import os
import pty
import select
import shlex
import signal
import struct
import subprocess
import threading
import time

from .config import PROJECT_ROOT

SESSION_TTL_S = 30 * 60          # idle reaper
SCROLLBACK_BYTES = 512 * 1024    # per-session history kept for late attachers
READ_CHUNK = 4096


class PtySession:
    def __init__(self, session_id: str, cwd: str, cols: int = 120, rows: int = 32):
        self.id = session_id
        self.created_at = time.time()
        self.last_activity = time.time()
        self.lock = threading.Lock()
        self.buffer = bytearray()
        self.subscribers: set[int] = set()  # wfileno of SSE response objects
        self.closed = False
        self.cwd = cwd if os.path.isdir(cwd) else str(PROJECT_ROOT)
        self.cols = max(20, min(500, int(cols or 120)))
        self.rows = max(5, min(200, int(rows or 32)))

        self.pid, self.master = pty.fork()
        if self.pid == 0:  # child — exec the shell
            try:
                os.chdir(self.cwd)
            except OSError:
                pass
            os.environ["TERM"] = os.environ.get("TERM") or "xterm-256color"
            os.environ.setdefault("HOME", "/root")
            os.environ.setdefault("USER", "root")
            os.environ["NOVA_GATEWAY"] = "1"
            for shell in ("/bin/bash", "/bin/sh"):
                try:
                    os.execv(shell, [shell, "-i"])
                except OSError:
                    continue
            os._exit(127)

        # Parent: size the pty and make reads non-blocking.
        self._resize_fd(self.cols, self.rows)
        flags = fcntl.fcntl(self.master, fcntl.F_GETFL)
        fcntl.fcntl(self.master, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        self.reader = threading.Thread(target=self._read_loop, daemon=True, name=f"pty-{session_id}")
        self.reader.start()

    # ------------------------------------------------------------------ #
    # IO
    # ------------------------------------------------------------------ #

    def _append(self, data: bytes) -> None:
        with self.lock:
            self.buffer.extend(data)
            if len(self.buffer) > SCROLLBACK_BYTES:
                del self.buffer[: len(self.buffer) - SCROLLBACK_BYTES]
            targets = list(self.subscribers)

        for wfileno in targets:
            try:
                os.write(wfileno, data)
            except OSError:
                with self.lock:
                    self.subscribers.discard(wfileno)

    def _read_loop(self) -> None:
        while not self.closed:
            try:
                ready, _, _ = select.select([self.master], [], [], 0.25)
            except (OSError, ValueError):
                break
            if not ready:
                if self._exited():
                    break
                continue
            try:
                data = os.read(self.master, READ_CHUNK)
            except BlockingIOError:
                continue
            except OSError:
                break
            if not data:
                break
            self.last_activity = time.time()
            self._append(data)
        self.closed = True
        # Let subscribers know the stream ended.
        self._append(b"\r\n\x1b[90m[session ended]\x1b[0m\r\n")

    def _exited(self) -> bool:
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            return True
        return pid == self.pid

    def write(self, data: str) -> None:
        """Feed keystrokes (raw, may include \r, \u0003 for Ctrl+C …)."""
        if self.closed:
            raise RuntimeError("session closed")
        self.last_activity = time.time()
        try:
            os.write(self.master, data.encode("utf-8", errors="replace"))
        except OSError as err:
            raise RuntimeError(f"session write failed: {err}") from err

    def resize(self, cols: int, rows: int) -> None:
        self.cols = max(20, min(500, int(cols or self.cols)))
        self.rows = max(5, min(200, int(rows or self.rows)))
        self._resize_fd(self.cols, self.rows)

    def _resize_fd(self, cols: int, rows: int) -> None:
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master, 1, winsize)  # TIOCSWINSZ
            os.kill(self.pid, signal.SIGWINCH)
        except (OSError, ValueError):
            pass

    def subscribe(self, wfileno: int, from_offset: int = 0) -> bytes:
        """Register an SSE response for live output; returns the backlog."""
        with self.lock:
            backlog = bytes(self.buffer[from_offset:]) if from_offset else bytes(self.buffer)
            self.subscribers.add(wfileno)
        return backlog

    def unsubscribe(self, wfileno: int) -> None:
        with self.lock:
            self.subscribers.discard(wfileno)

    def stop(self) -> None:
        if self.closed:
            return
        try:
            os.kill(self.pid, signal.SIGHUP)
        except OSError:
            pass
        time.sleep(0.1)
        if not self._exited():
            try:
                os.kill(self.pid, signal.SIGKILL)
            except OSError:
                pass
        self.closed = True
        try:
            os.close(self.master)
        except OSError:
            pass

    def snapshot(self, from_offset: int = 0) -> tuple[bytes, int]:
        with self.lock:
            return bytes(self.buffer[from_offset:]), len(self.buffer)

    def info(self) -> dict:
        return {
            "id": self.id,
            "pid": self.pid,
            "cwd": self.cwd,
            "closed": self.closed,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "cols": self.cols,
            "rows": self.rows,
            "buffer_size": len(self.buffer),
        }


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

_SESSIONS: dict[str, PtySession] = {}
_REG_LOCK = threading.Lock()
_REAPER_STARTED = False


def start_reaper() -> None:
    global _REAPER_STARTED
    if _REAPER_STARTED:
        return

    def reap() -> None:
        while True:
            time.sleep(60)
            now = time.time()
            with _REG_LOCK:
                dead = [sid for sid, s in _SESSIONS.items() if s.closed or now - s.last_activity > SESSION_TTL_S]
                for sid in dead:
                    sess = _SESSIONS.pop(sid)
                    sess.stop()

    threading.Thread(target=reap, daemon=True, name="pty-reaper").start()
    _REAPER_STARTED = True


def get_session(session_id: str) -> PtySession | None:
    with _REG_LOCK:
        return _SESSIONS.get(session_id)


def create_session(session_id: str, cwd: str = "", cols: int = 120, rows: int = 32) -> PtySession:
    start_reaper()
    with _REG_LOCK:
        old = _SESSIONS.get(session_id)
        if old is not None and not old.closed:
            return old
        if old is not None:
            old.stop()
        sess = PtySession(session_id, cwd or str(PROJECT_ROOT), cols, rows)
        _SESSIONS[session_id] = sess
        return sess


def stop_session(session_id: str) -> bool:
    with _REG_LOCK:
        sess = _SESSIONS.pop(session_id, None)
    if sess is None:
        return False
    sess.stop()
    return True


def stop_all() -> None:
    with _REG_LOCK:
        sessions = list(_SESSIONS.values())
        _SESSIONS.clear()
    for sess in sessions:
        sess.stop()


def list_sessions() -> list[dict]:
    with _REG_LOCK:
        return [s.info() for s in _SESSIONS.values()]


def shell_hint(cmd: str) -> str | None:
    """One-shot exec hint: TTY-requiring flows need the live terminal."""
    first = shlex.split(cmd)[0] if shlex.split(cmd) else ""
    if first in ("ssh", "sftp", "scp"):
        return (
            "ssh needs an interactive terminal for host-key/password prompts — "
            "open the Terminal tab and press “Live session”, then run it there."
        )
    if first == "git" and any(
        flag in cmd for flag in ("clone", "push", "pull", "fetch")
    ) and "https://" in cmd:
        return (
            "private/interactive git over HTTPS asks for credentials — run it in "
            "a Live session (Terminal tab) where the username/password prompt works, "
            "or use a token URL / SSH remote."
        )
    return None
