"""Rebuild a scan from its individual depth frames, in scan order.

Why this exists: the timeline used to be the point order inside pc_after.ply.
That order is scan time, but a point cannot be traced back to the frame it came
from, so the slider and the deletion only agreed on average. A frame is a single
instant, and every frame in a project carries its own pose — so the frames can
be put back into one coordinate system and the timeline becomes exact: the
slider selects frames, and those very frames are what the cut deletes.

Neither the codec nor the poses are documented; both are read by calling the
functions inside lib_orbbec_scan.dll directly (see TEENDOK.md for how they were
found). Only the file path is needed — no session, no import, no scanner:

    open_container(dir, filename, 1) -> container
    read_frame(container, <the number in d~%06d>, &desc)   -> depth image
    read_records(container, &"p~after", &{begin,end,cap})  -> 160-byte records

A record holds the frame number at +0x08 and a 4x4 double pose at +0x20, so the
frame number ties the two calls together. The pose is camera->world; measured
against pc_after.ply the placed frames land 0.19 mm (small scan) to 0.79 mm
(full body) from it.

ALWAYS RUN THIS AS A SUBPROCESS — same as obscan_sdk: the SDK's background
threads fault on teardown, so the process is expected to die once the result is
written. The addresses below belong to this DLL build; if Creality Scan updates
them, the calls fail (or the process dies) and the caller falls back to the
pc_after timeline rather than showing something wrong.

Protocol: job JSON on stdin, JSON lines on stdout —
    {"stage": "frames", "progress": 0.0}          repeatedly
    {"ok": true, ...} | {"ok": false, "error": ""} exactly once, last
"""
import ctypes as C
from ctypes import wintypes
import json
import os
import struct
import sys

from obscan_sdk import (DLL, INSTALL_DIR, METADATA, PLUGIN_DIR,  # noqa: F401
                        _context_config_fields, _emit, is_available)

# Offsets into lib_orbbec_scan.dll (SDK 3.4.9.260722). Not exports — these were
# reverse engineered, so they are pinned to this build.
OFF_OPEN = 0x69FC90        # (const std::string& dir, const std::string& file, uint) -> container
OFF_IS_OPEN = 0x69D780     # (container) -> bool
OFF_READ_FRAME = 0x69C1F0  # (container, int frame_number, Desc* io) -> bool
OFF_READ_RECS = 0x69C6B0   # (container, const std::string& name, vector* out) -> bool

REC_SIZE = 0xA0            # sizeof(rs_common::RecordFrameEntry)
REC_NUMBER = 0x08          # uint32: the number used in "d~%06d"
REC_POSE = 0x20            # 4x4 double, row major, last row (0,0,0,1)
DEPTH_SCALE = 1e-4         # the depth images are uint16 in 0.1 mm units

# How many points the viewer gets. A full body scan is ~2400 frames of ~50k
# valid pixels: drawing all 100M would kill the browser, so every frame gives an
# equal share of this budget. Equal share matters — a frame must not become more
# or less prominent on the timeline because it happened to see more surface.
#
# The budget costs no decoding time — measured on a 2401-frame scan, 3M, 8M and
# 20M points all took ~30 s, because the time goes on unpacking every depth
# image, which happens either way. What it costs is file size and browser
# memory (20M points = 229 MB), so it is set by what the viewer can carry.
DEFAULT_MAX_POINTS = 20_000_000
MIN_PER_FRAME = 120


