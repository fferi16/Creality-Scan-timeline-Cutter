"""
3D Scan Doctor launcher.

Starts the FastAPI backend invisibly (no console window), opens the app in the
default browser, and sits in the Windows system tray with a "Kilepes" option.
Built into a windowed .exe with PyInstaller, so no console ever appears.
"""
import os
import sys
import time
import ctypes
import subprocess
import urllib.request
import webbrowser

APP_URL = "http://127.0.0.1:8000"
CREATE_NO_WINDOW = 0x08000000


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def error_box(message):
    ctypes.windll.user32.MessageBoxW(None, message, "3D Scan Doctor", 0x10)


def server_running():
    try:
        with urllib.request.urlopen(APP_URL + "/docs", timeout=1):
            return True
    except Exception:
        return False


def make_tray_icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 62, 62], radius=14, fill=(14, 116, 244, 255))
    # white medical cross
    d.rectangle([26, 12, 38, 52], fill=(255, 255, 255, 255))
    d.rectangle([12, 26, 52, 38], fill=(255, 255, 255, 255))
    return img


def main():
    root = base_dir()

    if server_running():
        if os.environ.get("SCAN_DOCTOR_NO_BROWSER") != "1":
            webbrowser.open(APP_URL)
        return

    # Use python.exe (not pythonw) so the server has real stdout/stderr —
    # uvicorn crashes without them. CREATE_NO_WINDOW keeps it invisible.
    python = os.path.join(root, ".venv", "Scripts", "python.exe")
    backend = os.path.join(root, "backend")
    if not os.path.exists(python) or not os.path.isdir(backend):
        error_box(
            "Nem talalom a program fajljait!\n\n"
            "A '3D Scan Doctor.exe' a projekt mappajaban kell legyen\n"
            "(a .venv es a backend mappak mellett).\n\n"
            "Ha parancsikont szeretnel az asztalra, ne az exe-t masold at,\n"
            "hanem jobb klikk > Kuldes > Asztal (parancsikon letrehozasa)."
        )
        return

    log = open(os.path.join(backend, "server.log"), "w")
    server = subprocess.Popen(
        [python, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=backend,
        creationflags=CREATE_NO_WINDOW,
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
    )

    # Wait for the server to come up (large model libs take a few seconds)
    for _ in range(60):
        if server.poll() is not None:
            error_box(
                "A szerver nem tudott elindulni.\n\n"
                "Probald ujra, vagy ellenorizd, hogy a 8000-es portot\n"
                "nem hasznalja-e masik program."
            )
            return
        if server_running():
            break
        time.sleep(0.5)
    else:
        server.terminate()
        error_box("A szerver nem valaszolt idoben. Probald ujra!")
        return

    if os.environ.get("SCAN_DOCTOR_NO_BROWSER") != "1":
        webbrowser.open(APP_URL)

    # System tray icon so the app can be reopened or shut down
    import pystray

    def on_open(icon, item):
        webbrowser.open(APP_URL)

    def on_quit(icon, item):
        server.terminate()
        icon.stop()

    icon = pystray.Icon(
        "3d_scan_doctor",
        make_tray_icon_image(),
        "3D Scan Doctor - fut",
        menu=pystray.Menu(
            pystray.MenuItem("Megnyitas a bongeszoben", on_open, default=True),
            pystray.MenuItem("Kilepes", on_quit),
        ),
    )
    icon.run()

    # Safety net: make sure the server is gone when the tray exits
    if server.poll() is None:
        server.terminate()


if __name__ == "__main__":
    main()
