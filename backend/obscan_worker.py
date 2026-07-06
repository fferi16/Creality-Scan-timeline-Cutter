"""
Standalone worker that drives the Creality/Orbbec Scan SDK for the raw-project
timeline editor. Run as a one-shot subprocess (the SDK segfaults on teardown,
so we do one job, flush output, and os._exit — the crashy shutdown is ignored).

Commands:
  list
      Print the user's Creality Scan projects as JSON (name, obp path, mtime).

  load  <project.obp>  <out_cloud.bin>
      Import the project and dump its raw point cloud, which is stored in
      acquisition (time) order, as packed little-endian float32 xyz triples.
      Prints one JSON line: {"num_points": N}.

  cut   <project.obp>  <start_index>  <end_index>
      Delete raw-cloud points in [start_index, end_index) — i.e. one time
      interval of the scan — and persist it into the project. Prints
      {"before": N, "after": M}.  Work on a COPY: the edit is written to disk.
"""
import os
import sys
import json
import time
import glob
import ctypes

PLUGIN_DIR = r"C:\Program Files\CrealityScan\CrealityScan_Data\Plugins\x86_64"
PROJECTS_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Creality", "CrealityScan", "Projects")

# --- SDK enums / message ids (from Il2CppDumper) ---
OB_OK = 0
MSG_IMPORT_OK = 341967380
MSG_IMPORT_FAIL = 341967381
MSG_EDIT_FINISHED = 341967398
POST_STOP_FINISH = 1


def _load_lib():
    os.add_dll_directory(PLUGIN_DIR)
    os.environ["PATH"] = PLUGIN_DIR + os.pathsep + os.environ.get("PATH", "")
    return ctypes.CDLL(os.path.join(PLUGIN_DIR, "lib_orbbec_scan.dll"))


# --- ctypes struct mirrors of the SDK types ---
class _Cfg(ctypes.Structure):
    _fields_ = [("num_thread", ctypes.c_short)] + [(c, ctypes.c_char_p) for c in "abcde"]


class _Msg(ctypes.Structure):
    _fields_ = [("msg_id", ctypes.c_int), ("data", ctypes.c_void_p)]


_MSG_CB = ctypes.CFUNCTYPE(None, _Msg)


class _Listener(ctypes.Structure):
    _fields_ = [("onMessage", _MSG_CB)]


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float),
                ("z", ctypes.c_float), ("intensity", ctypes.c_float)]


class _Clouds(ctypes.Structure):
    _fields_ = [("num", ctypes.c_uint), ("points", ctypes.c_void_p), ("normals", ctypes.c_void_p)]


class _Indexs(ctypes.Structure):
    _fields_ = [("idx", ctypes.c_void_p), ("idx_num", ctypes.c_int)]