class _MemoryBasicInformation(C.Structure):
    _fields_ = [("BaseAddress", C.c_void_p), ("AllocationBase", C.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("__align", wintypes.DWORD),
                ("RegionSize", C.c_size_t), ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]


_k32 = C.WinDLL("kernel32")
_k32.VirtualQuery.argtypes = [C.c_void_p, C.POINTER(_MemoryBasicInformation), C.c_size_t]
_k32.VirtualQuery.restype = C.c_size_t


def _readable_bytes(addr):
    """How many bytes are readable from `addr`. The decoder hands back pointers
    into its own buffers; checking them first turns a wrong guess into a skipped
    frame instead of a crashed process."""
    info = _MemoryBasicInformation()
    if not _k32.VirtualQuery(C.c_void_p(addr), C.byref(info), C.sizeof(info)):
        return 0
    if info.State != 0x1000 or not (info.Protect & (0x02 | 0x04 | 0x20 | 0x40 | 0x80)):
        return 0
    return max(0, (info.BaseAddress or 0) + info.RegionSize - addr)


def _std_string(text):
    """Build an MSVC std::string by hand.

    Up to 15 characters the text lives INLINE in the first 16 bytes and only
    longer strings use a heap pointer. Writing a pointer unconditionally works
    for paths but makes every short name ("p~after", "d~000054") read the
    pointer's own bytes as text — and the call then silently returns false."""
    raw = text.encode("utf-8")
    buf = (C.c_ubyte * 32)()
    keep = None
    if len(raw) <= 15:
        buf[:len(raw)] = raw                        # zero fill terminates it
        struct.pack_into("<Q", buf, 24, 15)         # capacity stays at the SSO limit
    else:
        keep = C.create_string_buffer(raw)
        struct.pack_into("<Q", buf, 0, C.addressof(keep))
        struct.pack_into("<Q", buf, 24, len(raw) + 1)
    struct.pack_into("<Q", buf, 16, len(raw))       # size
    return buf, keep


class _Container:
    """The obscan file, opened through the library's own reader."""

    def __init__(self, obscan_path, log_dir):
        os.add_dll_directory(PLUGIN_DIR)
        os.environ["PATH"] = PLUGIN_DIR + os.pathsep + os.environ.get("PATH", "")
        self.lib = C.CDLL(DLL)
        base = C.cast(self.lib._handle, C.c_void_p).value
        self._open = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint)(base + OFF_OPEN)
        self._is_open = C.CFUNCTYPE(C.c_ubyte, C.c_void_p)(base + OFF_IS_OPEN)
        self._read_frame = C.CFUNCTYPE(C.c_ubyte, C.c_void_p, C.c_int, C.c_void_p)(base + OFF_READ_FRAME)
        self._read_recs = C.CFUNCTYPE(C.c_ubyte, C.c_void_p, C.c_void_p, C.c_void_p)(base + OFF_READ_RECS)
        self._keep = []
        self._create_context(log_dir)

        # the reader wants the DIRECTORY and the FILE NAME separately, not a path
        dir_s, k1 = _std_string(os.path.dirname(obscan_path))
        file_s, k2 = _std_string(os.path.basename(obscan_path))
        self._keep += [dir_s, k1, file_s, k2]
        self.handle = self._open(C.byref(dir_s), C.byref(file_s), 1)
        if not self.handle or not self._is_open(self.handle):
            raise RuntimeError("A resources.obscan nem nyithato meg.")
        # a big scratch buffer for the decoder to write a frame into
        self._dest = (C.c_ubyte * (8 << 20))()

    def _create_context(self, log_dir):
        """The reader relies on library-global state that only exists once a
        context has been created."""
        fields = _context_config_fields()
        if not fields:
            raise RuntimeError("Nem sikerult kiolvasni az SDK config-mezoit.")
        dirs = [f for f in fields if f != "num_thread"]
        os.makedirs(log_dir, exist_ok=True)
        Cfg = type("OBScanContextConfig", (C.Structure,), {
            "_fields_": [("num_thread", C.c_short)] + [(d, C.c_char_p) for d in dirs]})
        cfg = Cfg()
        cfg.num_thread = 4
        for d in dirs:
            setattr(cfg, d, (INSTALL_DIR if "performance" in d else log_dir).encode("utf-8"))
        self._keep.append(cfg)
        ctx = C.c_void_p()
        fn = self.lib.obscan_context_create
        fn.restype = C.c_int
        fn.argtypes = [C.POINTER(C.c_void_p), C.POINTER(Cfg)]
        rc = fn(C.byref(ctx), C.byref(cfg))
        if rc != 0 or not ctx.value:
            raise RuntimeError(f"Az SDK context nem jott letre (rc={rc}).")
        self._keep.append(ctx)

    def records(self, name):
        """The per-frame records stored under `name` (e.g. "p~after").

        Returns (frame_numbers, poses). The vector's memory belongs to the
        library; we only read it and let the process die with it."""
        import numpy as np
        s_buf, keep = _std_string(name)
        vec = (C.c_uint64 * 3)()                 # {begin, end, capacity}
        ok = self._read_recs(self.handle, C.byref(s_buf), C.byref(vec))
        del keep
        if not ok or vec[1] <= vec[0]:
            return None, None
        count = (vec[1] - vec[0]) // REC_SIZE
        raw = C.string_at(vec[0], count * REC_SIZE)
        numbers = np.frombuffer(raw, dtype="<u4").reshape(count, REC_SIZE // 4)[:, REC_NUMBER // 4]
        poses = np.frombuffer(raw, dtype="<f8").reshape(
            count, REC_SIZE // 8)[:, REC_POSE // 8:].reshape(count, 4, 4)
        return numbers.copy(), poses.copy()

    def frame_points(self, number):
        """Camera-space points (metres) of depth frame `number`.

        Returns (points, reason): on failure the points are None and the reason
        names which check stopped it. A frame silently missing from the timeline
        would be a lie about what the cut covers, so the reasons are counted and
        reported rather than swallowed.

        The descriptor we pass in comes back holding the frame's own intrinsics,
        and its first field is replaced by the decoder's output descriptor —
        the depth plane has to be followed from there, not from our buffer."""
        import numpy as np
        desc = (C.c_ubyte * 64)()
        struct.pack_into("<Q", desc, 0, C.addressof(self._dest))
        if not self._read_frame(self.handle, int(number), C.byref(desc)):
            return None, "read_failed"
        blob = bytes(desc)
        fx, fy, cx, cy = struct.unpack_from("<ffff", blob, 8)
        out = struct.unpack_from("<Q", blob, 0)[0]
        if _readable_bytes(out) < 64:
            return None, "descriptor_unreadable"
        head = C.string_at(out, 64)
        height, width = struct.unpack_from("<II", head, 8)
        plane = struct.unpack_from("<Q", head, 0x10)[0]
        plane_end = struct.unpack_from("<Q", head, 0x20)[0]
        need = width * height * 2
        if not (0 < need <= (8 << 20)):
            return None, "bad_size"
        if plane_end - plane != need:
            return None, "plane_mismatch"
        if _readable_bytes(plane) < need:
            return None, "plane_unreadable"
        depth = np.frombuffer(C.string_at(plane, need), dtype="<u2").reshape(height, width)
        ys, xs = np.nonzero(depth)
        if not len(ys):
            return None, "empty"
        z = depth[ys, xs].astype(np.float64) * DEPTH_SCALE
        return np.stack([(xs - cx) / fx * z, (ys - cy) / fy * z, z], 1), None


def read_frames(session_dir, out_bin, log_dir, max_points=DEFAULT_MAX_POINTS):
    """Write every frame's points, in scan order, as packed float32 xyz.

    Returns the manifest: one (frame number, point count) pair for every frame
    the registration accepted, so the caller can turn a slider position into
    exact frames and back into a contiguous range of the file. A frame whose
    image would not decode is still listed, with zero points."""
    import numpy as np

    _emit(stage="open", progress=0.0)
    obscan = os.path.join(session_dir, "resources.obscan")
    if not os.path.isfile(obscan):
        raise RuntimeError("Ebben a projektben nincs resources.obscan.")
    con = _Container(obscan, log_dir)

    numbers, poses = con.records("p~after")
    if numbers is None:
        raise RuntimeError("A projektben nincsenek kepkocka-pozok (p~after).")
    # A frame the registration gave up on has an all-zero matrix; the fusion
    # skips exactly those, so they are not part of the model and must not be
    # part of the timeline either.
    usable = np.isclose(poses[:, 3], [0, 0, 0, 1]).all(1)
    order = np.nonzero(usable)[0]
    if not len(order):
        raise RuntimeError("Egyetlen kepkockanak sincs ervenyes pozja.")

    quota = max(MIN_PER_FRAME, int(max_points // len(order)))
    rng = np.random.default_rng(0)             # same view every time it is opened
    manifest = []
    written = 0
    dropped = {}
    with open(out_bin, "wb") as fh:
        for done, k in enumerate(order):
            pts, why = con.frame_points(numbers[k])
            if pts is None:
                # Decoding fails now and then (measured: the same scan gave 2350
                # frames twice and 2338 once), so try again before giving up.
                pts, why = con.frame_points(numbers[k])
            if pts is None:
                # It has a pose, so it IS part of the model and part of scan
                # time — it stays on the timeline with no points of its own.
                # Dropping it would leave a frame the user cannot select and the
                # cut would not remove, while the section around it disappears.
                dropped[why] = dropped.get(why, 0) + 1
                manifest.append((int(numbers[k]), 0))
                continue
            if len(pts) > quota:
                pts = pts[rng.choice(len(pts), quota, replace=False)]
            world = pts @ poses[k][:3, :3].T + poses[k][:3, 3]
            fh.write(np.ascontiguousarray(world, dtype="<f4").tobytes())
            manifest.append((int(numbers[k]), int(len(world))))
            written += len(world)
            if done % 25 == 0:
                _emit(stage="frames", progress=round(done / len(order), 4))
    if not written:
        raise RuntimeError("Egyetlen kepkockat sem sikerult dekodolni.")

    return {
        "ok": True,
        "frames": manifest,
        "num_frames": len(manifest),
        "num_points": written,
        "total_frames": int(len(numbers)),
        "no_pose_frames": int(len(numbers) - len(order)),
        # frames on the timeline that ended up with no points, and why
        "blank_frames": int(sum(dropped.values())),
        "dropped": dropped,
        "points_per_frame": quota,
        # echoed so the caller's cache can tell a stale budget from a fresh one
        "max_points": int(max_points),
    }


def main():
    job = json.loads(sys.stdin.read())
    try:
        result = read_frames(job["session_dir"], job["out_bin"], job["log_dir"],
                             int(job.get("max_points") or DEFAULT_MAX_POINTS))
    except Exception as exc:                      # noqa: BLE001 - reported to the parent
        _emit(ok=False, error=str(exc))
        sys.stdout.flush()
        os._exit(1)
    _emit(**result)
    sys.stdout.flush()
    # never return: the SDK's teardown faults, and the result is already sent
    os._exit(0)


if __name__ == "__main__":
    main()
