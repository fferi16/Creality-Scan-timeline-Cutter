"""Bridge to Creality Scan's own scan SDK (lib_orbbec_scan.dll).

Why this exists: cutting a time range by hand meant deleting depth frames out of
resources.obscan with SQLite and then asking the user to re-fuse in Creality
Scan. The SDK does both properly — it deletes points from the scan's own cloud
and rebuilds the mesh — so the edited project is finished when we hand it back.

ALWAYS RUN THIS AS A SUBPROCESS. The SDK starts background threads (network
device discovery) that fault on teardown, so the process is expected to die
badly once the work is done; we let it, after the result is on stdout. Loading
the DLL into the API process would take the server down with it.

Protocol: job JSON on stdin, JSON lines on stdout —
    {"stage": "...", "progress": 0.0}   progress, repeatedly
    {"ok": true, ...} | {"ok": false, "error": "..."}   exactly once, last

Everything version-specific is read from the installed app at runtime rather
than hardcoded: between SDK 3.4.2 and 3.4.9 a field was dropped from
OBScanContextConfig and import_session grew a parameter, and hardcoding either
one produced silent garbage reads rather than a clean error.
"""
import ctypes as C
import json
import os
import re
import sys
import time

INSTALL_DIR = os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "CrealityScan")
PLUGIN_DIR = os.path.join(INSTALL_DIR, "CrealityScan_Data", "Plugins", "x86_64")
METADATA = os.path.join(INSTALL_DIR, "CrealityScan_Data", "il2cpp_data",
                        "Metadata", "global-metadata.dat")
DLL = os.path.join(PLUGIN_DIR, "lib_orbbec_scan.dll")

# process-message ids we act on (stable across 3.4.2 -> 3.4.9)
MSG_IMPORT_OK = 341967380
MSG_IMPORT_FAILED = 341967381
MSG_EDIT_FINISHED = 341967398
MSG_FUSE_OK = 341967362

# points closer than this to a cloud point are treated as the same sample; the
# measured nearest-neighbour distance between the SDK cloud and pc_after.ply is
# ~0.4-2.2 mm, so 5 mm is a wide gate that still rejects unrelated geometry
MATCH_RADIUS_M = 0.005


def is_available():
    """True if Creality Scan is installed with the SDK we drive. Safe to call
    from the API process — it only stats files, never loads the DLL."""
    return os.path.isfile(DLL) and os.path.isfile(METADATA)


def _emit(**kw):
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.stdout.flush()


# The SDK reports failures as a numeric message id, which tells the user
# nothing. Its own log names the real cause, so read that back instead.
_REASONS = {
    "OUT_MEMORY":
        "Elfogyott a memória a mesh újraépítése közben. Zárd be a többi futó "
        "programot, és próbáld újra — egy teljes alakos szken fúziója sok GB-ot "
        "igényel.",
    "OUT_HARD_DISK":
        "Elfogyott a lemezterület az újrafúzió közben.",
    "PROJ_NOT_WRITE_PERMISSION":
        "A projekt nem írható. Zárd be a Creality Scant, és próbáld újra.",
    "DEVICE_BUSY":
        "Az eszköz foglalt. Zárd be a Creality Scant, és próbáld újra.",
}


def _sdk_failure_reason(log_dir):
    """Pull the last 'the failed status: OB_SCAN_ERROR_xxx' out of the SDK's own
    log and turn it into something the user can act on."""
    try:
        logs = [os.path.join(log_dir, f) for f in os.listdir(log_dir)
                if f.startswith("scan_log_") and f.endswith(".txt")]
        if not logs:
            return None
        newest = max(logs, key=os.path.getmtime)
        with open(newest, "r", encoding="utf-8", errors="replace") as f:
            hits = re.findall(r"the failed status: OB_SCAN_ERROR_(\w+)", f.read())
    except OSError:
        return None
    if not hits:
        return None
    code = hits[-1]
    return _REASONS.get(code, f"Az SDK hibája: {code}.")


def _context_config_fields():
    """Field names of OBScanContextConfig, in declaration order, straight from
    the installed app's IL2CPP metadata. 3.4.9 dropped
    computer_performance_algo_dir, which a hardcoded layout would not survive."""
    with open(METADATA, "rb") as f:
        blob = f.read()
    i = blob.find(b"OBScanContextConfig\x00")
    if i < 0:
        return None
    names = [m.group(1).decode() for m in
             re.finditer(rb"([\x20-\x7e]{2,60})\x00", blob[i:i + 400])]
    out = []
    for n in names[1:]:                      # [0] is the type name itself
        if n[0].isupper() or "|" in n:       # next type name => struct ended
            break
        out.append(n)
    return out or None


