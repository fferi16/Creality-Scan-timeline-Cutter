"""
Thin ctypes bridge to Creality Scan's bundled Orbbec Scan SDK
(lib_orbbec_scan.dll), so we can read raw scan projects — per-frame point
clouds, poses and timestamps — without decoding the proprietary `40 a2`
container ourselves.

The DLL exposes ~194 clean `obscan_*` C exports but ships no headers, so the
function signatures below are reverse-engineered incrementally and verified
against real projects. Every risky first-call is exercised from a throwaway
subprocess first (see scratchpad experiments) so a wrong signature can't take
down the API server.
"""
import os
import time
import ctypes

# CrealityScan install layout (default Windows install)
CREALITY_ROOT = r"C:\Program Files\CrealityScan"
PLUGIN_DIR = os.path.join(CREALITY_ROOT, "CrealityScan_Data", "Plugins", "x86_64")
SCAN_DLL = os.path.join(PLUGIN_DIR, "lib_orbbec_scan.dll")

# OBScanStatus: 0 = success, 0x325D_xxxx = error codes (see dump.cs)
OB_SCAN_SUCCESS = 0

_lib = None


# Message IDs (from Il2CppDumper): the process listener signals import completion.
MSG_PROJECT_IMPORT_SUCCESS = 341967380
MSG_PROJECT_IMPORT_FAILED = 341967381


class OBScanContextConfig(ctypes.Structure):
    """Matches OBScanContextConfig in the SDK (offsets verified against
    Il2CppDumper output: 0x0, 0x8, 0x10, 0x18, 0x20, 0x28). The string fields
    are directory paths the SDK writes logs / perf data into."""
    _fields_ = [
        ("num_thread", ctypes.c_short),
        ("ob_sdk_log_dir", ctypes.c_char_p),
        ("ob_scan_log_dir", ctypes.c_char_p),
        ("computer_performance_fps_dir", ctypes.c_char_p),
        ("computer_performance_algo_dir", ctypes.c_char_p),
        ("windows_gpu_info_dir", ctypes.c_char_p),
    ]


class _Msg(ctypes.Structure):
    _fields_ = [("msg_id", ctypes.c_int), ("data", ctypes.c_void_p)]


_MSG_CB = ctypes.CFUNCTYPE(None, _Msg)


class _Listener(ctypes.Structure):
    _fields_ = [("onMessage", _MSG_CB)]


class OBScanPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float),
                ("z", ctypes.c_float), ("intensity", ctypes.c_float)]


class OBScanPointClouds(ctypes.Structure):
    _fields_ = [("num_points", ctypes.c_uint),
                ("points", ctypes.c_void_p), ("normals", ctypes.c_void_p)]


def is_available() -> bool:
    """True if a CrealityScan install with the scan SDK is present."""
    return os.path.isfile(SCAN_DLL)


def load():
    """Load lib_orbbec_scan.dll once, resolving its sibling dependencies
    (OrbbecSDK.dll, opencv_world455.dll, ...) from the plugin folder."""
    global _lib
    if _lib is not None:
        return _lib
    if not is_available():
        raise FileNotFoundError(
            f"CrealityScan scan SDK not found at {SCAN_DLL}. "
            "Is CrealityScan installed in the default location?"
        )
    # Make dependent DLLs discoverable before loading the main library.
    os.add_dll_directory(PLUGIN_DIR)
    os.environ["PATH"] = PLUGIN_DIR + os.pathsep + os.environ.get("PATH", "")
    _lib = ctypes.CDLL(SCAN_DLL)
    return _lib


def create_context(log_dir: str) -> ctypes.c_void_p:
    """Create an SDK context (verified working: returns OB_SCAN_SUCCESS and a
    valid handle even with no scanner attached). `log_dir` must be a writable
    directory — all five config path fields point at it. Pair every successful
    call with destroy_context()."""
    lib = load()
    os.makedirs(log_dir, exist_ok=True)
    b = log_dir.encode("utf-8")
    cfg = OBScanContextConfig(4, b, b, b, b, b)

    fn = lib.obscan_context_create
    fn.restype = ctypes.c_int
    fn.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(OBScanContextConfig)]
    ctx = ctypes.c_void_p()
    rc = fn(ctypes.byref(ctx), ctypes.byref(cfg))
    if rc != OB_SCAN_SUCCESS or not ctx.value:
        raise RuntimeError(f"obscan_context_create failed (status {rc:#x})")
    return ctx