class _Sdk:
    """Holds an SDK context + imported session for one project."""

    def __init__(self, log_dir):
        os.makedirs(log_dir, exist_ok=True)
        self.lib = _load_lib()
        b = log_dir.encode("utf-8")
        self.lib.obscan_context_create.restype = ctypes.c_int
        self.lib.obscan_context_create.argtypes = [
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(_Cfg)]
        self.ctx = ctypes.c_void_p()
        if self.lib.obscan_context_create(ctypes.byref(self.ctx),
                                          ctypes.byref(_Cfg(4, b, b, b, b, b))) != OB_OK:
            raise RuntimeError("context_create failed")
        self._state = {"session": None, "fail": False, "edit_done": False}
        self._keep = self._install_listeners()

    def _install_listeners(self):
        def on_proc(msg):
            if msg.msg_id == MSG_IMPORT_OK:
                self._state["session"] = msg.data
            elif msg.msg_id == MSG_IMPORT_FAIL:
                self._state["fail"] = True
            elif msg.msg_id == MSG_EDIT_FINISHED:
                self._state["edit_done"] = True
        pcb = _MSG_CB(on_proc)
        dcb = _MSG_CB(lambda m: None)
        pl, dl = _Listener(pcb), _Listener(dcb)
        for name, lst in [("add_process_msg_listener", pl), ("add_data_msg_listener", dl)]:
            fn = getattr(self.lib, "obscan_context_" + name)
            fn.restype = ctypes.c_int
            fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            fn(self.ctx, ctypes.cast(ctypes.byref(lst), ctypes.c_void_p))
        return (pcb, dcb, pl, dl)

    def import_project(self, obp_path, timeout=180):
        PROG = ctypes.CFUNCTYPE(None, ctypes.c_float)
        self._prog = PROG(lambda p: None)
        fn = self.lib.obscan_context_import_session
        fn.restype = ctypes.c_int
        fn.argtypes = [ctypes.c_void_p, PROG, ctypes.c_char_p]
        # NOTE: must point at the project.obp FILE, not the folder.
        if fn(self.ctx, self._prog, obp_path.encode("utf-8")) != OB_OK:
            raise RuntimeError("import_session rejected")
        t = time.time()
        while self._state["session"] is None and not self._state["fail"]:
            if time.time() - t > timeout:
                raise TimeoutError("import timed out")
            time.sleep(0.2)
        if self._state["fail"] or not self._state["session"]:
            raise RuntimeError("import failed")
        self.session = ctypes.c_void_p(self._state["session"])
        self.lib.obscan_session_get_scans_size.restype = ctypes.c_int
        self.lib.obscan_session_get_scans_size.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        nscans = ctypes.c_int()
        self.lib.obscan_session_get_scans_size(self.session, ctypes.byref(nscans))
        if nscans.value < 1:
            raise RuntimeError("A projekt nem tartalmaz szken-adatot (üres projekt).")
        self.lib.obscan_session_get_scan_handle_with_index.restype = ctypes.c_int
        self.lib.obscan_session_get_scan_handle_with_index.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        self.scan = ctypes.c_void_p()
        self.lib.obscan_session_get_scan_handle_with_index(self.session, 0, ctypes.byref(self.scan))

    def raw_cloud(self):
        self.lib.obscan_scan_get_raw_cloud.restype = ctypes.c_int
        self.lib.obscan_scan_get_raw_cloud.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Clouds)]
        c = _Clouds()
        if self.lib.obscan_scan_get_raw_cloud(self.scan, ctypes.byref(c)) != OB_OK:
            raise RuntimeError("get_raw_cloud failed")
        return c.num, ctypes.cast(c.points, ctypes.POINTER(_Point))

    def delete_indices(self, indices, timeout=180):
        n = len(indices)
        arr = (ctypes.c_uint * n)(*indices)
        model_del = _Indexs(ctypes.cast(arr, ctypes.c_void_p), n)
        empty = _Indexs(None, 0)
        self._state["edit_done"] = False
        fn = self.lib.obscan_scan_edit_scan_data_results_with_index
        fn.restype = ctypes.c_int
        fn.argtypes = [ctypes.c_void_p, ctypes.c_int, _Indexs, _Indexs]
        if fn(self.scan, POST_STOP_FINISH, model_del, empty) != OB_OK:
            raise RuntimeError("edit rejected")
        t = time.time()
        while not self._state["edit_done"]:
            if time.time() - t > timeout:
                raise TimeoutError("edit timed out")
            time.sleep(0.2)


def cmd_list():
    out = []
    for obp in glob.glob(os.path.join(PROJECTS_DIR, "*", "project.obp")):
        proj = os.path.dirname(obp)
        if os.path.basename(proj).endswith("_vagott"):
            continue  # our own edited copies aren't editable sources
        thumb = os.path.join(proj, "thumbnail.png")
        out.append({
            "name": os.path.basename(proj),
            "obp": obp,
            "dir": proj,
            "mtime": os.path.getmtime(proj),
            "thumbnail": thumb if os.path.exists(thumb) else None,
        })
    out.sort(key=lambda p: p["mtime"], reverse=True)
    print(json.dumps(out))


def cmd_load(obp, out_bin):
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdk_logs")
    sdk = _Sdk(log_dir)
    sdk.import_project(obp)
    n, pts = sdk.raw_cloud()
    buf = (ctypes.c_float * (n * 3))()
    for i in range(n):
        buf[i * 3] = pts[i].x
        buf[i * 3 + 1] = pts[i].y
        buf[i * 3 + 2] = pts[i].z
    with open(out_bin, "wb") as f:
        f.write(bytes(buf))
    print(json.dumps({"num_points": n}))
    sys.stdout.flush()
    os._exit(0)


def cmd_cut(obp, start, end):
    start, end = int(start), int(end)
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdk_logs")
    sdk = _Sdk(log_dir)
    sdk.import_project(obp)
    before, _ = sdk.raw_cloud()
    end = min(end, before)
    sdk.delete_indices(list(range(start, end)))
    after, _ = sdk.raw_cloud()
    print(json.dumps({"before": before, "after": after}))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if cmd == "list":
            cmd_list()
        elif cmd == "load":
            cmd_load(sys.argv[2], sys.argv[3])
        elif cmd == "cut":
            cmd_cut(sys.argv[2], sys.argv[3], sys.argv[4])
        else:
            print(json.dumps({"error": "unknown command"}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
    sys.stdout.flush()
    os._exit(0)
