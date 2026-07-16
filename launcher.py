"""
Creality Scan Timeline Cutter — desktop app.

Opens the tool in its OWN native window (Edge WebView2 via pywebview), no
browser involved. On launch it starts the FastAPI backend invisibly on a FREE
port (no more port-8000 collisions), and when the window is closed the server
is shut down with it — a Windows job object guarantees the child dies even if
the launcher is killed. Built into a windowed .exe with PyInstaller.
"""
import os
import sys
import time
import ctypes
import socket
import subprocess
import threading
import urllib.request

APP_NAME = "Creality Scan Timeline Cutter"
CREATE_NO_WINDOW = 0x08000000

LOADING_HTML = """
<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{height:100%;margin:0;background:#060810;color:#e2e8f0;
    font-family:'Segoe UI',sans-serif;display:flex;align-items:center;justify-content:center}
  .box{text-align:center}
  .spin{width:44px;height:44px;border:4px solid #1e293b;border-top-color:#00f2fe;
    border-radius:50%;margin:0 auto 18px;animation:r 0.9s linear infinite}
  @keyframes r{to{transform:rotate(360deg)}}
  h2{font-weight:600;margin:0 0 6px} p{color:#64748b;margin:0;font-size:14px}
</style></head><body><div class="box">
  <div class="spin"></div>
  <h2>Creality Scan Timeline Cutter</h2>
  <p>A szerver indul&hellip; / Starting server&hellip;</p>
</div></body></html>
"""

ERROR_HTML = """
<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{height:100%;margin:0;background:#060810;color:#e2e8f0;
    font-family:'Segoe UI',sans-serif;display:flex;align-items:center;justify-content:center}
  .box{text-align:center;max-width:460px;padding:0 24px}
  h2{color:#f87171;margin:0 0 10px} p{color:#94a3b8;line-height:1.6;font-size:14px}
</style></head><body><div class="box">
  <h2>A szerver nem tudott elindulni</h2>
  <p>Ellen&#337;rizd, hogy a program a projekt mapp&#225;j&#225;ban van-e
  (a <b>.venv</b> &#233;s a <b>backend</b> mapp&#225;k mellett), majd ind&#237;tsd &#250;jra.
  R&#233;szletek: <b>backend\\server.log</b></p>
</div></body></html>
"""


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def error_box(message):
    ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)


def free_port():
    """Ask the OS for a free TCP port so parallel/leftover instances never clash."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_kill_on_close_job():
    """A Windows job object that kills every process assigned to it the moment
    the job handle is closed — i.e. when THIS process exits for any reason.
    This is what guarantees no orphaned server is ever left behind."""
    k32 = ctypes.windll.kernel32
    job = k32.CreateJobObjectW(None, None)

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", ctypes.c_uint32),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32)]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [("ReadOperationCount", ctypes.c_uint64),
                    ("WriteOperationCount", ctypes.c_uint64),
                    ("OtherOperationCount", ctypes.c_uint64),
                    ("ReadTransferCount", ctypes.c_uint64),
                    ("WriteTransferCount", ctypes.c_uint64),
                    ("OtherTransferCount", ctypes.c_uint64)]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    JobObjectExtendedLimitInformation = 9
    k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                                ctypes.byref(info), ctypes.sizeof(info))
    return job


def start_server(root, port):
    python = os.path.join(root, ".venv", "Scripts", "python.exe")
    backend = os.path.join(root, "backend")
    if not os.path.exists(python) or not os.path.isdir(backend):
        return None
    log = open(os.path.join(backend, "server.log"), "w")
    # python.exe (not pythonw) so uvicorn has real stdout/stderr; the console
    # stays hidden via CREATE_NO_WINDOW.
    server = subprocess.Popen(
        [python, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
         "--port", str(port)],
        cwd=backend, creationflags=CREATE_NO_WINDOW,
        stdout=log, stderr=log, stdin=subprocess.DEVNULL,
    )
    # tie the child's life to ours
    job = make_kill_on_close_job()
    ctypes.windll.kernel32.AssignProcessToJobObject(job, int(server._handle))
    server._job = job  # keep the handle alive for the process' lifetime
    return server


def server_ready(port):
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/raw/available", timeout=1):
            return True
    except Exception:
        return False


def main():
    root = base_dir()
    port = free_port()
    server = start_server(root, port)
    if server is None:
        error_box(
            "Nem talalom a program fajljait!\n\n"
            f"A '{APP_NAME}.exe' a projekt mappajaban kell legyen\n"
            "(a .venv es a backend mappak mellett).\n\n"
            "Ha parancsikont szeretnel az asztalra, ne az exe-t masold at,\n"
            "hanem jobb klikk > Kuldes > Asztal (parancsikon letrehozasa)."
        )
        return

    # Headless smoke test: verify the server comes up, then exit (no window).
    if os.environ.get("CUTTER_SMOKE_TEST") == "1":
        ok = False
        for _ in range(120):
            if server.poll() is not None:
                break
            if server_ready(port):
                ok = True
                break
            time.sleep(0.5)
        print(f"SMOKE port={port} ready={ok}")
        server.terminate()
        sys.exit(0 if ok else 1)

    import webview  # deferred: heavy import, not needed for the error paths

    window = webview.create_window(
        APP_NAME, html=LOADING_HTML,
        width=1440, height=900, min_size=(1100, 700),
        background_color="#060810",
    )

    def swap_to_app_when_ready():
        # large model libs (pymeshlab) take a few seconds to import
        for _ in range(120):
            if server.poll() is not None:
                window.load_html(ERROR_HTML)
                return
            if server_ready(port):
                window.load_url(f"http://127.0.0.1:{port}")
                return
            time.sleep(0.5)
        window.load_html(ERROR_HTML)

    threading.Thread(target=swap_to_app_when_ready, daemon=True).start()

    # blocks until the window is closed
    webview.start()

    # window closed -> stop the server (the job object is the safety net)
    if server.poll() is None:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    main()