def destroy_context(ctx: ctypes.c_void_p):
    """Release a context so the SDK's background threads shut down cleanly."""
    lib = load()
    fn = lib.obscan_context_destroy
    fn.restype = ctypes.c_int
    fn.argtypes = [ctypes.c_void_p]
    fn(ctx)


def import_project(ctx, project_obp_path: str, timeout_s: float = 120.0) -> ctypes.c_void_p:
    """Import a Creality Scan project and return its session handle.

    `project_obp_path` MUST point at the project's `project.obp` FILE (not the
    folder — the folder form fails the SDK's path/write checks). The project
    directory must be writable, so import a robocopy'd copy, not the user's
    original, and make sure CrealityScan itself is closed (it locks projects).

    Verified working: pulls 198k+ real points out of a real project.
    """
    lib = load()
    # Both listeners must be registered before import, or it fails with
    # LISTENER_NOT_FOUND. The process listener delivers the session handle.
    captured = {"session": None, "failed": False}

    def on_proc(msg):
        if msg.msg_id == MSG_PROJECT_IMPORT_SUCCESS:
            captured["session"] = msg.data
        elif msg.msg_id == MSG_PROJECT_IMPORT_FAILED:
            captured["failed"] = True

    proc_cb = _MSG_CB(on_proc)
    proc_listener = _Listener(proc_cb)
    data_cb = _MSG_CB(lambda msg: None)
    data_listener = _Listener(data_cb)
    # keep refs alive for the SDK's lifetime
    import_project._keep = (proc_cb, proc_listener, data_cb, data_listener)

    for name, lst in [("add_process_msg_listener", proc_listener),
                      ("add_data_msg_listener", data_listener)]:
        fn = getattr(lib, "obscan_context_" + name)
        fn.restype = ctypes.c_int
        fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        if fn(ctx, ctypes.cast(ctypes.byref(lst), ctypes.c_void_p)) != OB_SCAN_SUCCESS:
            raise RuntimeError(f"{name} failed")

    PROG = ctypes.CFUNCTYPE(None, ctypes.c_float)
    prog_cb = PROG(lambda p: None)
    fn = lib.obscan_context_import_session
    fn.restype = ctypes.c_int
    fn.argtypes = [ctypes.c_void_p, PROG, ctypes.c_char_p]
    rc = fn(ctx, prog_cb, project_obp_path.encode("utf-8"))
    if rc != OB_SCAN_SUCCESS:
        raise RuntimeError(f"import_session failed (status {rc:#x})")

    deadline = time.time() + timeout_s
    while captured["session"] is None and not captured["failed"]:
        if time.time() > deadline:
            raise TimeoutError("import did not complete in time")
        time.sleep(0.2)
    if captured["failed"] or captured["session"] is None:
        raise RuntimeError("project import failed")
    return ctypes.c_void_p(captured["session"])


def get_scan_raw_cloud(session, index: int = 0):
    """Return the raw point cloud of scan `index` as an (N, 3) list of xyz
    tuples in metres. Coordinates come straight from the SDK decoder."""
    lib = load()
    lib.obscan_session_get_scan_handle_with_index.restype = ctypes.c_int
    lib.obscan_session_get_scan_handle_with_index.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    scan = ctypes.c_void_p()
    if lib.obscan_session_get_scan_handle_with_index(session, index, ctypes.byref(scan)):
        raise RuntimeError("get_scan_handle failed")

    lib.obscan_scan_get_raw_cloud.restype = ctypes.c_int
    lib.obscan_scan_get_raw_cloud.argtypes = [ctypes.c_void_p, ctypes.POINTER(OBScanPointClouds)]
    cloud = OBScanPointClouds()
    if lib.obscan_scan_get_raw_cloud(scan, ctypes.byref(cloud)):
        raise RuntimeError("get_raw_cloud failed")
    pts = ctypes.cast(cloud.points, ctypes.POINTER(OBScanPoint))
    return cloud.num_points, pts


if __name__ == "__main__":
    print("SDK available:", is_available())
    ctx = create_context(os.path.join(os.path.dirname(__file__), "sdk_logs"))
    print("Context created:", hex(ctx.value))
    destroy_context(ctx)
    print("Context destroyed OK")