class _Sdk:
    def __init__(self, log_dir):
        os.add_dll_directory(PLUGIN_DIR)
        os.environ["PATH"] = PLUGIN_DIR + os.pathsep + os.environ.get("PATH", "")
        self.lib = C.CDLL(DLL)
        self.log_dir = log_dir
        self.ctx = C.c_void_p()
        self._keep = []          # callbacks must outlive the calls that use them
        self.messages = []

    # ---- setup ----
    def create_context(self):
        fields = _context_config_fields()
        if not fields:
            raise RuntimeError("Nem sikerult kiolvasni az SDK config-mezoit.")
        dirs = [f for f in fields if f != "num_thread"]
        os.makedirs(self.log_dir, exist_ok=True)
        Cfg = type("OBScanContextConfig", (C.Structure,), {
            "_fields_": [("num_thread", C.c_short)] + [(d, C.c_char_p) for d in dirs]})
        cfg = Cfg()
        cfg.num_thread = 4
        for d in dirs:
            # perf configs are read from the install dir; everything else is
            # written, so it has to be somewhere we own
            target = INSTALL_DIR if "performance" in d else self.log_dir
            setattr(cfg, d, target.encode("utf-8"))
        self._keep.append(cfg)

        fn = self.lib.obscan_context_create
        fn.restype = C.c_int
        fn.argtypes = [C.POINTER(C.c_void_p), C.POINTER(Cfg)]   # OUT pointer first
        rc = fn(C.byref(self.ctx), C.byref(cfg))
        if rc != 0 or not self.ctx.value:
            raise RuntimeError(f"Az SDK context nem jott letre (rc={rc}).")

    def add_listeners(self):
        """Both listeners must exist before import — without the data listener
        the import fails outright. They are passed BY REFERENCE; by value the
        SDK stores a bogus callback and faults on the first message."""
        CB = C.CFUNCTYPE(None, C.c_void_p)

        class Listener(C.Structure):
            _fields_ = [("cb", CB)]

        def make():
            def handler(ptr):
                if not ptr:
                    return
                raw = C.string_at(ptr, 16)
                self.messages.append((int.from_bytes(raw[0:4], "little"),
                                      int.from_bytes(raw[8:16], "little")))
            return CB(handler)

        for name in ("obscan_context_add_process_msg_listener",
                     "obscan_context_add_data_msg_listener"):
            cb = make()
            lis = Listener(cb)
            self._keep += [cb, lis]
            fn = getattr(self.lib, name)
            fn.restype = C.c_int
            fn.argtypes = [C.c_void_p, C.POINTER(Listener)]
            fn(self.ctx, C.byref(lis))

    def _wait(self, ids, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for mid, data in self.messages:
                if mid in ids:
                    return mid, data
            time.sleep(0.2)
        return None, None

    # ---- work ----
    def import_project(self, obp_path, timeout=600):
        """`obp_path` is the project.obp FILE (not its directory), and the
        project must be writable — the import writes into it."""
        PROG = C.CFUNCTYPE(None, C.c_float)
        prog = PROG(lambda p: None)
        self._keep.append(prog)
        fn = self.lib.obscan_context_import_session
        fn.restype = C.c_int
        # 3.4.9 added `is_mesh_project`; omitting it makes the SDK read a junk
        # register, take the mesh-import branch and reject the .obp extension
        fn.argtypes = [C.c_void_p, PROG, C.c_char_p, C.c_int]
        fn(self.ctx, prog, obp_path.encode("utf-8"), 0)
        mid, data = self._wait({MSG_IMPORT_OK, MSG_IMPORT_FAILED}, timeout)
        if mid != MSG_IMPORT_OK or not data:
            raise RuntimeError(
                "Az SDK nem tudta megnyitni a projektet. Zarva van a Creality Scan?")
        return C.c_void_p(data)

    def first_scan(self, session):
        n = C.c_int()
        fn = self.lib.obscan_session_get_scans_size
        fn.restype = C.c_int
        fn.argtypes = [C.c_void_p, C.POINTER(C.c_int)]
        fn(session, C.byref(n))
        if n.value < 1:
            raise RuntimeError("A projekt nem tartalmaz szkennelt adatot.")
        scan = C.c_void_p()
        fn = self.lib.obscan_session_get_scan_handle_with_index
        fn.restype = C.c_int
        fn.argtypes = [C.c_void_p, C.c_int, C.POINTER(C.c_void_p)]
        fn(session, 0, C.byref(scan))
        if not scan.value:
            raise RuntimeError("A szken nem nyithato meg.")
        return scan

    def raw_cloud(self, scan):
        """The scan's own point cloud, as an (n, 4) float32 array of x,y,z,i in
        metres. This is a subset of pc_after.ply, in the SDK's own index space —
        which is the space the delete call addresses."""
        import numpy as np

        class Pt(C.Structure):
            _fields_ = [("x", C.c_float), ("y", C.c_float),
                        ("z", C.c_float), ("i", C.c_float)]

        class Nrm(C.Structure):
            _fields_ = [("nx", C.c_float), ("ny", C.c_float),
                        ("nz", C.c_float), ("nw", C.c_float)]

        class Clouds(C.Structure):
            _fields_ = [("num_points", C.c_uint), ("points", C.POINTER(Pt)),
                        ("normals", C.POINTER(Nrm))]

        fn = self.lib.obscan_scan_get_raw_cloud
        fn.restype = C.c_int
        fn.argtypes = [C.c_void_p, C.POINTER(Clouds)]
        cl = Clouds()
        if fn(scan, C.byref(cl)) != 0 or not cl.num_points or not cl.points:
            raise RuntimeError("Az SDK nem adott vissza pontfelhot.")
        return np.ctypeslib.as_array(
            C.cast(cl.points, C.POINTER(C.c_float)),
            shape=(cl.num_points, 4)).copy()

    def delete_points(self, scan, indices, timeout=600):
        """Remove raw-cloud points by index. Persistent — only ever call this on
        a copy of the user's project."""
        class Indexs(C.Structure):
            _fields_ = [("idx", C.POINTER(C.c_uint)), ("idx_num", C.c_int)]

        buf = (C.c_uint * len(indices))(*indices)
        fn = self.lib.obscan_scan_edit_scan_data_results_with_index
        fn.restype = C.c_int
        fn.argtypes = [C.c_void_p, C.c_int, Indexs, Indexs]   # by value
        rc = fn(scan, 1, Indexs(buf, len(indices)), Indexs(None, 0))  # 1 = STOP_FINISH
        if rc != 0:
            raise RuntimeError(f"A pontok torlese nem sikerult (rc={rc}).")
        if self._wait({MSG_EDIT_FINISHED}, timeout)[0] is None:
            raise RuntimeError("A torles nem fejezodott be idoben.")

    def refuse(self, scan, timeout=3600):
        """Rebuild the mesh. Uses the settings this project was already fused
        with — the SDK's default is coarser (voxel 1.0 vs 0.14 on the projects
        measured), which would hand the user a visibly worse model and look
        like the cut damaged it."""
        class OptCfg(C.Structure):
            _fields_ = [("compute_speed", C.c_int), ("voxel_size", C.c_float),
                        ("credible_ratio", C.c_float),
                        ("remove_markers", C.c_bool), ("_pad", C.c_ubyte * 3),
                        ("export_format", C.c_int)]

        cfg = OptCfg()
        for getter in ("obscan_scan_get_applyed_opt_to_mesh_config",
                       "obscan_scan_get_default_opt_to_mesh_config"):
            try:
                fn = getattr(self.lib, getter)
            except AttributeError:
                continue
            fn.restype = C.c_int
            fn.argtypes = [C.c_void_p, C.POINTER(OptCfg)]
            if fn(scan, C.byref(cfg)) == 0 and cfg.voxel_size > 0:
                break
        if not cfg.voxel_size > 0:
            raise RuntimeError("Nem sikerult ervenyes fuzios beallitast szerezni.")

        PROG = C.CFUNCTYPE(None, C.c_float)
        last = [0.0]

        def on_progress(p):
            # the mesher reports very frequently; throttle to readable steps
            if p - last[0] >= 0.01 or p >= 1.0:
                last[0] = p
                _emit(stage="fuse", progress=float(p))
        prog = PROG(on_progress)
        self._keep.append(prog)

        fn = self.lib.obscan_scan_optimization_to_mesh
        fn.restype = C.c_int
        # like import_session, the progress callback comes BEFORE the config;
        # passing the config as arg 2 makes the SDK read zeros and reject the
        # voxel size
        fn.argtypes = [C.c_void_p, PROG, C.POINTER(OptCfg)]
        seen = len(self.messages)
        rc = fn(scan, prog, C.byref(cfg))
        if rc != 0:
            raise RuntimeError(f"Az ujrafuzio nem indult el (rc={rc}).")
        deadline = time.time() + timeout
        while len(self.messages) == seen and time.time() < deadline:
            time.sleep(0.5)
        if len(self.messages) == seen:
            raise RuntimeError("Az ujrafuzio nem fejezodott be idoben.")
        new = [m for m, _d in self.messages[seen:]]
        if MSG_FUSE_OK not in new:
            raise RuntimeError(_sdk_failure_reason(self.log_dir)
                               or "Az újrafúzió ismeretlen hibával zárult.")
        return float(cfg.voxel_size)


def _read_ply_xyz(path):
    """x,y,z of a binary-little-endian float32 PLY, in file order — which for
    pc_after.ply is capture order, i.e. the timeline the slider addresses."""
    import numpy as np
    with open(path, "rb") as f:
        header = b""
        while b"end_header\n" not in header:
            chunk = f.read(1)
            if not chunk:
                raise ValueError("nem PLY fejlec")
            header += chunk
        lines = header.split(b"\n")
        n = int(next(l for l in lines if l.startswith(b"element vertex")).split()[-1])
        nprop = sum(1 for l in lines if l.startswith(b"property"))
        body = np.frombuffer(f.read(n * nprop * 4), dtype="<f4").reshape(n, nprop)
    return body[:, :3].astype("float64")


def run_fuse(work_obp, log_dir):
    """Rebuild the mesh of an already-cut project.

    Deliberately does NOT choose what to remove. The SDK's cloud carries no
    scan time: its own order does not track capture time (Spearman -0.34
    against pc_after) and a nearest-neighbour match cannot recover it either —
    the 8 spatial neighbours of a point span a median 17% of the scan, because
    the scanner passes over the same surface many times. Selecting points there
    deleted a different section than the one on screen. The cut is decided in
    pc_after.ply, where the index IS the time; the SDK is used only for what it
    is reliable at — fusing."""
    _emit(stage="open", progress=0.0)
    sdk = _Sdk(log_dir)
    sdk.create_context()
    sdk.add_listeners()
    session = sdk.import_project(work_obp)
    scan = sdk.first_scan(session)
    voxel = sdk.refuse(scan)
    return {"ok": True, "fused": True, "voxel_size": voxel}


def run_cut_sdk_order(work_obp, ranges, log_dir, do_refuse=True, explicit_indices=None):
    """Cut ranges given as percentages of the SDK cloud's OWN index order.

    No matching and no second cloud: the viewer draws this exact array in this
    exact order, so the indices the slider covers are the indices deleted. The
    SDK's order is deterministic — a cloud read today is element-for-element the
    model.ply Creality Scan wrote in an earlier run — so an index is a stable
    identity for a point."""
    import numpy as np

    _emit(stage="open", progress=0.0)
    sdk = _Sdk(log_dir)
    sdk.create_context()
    sdk.add_listeners()
    session = sdk.import_project(work_obp)
    scan = sdk.first_scan(session)

    _emit(stage="cloud", progress=0.0)
    cloud = sdk.raw_cloud(scan)
    total = len(cloud)

    if explicit_indices is not None:
        # caller already decided which cloud points go (e.g. a whole region,
        # revisits included, chosen outside this process)
        picked = np.asarray(explicit_indices, dtype="uint32")
        picked = picked[picked < total]
    else:
        doomed = np.zeros(total, dtype=bool)
        for lo_pct, hi_pct in ranges:
            lo = max(0, min(total, int(round(lo_pct / 100.0 * total))))
            hi = max(0, min(total, int(round(hi_pct / 100.0 * total))))
            if hi > lo:
                doomed[lo:hi] = True
        picked = np.nonzero(doomed)[0].astype("uint32")
    if len(picked) == 0:
        raise RuntimeError("A kijelölt szakasz egyetlen pontot sem érint.")
    if len(picked) >= total:
        raise RuntimeError("A teljes szken ki lenne vágva.")

    _emit(stage="delete", progress=0.0, points=int(len(picked)))
    sdk.delete_points(scan, picked.tolist())

    voxel, fuse_error = None, None
    if do_refuse:
        try:
            voxel = sdk.refuse(scan)
        except RuntimeError as exc:
            fuse_error = str(exc)

    return {
        "ok": True,
        "removed_points": int(len(picked)),
        "remaining_points": int(total - len(picked)),
        "total_points": int(total),
        "fused": voxel is not None,
        "fuse_error": fuse_error,
        "voxel_size": voxel,
    }


def run_cut(work_obp, capture_ply, ranges, log_dir, do_refuse=True):
    """Cut the given time ranges out of an ALREADY-COPIED project and re-fuse it.

    The timeline lives in pc_after.ply (capture order); the SDK addresses its own
    voxel-ordered cloud. A nearest-neighbour match bridges the two: each SDK
    point takes the scan-time of the capture-ordered point it sits on."""
    import numpy as np
    from scipy.spatial import cKDTree

    _emit(stage="open", progress=0.0)
    sdk = _Sdk(log_dir)
    sdk.create_context()
    sdk.add_listeners()
    session = sdk.import_project(work_obp)
    scan = sdk.first_scan(session)

    _emit(stage="cloud", progress=0.0)
    cloud = sdk.raw_cloud(scan)
    pc = _read_ply_xyz(capture_ply)
    total = len(pc)

    _emit(stage="match", progress=0.0)
    dist, idx = cKDTree(pc).query(cloud[:, :3].astype("float64"), k=1, workers=-1)
    doomed = np.zeros(len(cloud), dtype=bool)
    timeline_removed = 0
    for lo_pct, hi_pct in ranges:
        lo = max(0, min(total, int(round(lo_pct / 100.0 * total))))
        hi = max(0, min(total, int(round(hi_pct / 100.0 * total))))
        if hi > lo:
            doomed |= (idx >= lo) & (idx < hi)
            timeline_removed += hi - lo
    doomed &= dist < MATCH_RADIUS_M
    picked = np.nonzero(doomed)[0].astype("uint32")
    if len(picked) == 0:
        raise RuntimeError("A kijelolt szakasz egyetlen pontot sem erint.")
    if len(picked) >= len(cloud):
        raise RuntimeError("A teljes szken ki lenne vagva.")

    _emit(stage="delete", progress=0.0, points=int(len(picked)))
    sdk.delete_points(scan, picked.tolist())

    # Once the points are gone the project is already correct — only its mesh is
    # stale. If the rebuild fails (running out of memory on a big scan is the
    # realistic case) that is a degraded result, not a lost one: hand the
    # project back and let the user fuse it in Creality Scan, exactly as the
    # non-SDK path does. Throwing it away would discard a gigabyte of work.
    voxel, fuse_error = None, None
    if do_refuse:
        try:
            voxel = sdk.refuse(scan)
        except RuntimeError as exc:
            fuse_error = str(exc)

    # Report in TIMELINE points — that is the cloud the user scrubbed and the
    # count the UI already shows. The SDK's own cloud is a smaller internal
    # sample of it; mixing the two made the sidebar read "26 529 / 0 points".
    return {
        "ok": True,
        "removed_points": int(timeline_removed),
        "remaining_points": int(total - timeline_removed),
        "total_points": int(total),
        # what actually went to the SDK, kept for diagnostics
        "cloud_removed": int(len(picked)),
        "cloud_points": int(len(cloud)),
        # False => the cut is applied but the user still has to fuse in Creality
        "fused": voxel is not None,
        "fuse_error": fuse_error,
        "voxel_size": voxel,
        "match_median_mm": float(np.median(dist) * 1000.0),
    }


def main():
    job = json.loads(sys.stdin.read())
    try:
        if job.get("mode") == "fuse":
            result = run_fuse(job["work_obp"], job["log_dir"])
        elif job.get("mode") == "match":
            # legacy: decide the cut in pc_after and map it across. Kept for
            # reference; the default path cuts in the SDK's own order.
            result = run_cut(job["work_obp"], job["capture_ply"],
                             job["ranges"], job["log_dir"],
                             do_refuse=job.get("refuse", True))
        else:
            result = run_cut_sdk_order(job["work_obp"], job["ranges"],
                                       job["log_dir"],
                                       do_refuse=job.get("refuse", True),
                                       explicit_indices=job.get("explicit_indices"))
    except Exception as exc:                      # noqa: BLE001 - reported to the parent
        _emit(ok=False, error=str(exc))
        sys.stdout.flush()
        os._exit(1)
    _emit(**result)
    sys.stdout.flush()
    # never return: SDK teardown faults, and the result is already delivered
    os._exit(0)


if __name__ == "__main__":
    main()
