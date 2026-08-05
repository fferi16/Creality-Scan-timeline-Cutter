"""
API for the timeline cutter: browse Creality Scan projects, load a project's
acquisition-ordered point cloud (read straight from the plain-text pc_after.ply,
no proprietary SDK needed), and delete time intervals by removing those points
from the project's registered clouds (pc_after.ply + pc_before.ply) in a copy.
The user then re-fuses the edited project in Creality Scan — which builds the
mesh from those clouds, so the cut sections are gone.
"""
import os
import sys
import glob as _glob
import json
import uuid
import shutil
import datetime
import threading
import subprocess

import pymeshlab
import trimesh

from cleaner import MeshCleaner
import obscan_sdk

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROCESSED_DIR = os.path.join(BASE_DIR, "processed_downloads")
CREALITY_ROOT = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Creality", "CrealityScan")
PROJECTS_DIR = os.path.join(CREALITY_ROOT, "Projects")
# Creality Scan's own project registry — a folder alone won't show up in the
# app unless it's listed here, so we add the edited copy to it.
PROJECTS_DB = os.path.join(CREALITY_ROOT, "projects.dat")
# Edited copies land next to the originals with this suffix so they appear in
# Creality Scan's own project list, ready to re-fuse.
CUT_SUFFIX = "_vagott"


def _dir_size(path):
    return sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(path) for f in fs)


def _copy_project(src_dir, work_dir):
    """Copy a project so the edit never touches the original.

    Scans are gigabytes, so check there is room first and then check that
    robocopy actually succeeded. Its exit code used to be ignored entirely: a
    copy that ran out of disk left a half-written project that the cut then
    edited and registered, handing the user a broken scan with no error."""
    need = _dir_size(src_dir)
    free = shutil.disk_usage(os.path.dirname(work_dir)).free
    if free < need * 1.1:
        raise HTTPException(
            507, f"Nincs eleg hely a masolathoz: {need / 2**30:.1f} GB kellene, "
                 f"{free / 2**30:.1f} GB szabad. Szabadits fel helyet, es probald ujra.")
    result = subprocess.run(
        ["robocopy", src_dir, work_dir, "/E", "/MT:16", "/J",
         "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
        capture_output=True,
    )
    # robocopy is not a normal exit-code citizen: 0-7 are success variants
    # (files copied, extras present, ...), 8 and above are real failures.
    if result.returncode >= 8:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(
            500, f"A projekt masolasa nem sikerult (robocopy {result.returncode}). "
                 "A felmasolt reszt eltavolitottam.")


def _register_in_creality(work_dir):
    """Add the edited copy to Creality Scan's projects.dat so it shows up in
    the app's project list. Creality Scan must be closed, or it will overwrite
    the file on exit."""
    if not os.path.isfile(PROJECTS_DB):
        return
    try:
        with open(PROJECTS_DB, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    win_path = os.path.normpath(work_dir)
    if any(os.path.normpath(e.get("Path", "")) == win_path for e in entries):
        return  # already registered
    now = datetime.datetime.now().astimezone().isoformat()
    entries.append({
        "Version": 3,
        "Id": str(uuid.uuid4()),
        "Path": win_path,
        "Name": os.path.basename(work_dir),
        "Date": "0001-01-01T00:00:00",
        "CreationDate": now,
        "ModifiedDate": now,
        "Size": _dir_size(work_dir),
        "IsTrashed": False,
    })
    tmp = PROJECTS_DB + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f)
    os.replace(tmp, PROJECTS_DB)

router = APIRouter(prefix="/api/raw", tags=["raw-editor"])


def _is_creality_available():
    return os.path.isdir(PROJECTS_DIR)


class LoadRequest(BaseModel):
    obp: str          # full path to the source project's project.obp
    reset: bool = False   # re-read even if cached
    # Decimating a multi-million-face fused mesh takes a while and the viewer
    # hides it by default, so it is only built when actually asked for.
    with_mesh: bool = False


class FramesRequest(BaseModel):
    obp: str
    reset: bool = False     # rebuild even if a cached frame cloud exists


class CutRequest(BaseModel):
    obp: str            # the ORIGINAL project's project.obp (copied on cut)
    # Every cut section, as [start_pct, end_pct] over the ORIGINAL point cloud
    # (the slider works in point/time order). The copy is always rebuilt from the
    # original minus the union of these, so repeated cuts never drift.
    ranges: list = []
    # Exact frame numbers to delete, when the view is the frame timeline. With
    # these the cut needs no percentage->frame guesswork at all: the frames the
    # user saw disappear are the frames that go.
    frames: list = []
    # Backwards-compat: an older cached frontend sends a single interval this way
    # instead of `ranges`. Accept it so a stale browser still cuts correctly.
    start_pct: float | None = None
    end_pct: float | None = None
    # SDK path only: rebuild the mesh after cutting. Off means the cut project
    # comes back with a stale mesh and the user fuses it in Creality Scan —
    # much faster, and the only option on a machine short of memory.
    refuse: bool = True


@router.get("/available")
def available():
    return {"available": _is_creality_available()}


@router.get("/thumb")
def thumb(path: str):
    # Only serve thumbnail.png files that live under the Creality projects dir.
    norm = os.path.normpath(path)
    if not norm.startswith(os.path.normpath(PROJECTS_DIR)) or os.path.basename(norm) != "thumbnail.png":
        raise HTTPException(403, "Nem engedélyezett elérési út.")
    if not os.path.isfile(norm):
        raise HTTPException(404, "Nincs bélyegkép.")
    return FileResponse(norm, media_type="image/png")


@router.get("/projects")
def projects():
    """List Creality Scan projects by scanning the projects folder directly —
    no SDK required. Skips our own `_vagott` edited copies."""
    if not _is_creality_available():
        raise HTTPException(400, "CrealityScan projektek nem találhatók ezen a gépen.")
    out = []
    for obp in _glob.glob(os.path.join(PROJECTS_DIR, "*", "project.obp")):
        proj = os.path.dirname(obp)
        if os.path.basename(proj).endswith(CUT_SUFFIX):
            continue
        thumb_path = os.path.join(proj, "thumbnail.png")
        out.append({
            "name": os.path.basename(proj),
            "obp": obp,
            "mtime": os.path.getmtime(proj),
            "thumbnail": thumb_path if os.path.exists(thumb_path) else None,
        })
    out.sort(key=lambda p: p["mtime"], reverse=True)
    return out


def _work_dir_for(name):
    return os.path.join(PROJECTS_DIR, name + CUT_SUFFIX)


def _find_fused_mesh(work_dir):
    """Locate the project's fused surface mesh (the one Creality shows). Layouts
    vary, so prefer the clean meshed surface (Mesh.ply) — it's already display
    sized (~1M faces) and aligns with the cloud — over the raw Fused.ply, which
    can be 8M+ faces and murder-slow to decimate. Fall back to whatever has
    faces (some result files, e.g. model.ply, are empty).

    Only the PLY HEADER is read to decide. Loading each candidate with pymeshlab
    just to call face_number() meant parsing multi-million-face meshes on every
    project open — half a second or more spent answering a yes/no question about
    a surface the viewer hides by default."""
    candidates = _glob.glob(os.path.join(work_dir, "*", "result", "*.ply"))

    def rank(p):
        b = os.path.basename(p).lower()
        return 0 if b == "mesh.ply" else 1 if b == "model.ply" else 2

    for ply in sorted(candidates, key=rank):
        if _ply_face_count(ply) > 0:
            return ply
    return None


def _ply_face_count(path):
    """Faces declared in a PLY header, or 0 if it has none / cannot be read."""
    try:
        with open(path, "rb") as f:
            header = b""
            while b"end_header" not in header:
                chunk = f.read(4096)
                if not chunk:
                    return 0
                header += chunk
                if len(header) > 1 << 16:      # not a PLY header we understand
                    return 0
    except OSError:
        return 0
    for line in header.split(b"\n"):
        if line.startswith(b"element face"):
            try:
                return int(line.split()[2])
            except (IndexError, ValueError):
                return 0
    return 0


def _find_sdk_cloud(proj_dir):
    """The scan cloud the SDK works on, as written to disk (`result/model.ply`).

    Verified identical, element for element, to what obscan_scan_get_raw_cloud
    returns — so its index is a stable identity for a point, and the viewer can
    draw the very array the cut addresses."""
    hits = _glob.glob(os.path.join(proj_dir, "*", "result", "model.ply"))
    return hits[0] if hits else None


def _read_sdk_cloud_xyz(path):
    """model.ply as packed float32 xyz in METRES (it is stored in mm), in file
    order — that order is the timeline the slider runs on."""
    import numpy as np
    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(path)
    v = (ms.current_mesh().vertex_matrix() / 1000.0).astype("float32")
    return np.ascontiguousarray(v).tobytes()


def _session_dir(proj_dir):
    """The session subfolder that holds the scan itself (resources.obscan)."""
    hits = _glob.glob(os.path.join(proj_dir, "*", "resources.obscan"))
    return os.path.dirname(hits[0]) if hits else None


def _frames_available(proj_dir):
    """Whether this project can be shown as a frame timeline. Needs the frames
    themselves and the installed library that decodes them — without it the
    viewer falls back to the pc_after cloud, which is still a real timeline,
    only a coarser one."""
    return bool(_session_dir(proj_dir)) and obscan_sdk.is_available()


def _find_capture_cloud(proj_dir):
    """Find the acquisition-ordered raw cloud. `pc_after.ply`/`pc_before.ply`
    are plain PLYs the scanner writes in CAPTURE ORDER (verified: a point's
    position in the file is its position in scan time) — unlike the SDK's fused
    cloud, which is voxel-ordered. This is what makes a real time slider work,
    with no codec / SDK / scanner needed."""
    for name in ("pc_after.ply", "pc_before.ply"):
        hits = _glob.glob(os.path.join(proj_dir, "*", name))
        if hits:
            return hits[0]
    return None


def _read_ply_xyz(path):
    """Read a binary-little-endian PLY's vertices and return packed float32 xyz
    (dropping any extra per-vertex properties like normals), preserving order."""
    with open(path, "rb") as f:
        header = b""
        while b"end_header\n" not in header:
            chunk = f.read(1)
            if not chunk:
                raise ValueError("nem PLY fejléc")
            header += chunk
        lines = header.split(b"\n")
        n = int(next(l for l in lines if l.startswith(b"element vertex")).split()[-1])
        nprop = sum(1 for l in lines if l.startswith(b"property"))
        import numpy as np
        data = np.frombuffer(f.read(n * nprop * 4), dtype=np.float32).reshape(-1, nprop)
        return np.ascontiguousarray(data[:, :3]).tobytes()


def _export_mesh_glb(work_dir, out_glb):
    """Decimate the fused surface mesh and export it as GLB (scaled to metres to
    match the point cloud). Returns True on success. Huge meshes use fast
    clustering decimation; moderate ones use higher-quality quadric collapse —
    both keep browser rendering (and this export) quick."""
    ply = _find_fused_mesh(work_dir)
    if not ply:
        return False
    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(ply)
    nf = ms.current_mesh().face_number()
    if nf == 0:
        return False
    if nf > 1_200_000:
        # quadric collapse on multi-million-face meshes takes minutes; vertex
        # clustering gets a usable display surface in ~1s.
        ms.meshing_decimation_clustering(threshold=pymeshlab.PercentageValue(0.3))
    elif nf > 250_000:
        ms.meshing_decimation_quadric_edge_collapse(targetfacenum=250_000, preservenormal=True)
    # scan units are mm; scale to metres so it lines up with the raw cloud
    ms.compute_matrix_from_scaling_or_normalization(
        axisx=0.001, axisy=0.001, axisz=0.001, uniformflag=True)
    temp_obj = out_glb.replace(".glb", "_temp.obj")
    ms.save_current_mesh(temp_obj)
    try:
        trimesh.load(temp_obj).export(out_glb)
    finally:
        if os.path.exists(temp_obj):
            os.remove(temp_obj)
    return True


def _capture_cache_current(src_dir, cloud_bin):
    """Whether the cached pc_after cloud came from the cloud we serve today.

    It carries no provenance, so compare its point count with the source's: an
    experiment that cached a different cloud once left the viewer showing
    voxel-ordered points while the code had already moved back to pc_after, and
    nothing revealed the mismatch."""
    if not os.path.exists(cloud_bin):
        return False
    src = _find_capture_cloud(src_dir)
    if not src:
        return False
    try:
        _, n_src, _, _ = _ply_parts(src)
    except (OSError, ValueError, StopIteration):
        return False
    return os.path.getsize(cloud_bin) // 12 == n_src


def _build_capture_cloud(src_dir, cloud_bin):
    """Cache pc_after.ply as packed xyz. Only pc_after carries scan time: it is
    written in capture order, so a point's place in the file is its place in
    time. The SDK's own cloud is ordered spatially (voxel traversal) — scrubbing
    it builds the model up in scattered patches — so it cannot back a timeline."""
    ply = _find_capture_cloud(src_dir)
    if not ply:
        raise HTTPException(404, "Ebben a projektben nincs pc_after.ply (nyers, "
                                 "időrendes pontfelhő) — nem szerkeszthető így.")
    with open(cloud_bin, "wb") as f:
        f.write(_read_ply_xyz(ply))


@router.post("/load")
def load(req: LoadRequest):
    """Open a project: report what it can be viewed as, and build nothing that
    is not going to be looked at.

    Both heavy parts are on demand. The surface overlay is off by default, so
    /mesh builds it when the user switches it on. The pc_after cloud is only the
    fallback timeline when the frames cannot be read, so /cloud builds it if it
    is actually needed — reading it for a full body scan costs a couple of
    seconds that the frame timeline would then throw away."""
    if not os.path.isfile(req.obp):
        raise HTTPException(404, "A projektfájl nem található.")
    src_dir = os.path.dirname(req.obp)
    name = os.path.basename(src_dir)
    cloud_bin = os.path.join(PROCESSED_DIR, f"{name}_cloud.bin")
    mesh_glb = os.path.join(PROCESSED_DIR, f"{name}_mesh.glb")

    frames_ok = _frames_available(src_dir)
    have_cloud = not req.reset and _capture_cache_current(src_dir, cloud_bin)
    # Without the frame timeline the pc_after cloud IS the view, so build it now
    # rather than making the viewer ask for what it is certain to need.
    if not frames_ok and not have_cloud:
        _build_capture_cloud(src_dir, cloud_bin)
        have_cloud = True

    if req.with_mesh and not os.path.exists(mesh_glb):
        try:
            _export_mesh_glb(src_dir, mesh_glb)  # reads the fused surface read-only
        except Exception:
            pass  # mesh view is optional; points always work

    return {
        "name": name, "obp": req.obp,
        # which cloud the viewer is drawing: the cut MUST address the same one,
        # or the slider and the deletion mean different things again
        "cloud_source": "capture",
        "num_points": os.path.getsize(cloud_bin) // 12 if have_cloud else None,
        # None means "not built" — ask /cloud for it
        "cloud_url": f"/static/processed/{name}_cloud.bin" if have_cloud else None,
        "mesh_url": (f"/static/processed/{name}_mesh.glb"
                     if os.path.exists(mesh_glb) else None),
        "mesh_available": _find_fused_mesh(src_dir) is not None,
        # the exact timeline is a separate, slower load (/frames); telling the
        # viewer up front lets it go straight for it
        "frames_available": frames_ok,
        "cached": have_cloud,
    }


@router.post("/cloud")
def cloud(req: LoadRequest):
    """Build (or return) the pc_after fallback cloud. The viewer asks for this
    only when the frame timeline is unavailable or failed."""
    if not os.path.isfile(req.obp):
        raise HTTPException(404, "A projektfájl nem található.")
    src_dir = os.path.dirname(req.obp)
    name = os.path.basename(src_dir)
    cloud_bin = os.path.join(PROCESSED_DIR, f"{name}_cloud.bin")
    if req.reset or not _capture_cache_current(src_dir, cloud_bin):
        _build_capture_cloud(src_dir, cloud_bin)
    return {
        "cloud_source": "capture",
        "cloud_url": f"/static/processed/{name}_cloud.bin",
        "num_points": os.path.getsize(cloud_bin) // 12,
    }


@router.post("/mesh")
def mesh(req: LoadRequest):
    """Build the surface overlay for a project already loaded without it. This
    is the slow half of loading, so the viewer asks for it only when the user
    turns the overlay on."""
    if not os.path.isfile(req.obp):
        raise HTTPException(404, "A projektfájl nem található.")
    src_dir = os.path.dirname(req.obp)
    name = os.path.basename(src_dir)
    mesh_glb = os.path.join(PROCESSED_DIR, f"{name}_mesh.glb")
    if not os.path.exists(mesh_glb):
        try:
            if not _export_mesh_glb(src_dir, mesh_glb):
                raise HTTPException(404, "Ebben a projektben nincs fúzionált felület.")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(500, "A felület előkészítése nem sikerült.")
    return {"mesh_url": f"/static/processed/{name}_mesh.glb"}


# ---------------------------------------------------------------------------
# The exact timeline: the scan rebuilt from its own depth frames.
#
# Every frame is one instant and carries its own pose, so placing them in a
# common coordinate system gives a cloud whose points are grouped BY FRAME in
# scan order. A slider position then names actual frames — and those frames are
# what the cut deletes, so what the user sees disappear is what disappears.
#
# Decoding a full body scan takes ~30 s, so this is a job with progress, and the
# result is cached next to the project's other derived files.
# ---------------------------------------------------------------------------
_JOBS = {}
_JOBS_LOCK = threading.Lock()

# How many points the frame cloud may hold in total, shared equally between the
# frames. It costs no decoding time (measured: 3M, 8M and 20M all take ~30 s on
# a 2401-frame scan) — only file size and browser memory.
FRAME_POINT_BUDGET = 20_000_000


def _set(job_id, **kw):
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(kw)


def _frames_paths(name):
    return (os.path.join(PROCESSED_DIR, f"{name}_frames.bin"),
            os.path.join(PROCESSED_DIR, f"{name}_frames.json"))


def _frames_cached(name, session_dir):
    """The cached frame cloud, if it was built from the scan as it is now.

    The manifest records the source obscan's size and mtime: a cache with no
    provenance is how the viewer once ended up drawing one cloud while the cut
    addressed another."""
    cloud_bin, manifest_json = _frames_paths(name)
    if not (os.path.exists(cloud_bin) and os.path.exists(manifest_json)):
        return None
    try:
        with open(manifest_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        st = os.stat(os.path.join(session_dir, "resources.obscan"))
    except (OSError, ValueError):
        return None
    if data.get("source_size") != st.st_size or data.get("source_mtime") != int(st.st_mtime):
        return None
    # a cloud built to a different point budget is stale even if the scan is not
    if data.get("max_points") != FRAME_POINT_BUDGET:
        return None
    if os.path.getsize(cloud_bin) // 12 != data.get("num_points"):
        return None
    return data


def _frames_reply(name, data):
    return {
        "name": name,
        "cloud_source": "frames",
        "cloud_url": f"/static/processed/{name}_frames.bin",
        "frames": data["frames"],            # [[frame number, point count], ...]
        "num_frames": data["num_frames"],
        "num_points": data["num_points"],
        "total_frames": data.get("total_frames"),
        # frames the registration gave up on: not on the timeline, and the
        # fusion skips them too
        "no_pose_frames": data.get("no_pose_frames"),
        # on the timeline but with nothing to draw — still cut with their section
        "blank_frames": data.get("blank_frames"),
    }


def _run_frames(job_id, obp):
    src_dir = os.path.dirname(obp)
    name = os.path.basename(src_dir)
    session_dir = _session_dir(src_dir)
    cloud_bin, manifest_json = _frames_paths(name)
    try:
        _set(job_id, stage="open", progress=0.0)
        proc = subprocess.Popen(
            [sys.executable, os.path.join(BASE_DIR, "frame_reader.py")],
            cwd=BASE_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        proc.stdin.write(json.dumps({
            "session_dir": session_dir, "out_bin": cloud_bin,
            "log_dir": os.path.join(BASE_DIR, "sdk_logs"),
            "max_points": FRAME_POINT_BUDGET,
        }))
        proc.stdin.close()

        result = None
        # the library prints its own chatter to stdout as well, so keep only the
        # lines that parse as our protocol
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "ok" in msg:
                result = msg
            elif "stage" in msg:
                # decoding is all of the wait; give it the whole bar
                _set(job_id, stage=msg["stage"],
                     progress=round(0.02 + 0.98 * msg.get("progress", 0.0), 4))
        proc.wait()

        if not result or not result.get("ok"):
            raise RuntimeError((result or {}).get("error")
                               or "A képkockák beolvasása ismeretlen hibával állt le.")

        st = os.stat(os.path.join(session_dir, "resources.obscan"))
        result["source_size"] = st.st_size
        result["source_mtime"] = int(st.st_mtime)
        with open(manifest_json, "w", encoding="utf-8") as f:
            json.dump(result, f)
        _set(job_id, state="done", stage="done", progress=1.0,
             result=_frames_reply(name, result))
    except Exception as exc:                       # noqa: BLE001 - surfaced to the UI
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        _set(job_id, state="error", error=detail)


@router.post("/frames")
def frames(req: FramesRequest):
    """Load the frame timeline. Returns the finished result straight away when
    it is cached, otherwise a job id to poll."""
    if not os.path.isfile(req.obp):
        raise HTTPException(404, "A projektfájl nem található.")
    src_dir = os.path.dirname(req.obp)
    name = os.path.basename(src_dir)
    session_dir = _session_dir(src_dir)
    if not session_dir:
        raise HTTPException(404, "Ebben a projektben nincs resources.obscan.")
    if not obscan_sdk.is_available():
        raise HTTPException(400, "A Creality Scan nincs telepítve, így a "
                                 "képkockák nem dekódolhatók.")

    if not req.reset:
        cached = _frames_cached(name, session_dir)
        if cached:
            return {"ready": True, **_frames_reply(name, cached)}

    job_id = str(uuid.uuid4())
    with _JOBS_LOCK:
        if any(j.get("state") == "running" and j.get("frames_for") == name
               for j in _JOBS.values()):
            raise HTTPException(409, "Ezt a projektet már olvassa egy másik betöltés.")
        _JOBS[job_id] = {"state": "running", "stage": "open", "progress": 0.0,
                         "frames_for": name}
    threading.Thread(target=_run_frames, args=(job_id, req.obp), daemon=True).start()
    return {"ready": False, "job_id": job_id}


@router.get("/frames/{job_id}")
def frames_status(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "Ismeretlen feladat.")
        return dict(job)


def _ply_parts(path):
    """Return (header_bytes, vertex_count, prop_count, body_bytes) for a
    binary-little-endian float32 PLY, preserving the header verbatim."""
    import numpy as np
    with open(path, "rb") as f:
        header = b""
        while b"end_header\n" not in header:
            chunk = f.read(1)
            if not chunk:
                raise ValueError("nem PLY fejléc")
            header += chunk
        lines = header.split(b"\n")
        n = int(next(l for l in lines if l.startswith(b"element vertex")).split()[-1])
        nprop = sum(1 for l in lines if l.startswith(b"property"))
        body = np.frombuffer(f.read(n * nprop * 4), dtype="<f4").reshape(n, nprop)
    return header, n, nprop, body


def _frame_axis(obscan_path):
    """Sorted (frame_number, blob_size) for every depth frame in a
    resources.obscan. Only reads name + length(data), so it stays fast on
    multi-GB files. The blob size tracks how many valid pixels (= points) the
    frame contributed, which lets us map slider percentages onto frames."""
    import re as _re
    import sqlite3
    con = sqlite3.connect(f"file:{obscan_path.replace(chr(92), '/')}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT name, length(data) FROM files WHERE name LIKE 'd~%'").fetchall()
    finally:
        con.close()
    out = []
    for nm, sz in rows:
        m = _re.match(r"d~0*(\d+)", nm)
        if m:
            out.append((int(m.group(1)), sz or 0))
    out.sort()
    return out


def _pct_to_index(axis, pct):
    """Map a slider percentage to a POSITION in the capture-ordered frame list —
    the same way the point mask maps it onto the cloud, so the frames deleted and
    the points deleted describe the same slice of scan time.

    This used to weight frames by their compressed blob size, on the assumption
    that size tracks how many points a frame contributed. It does not: every
    frame is a fixed 640x400 depth image, so the stored size is a compression
    artefact of that image's entropy. Measured against the real frame lists it
    put the cut between 0.6x and 2.2x away from the selection (e.g. a 7.8%
    selection deleted 12.5% of the scan), which is why cuts removed the wrong
    section."""
    k = len(axis)
    return max(0, min(k, int(round(max(0.0, min(1.0, pct / 100.0)) * k))))


def _cut_ply(src, dst, remove_mask):
    """Copy `src` PLY to `dst` with the masked-out vertices removed, rewriting the
    vertex count in the header. Points are dropped by array index, i.e. by their
    place in scan time — exactly the section the user isolated on the slider."""
    header, n, nprop, body = _ply_parts(src)
    keep = body[~remove_mask[:n]]
    new_header = header.replace(
        b"element vertex %d\n" % n, b"element vertex %d\n" % len(keep))
    with open(dst, "wb") as f:
        f.write(new_header)
        f.write(keep.astype("<f4").tobytes())
    return n, len(keep)


@router.post("/cut")
def cut(req: CutRequest):
    """Remove the selected time sections from a copy of the project, then let the
    user re-fuse in Creality Scan.

    What fusion actually reads is the raw depth frames in resources.obscan, so
    deleting frames is what drops a section — proven by feeding fusion a
    5%-point cloud and still getting a full body back. The registered clouds
    (pc_after.ply + pc_before.ply) are cut alongside them so the project stays
    self-consistent.

    The frame view sends the frame numbers it displayed, and those exact frames
    go. The older cloud view only has percentages, which are mapped onto the
    frame list by position. Either way the copy is rebuilt from the original
    minus the whole selection, so repeated cuts never drift."""
    import numpy as np
    if not os.path.isfile(req.obp):
        raise HTTPException(404, "A projektfájl nem található.")
    src_dir = os.path.dirname(req.obp)
    name = os.path.basename(src_dir)
    work_dir = _work_dir_for(name)
    work_id = name + CUT_SUFFIX

    doomed_frames = sorted({int(x) for x in req.frames})

    # the clouds Creality fuses from (same session subfolder, same point order)
    src_after = _glob.glob(os.path.join(src_dir, "*", "pc_after.ply"))
    src_before = _glob.glob(os.path.join(src_dir, "*", "pc_before.ply"))
    src_obscan = _glob.glob(os.path.join(src_dir, "*", "resources.obscan"))
    if not src_after and not doomed_frames:
        raise HTTPException(400, "Ebben a projektben nincs pc_after.ply pontfelhő.")

    # a frame selection is checked against the frames actually in the scan, so a
    # request that would empty the project is refused before anything is copied
    if doomed_frames:
        if not src_obscan:
            raise HTTPException(400, "Ebben a projektben nincs resources.obscan.")
        if len(doomed_frames) >= len(_frame_axis(src_obscan[0])):
            raise HTTPException(400, "A teljes szken ki lenne vágva — szűkítsd a kijelölést.")

    total_points = 0
    mask = None
    # accept both the new `ranges` list and a legacy single start/end interval
    ranges = list(req.ranges)
    if not ranges and req.start_pct is not None and req.end_pct is not None:
        ranges = [[req.start_pct, req.end_pct]]
    # keep the ranges that actually select something — the frame cut below must
    # use exactly these, or a malformed entry would be skipped here and still
    # blow up (or delete frames) there.
    valid_ranges = []
    if src_after:
        _, total_points, _, _ = _ply_parts(src_after[0])
        mask = np.zeros(total_points, dtype=bool)
        for r in ranges:
            try:
                s, e = float(r[0]), float(r[1])
            except (TypeError, ValueError, IndexError):
                continue
            a = max(0, min(total_points, int(round(s / 100.0 * total_points))))
            b = max(0, min(total_points, int(round(e / 100.0 * total_points))))
            if b > a:
                mask[a:b] = True
                valid_ranges.append((s, e))
    removed = int(mask.sum()) if mask is not None else 0
    if not doomed_frames:
        if removed == 0:
            # No points selected — almost always a stale frontend that didn't
            # send the cut range. Fail loudly instead of silently writing a full
            # copy.
            raise HTTPException(
                400,
                "A vágás 0 pontot távolítana el (nem érkezett kijelölés). Zárd be "
                "teljesen a programot ÉS a böngészőt, majd indítsd újra, hogy a "
                "javított verzió töltődjön be.")
        if removed >= total_points:
            raise HTTPException(400, "A teljes szken ki lenne vágva — szűkítsd a kijelölést.")

    # make/refresh the copy (one-time full copy; then only the light PLYs change)
    if not os.path.isdir(work_dir):
        _copy_project(src_dir, work_dir)

    # rewrite the copy's clouds from the ORIGINAL, minus the cut sections
    def _rel_in_copy(src_ply):
        rel = os.path.relpath(src_ply, src_dir)
        return os.path.join(work_dir, rel)

    if src_after and mask is not None and mask.any():
        _cut_ply(src_after[0], _rel_in_copy(src_after[0]), mask)
    if src_before and valid_ranges:
        # pc_before is NOT a positional twin of pc_after: it can hold a
        # different number of points (75 801 vs 76 860 on one scan here) and a
        # different order (points at the same index sit ~191 mm apart). Reusing
        # pc_after's mask crashed outright; apply the same percentage range over
        # pc_before's own count instead.
        _, before_total, _, _ = _ply_parts(src_before[0])
        before_mask = np.zeros(before_total, dtype=bool)
        for s, e in valid_ranges:
            a = max(0, min(before_total, int(round(s / 100.0 * before_total))))
            b = max(0, min(before_total, int(round(e / 100.0 * before_total))))
            if b > a:
                before_mask[a:b] = True
        _cut_ply(src_before[0], _rel_in_copy(src_before[0]), before_mask)

    # THE cut that fusion actually sees: Creality re-fuses from the RAW DEPTH
    # FRAMES inside resources.obscan, not from the pc_*.ply exports (proven by
    # feeding fusion a 5%-point cloud and still getting a full-body result). So
    # delete the selected sections' d~/c~ frames from the copy's obscan. The
    # copy's obscan is refreshed from the original first, so repeated cuts are
    # always applied to a clean base and never drift.
    frames_deleted = frames_remaining = frames_total = None
    if src_obscan:
        import re as _re
        import sqlite3
        dst_obscan = _rel_in_copy(src_obscan[0])
        try:
            shutil.copy2(src_obscan[0], dst_obscan)
            # the fresh main file must not be paired with the copy's stale
            # WAL/SHM sidecars — sync them with the original (its WAL is empty
            # after a clean close) or drop them.
            for ext in ("-wal", "-shm"):
                if os.path.exists(src_obscan[0] + ext):
                    shutil.copy2(src_obscan[0] + ext, dst_obscan + ext)
                elif os.path.exists(dst_obscan + ext):
                    os.remove(dst_obscan + ext)
        except OSError:
            raise HTTPException(
                409, "Nem tudom frissíteni a másolat adatbázisát — zárd be a "
                     "Creality Scant, és vágj újra.")
        axis = _frame_axis(dst_obscan)
        if axis:
            frames_total = len(axis)
            # The frame timeline sends the exact frame numbers it showed the
            # user, so nothing has to be inferred. Deleting precisely those is
            # also why frames the registration gave up on stay: they are not on
            # the timeline, and the fusion skips them anyway.
            doomed = set(doomed_frames)
            # Without it (the pc_after fallback view) the selection is still a
            # percentage, mapped to closed frame-NUMBER windows via list
            # positions. Frame numbers are not contiguous — dropped frames leave
            # gaps — but they are ordered, so a number window still selects a
            # contiguous run of scan time.
            windows = []
            if not doomed:
                for s, e in valid_ranges:
                    a = _pct_to_index(axis, s)
                    b = _pct_to_index(axis, e)
                    if b > a:
                        windows.append((axis[a][0], axis[b - 1][0]))

            def _is_doomed(num):
                if doomed:
                    return num in doomed
                return any(lo <= num <= hi for lo, hi in windows)

            con = sqlite3.connect(dst_obscan)
            try:
                cur = con.cursor()
                frames_deleted = 0
                for (nm,) in con.execute(
                        "SELECT name FROM files WHERE name LIKE 'd~%' OR name LIKE 'c~%'").fetchall():
                    # colour frames share the depth frames' numbering, so the
                    # same selection picks up their matching frames too
                    m = _re.match(r"[dc]~0*(\d+)", nm)
                    if m and _is_doomed(int(m.group(1))):
                        cur.execute("DELETE FROM files WHERE name=?", (nm,))
                        if nm.startswith("d~"):
                            frames_deleted += 1
                con.commit()
                con.execute("VACUUM")
                frames_remaining = con.execute(
                    "SELECT COUNT(*) FROM files WHERE name LIKE 'd~%'").fetchone()[0]
            finally:
                con.close()
            # keep the copy's advertised frame count in sync with reality
            pi = os.path.join(work_dir, "ProjectInfo.json")
            if frames_remaining is not None and os.path.isfile(pi):
                try:
                    with open(pi, "r", encoding="utf-8") as f:
                        info = json.load(f)
                    info["FrameCount"] = frames_remaining
                    with open(pi, "w", encoding="utf-8") as f:
                        json.dump(info, f, ensure_ascii=False)
                except (OSError, ValueError):
                    pass

    # A frame selection that matches nothing means the view and the project have
    # drifted apart (a stale timeline after an earlier cut, say). Say so instead
    # of handing back a copy that is identical to the original.
    if doomed_frames and not frames_deleted:
        raise HTTPException(
            409, "A kijelölt képkockák nem találhatók a projektben. Töltsd be "
                 "újra a projektet, és vágj újra.")

    # drop the fusion working cache so Creality rebuilds instead of reusing it
    for cache in _glob.glob(os.path.join(work_dir, "*", "temp", "*.obmc")):
        try:
            os.remove(cache)
        except OSError:
            pass

    _register_in_creality(work_dir)
    return {
        "work_id": work_id,
        "work_dir": work_dir,
        # which selection actually drove the deletion — the frame view addresses
        # frames directly, the older cloud view goes through percentages
        "cut_by": "frames" if doomed_frames else "range",
        "removed_points": removed,
        "remaining_points": total_points - removed,
        "total_points": total_points,
        "deleted_frames": frames_deleted,
        "remaining_frames": frames_remaining,
        "total_frames": frames_total,
    }


# ---------------------------------------------------------------------------
# SDK path: let Creality's own SDK do the cut and rebuild the mesh, so the
# edited project comes back finished instead of the user having to re-fuse it
# by hand in Creality Scan.
#
# The SDK runs in a subprocess (see obscan_sdk) and re-fusing a full body scan
# takes minutes, so this is a job with progress rather than one blocking call.
# ---------------------------------------------------------------------------
# stage -> where it starts on the overall bar, so the UI can show one honest
# progress figure. The stage name is sent as-is and the frontend translates it —
# the app is bilingual, so labels must not be baked in here.
_STAGES = {
    "copy":   0.00,
    "open":   0.30,
    "cloud":  0.35,
    "match":  0.40,
    "delete": 0.45,
    "fuse":   0.50,
}


def _overall(stage, progress):
    base = _STAGES.get(stage, 0.5)
    nxt = 1.0 if stage == "fuse" else min(
        [s for s in _STAGES.values() if s > base] or [1.0])
    return round(base + (nxt - base) * max(0.0, min(1.0, progress)), 4)


def _run_sdk_cut(job_id, obp, ranges, do_refuse=True):
    src_dir = os.path.dirname(obp)
    name = os.path.basename(src_dir)
    work_dir = _work_dir_for(name)
    try:
        # The SDK edit is written into the project, so repeated cuts would
        # compound. Always start from a clean copy of the original — the
        # frontend sends every range each time, so nothing is lost.
        _set(job_id, stage="copy", progress=0.0)
        if os.path.isdir(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
        _copy_project(src_dir, work_dir)

        work_obp = os.path.join(work_dir, os.path.basename(obp))

        proc = subprocess.Popen(
            [sys.executable, os.path.join(BASE_DIR, "obscan_sdk.py")],
            cwd=BASE_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        proc.stdin.write(json.dumps({
            "work_obp": work_obp, "ranges": ranges,
            "log_dir": os.path.join(BASE_DIR, "sdk_logs"), "refuse": do_refuse,
        }))
        proc.stdin.close()

        result = None
        # the SDK writes its own chatter to stdout too, so take only the lines
        # that parse as our JSON protocol and ignore the rest
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "ok" in msg:
                result = msg
            elif "stage" in msg:
                stage = msg["stage"]
                _set(job_id, stage=stage,
                     progress=_overall(stage, msg.get("progress", 0.0)))
        proc.wait()

        if not result or not result.get("ok"):
            raise RuntimeError((result or {}).get("error")
                               or "Az SDK-s vagas ismeretlen hibaval allt le.")

        _register_in_creality(work_dir)
        result.update(work_id=name + CUT_SUFFIX, work_dir=work_dir)
        _set(job_id, state="done", stage="done", progress=1.0, result=result)
    except Exception as exc:                       # noqa: BLE001 - surfaced to the UI
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        _set(job_id, state="error", error=detail)


@router.get("/sdk_available")
def sdk_available():
    """Whether the SDK path can be used. False just means we fall back to the
    manual cut — the tool works either way."""
    return {"available": obscan_sdk.is_available()}


@router.post("/cut_sdk")
def cut_sdk(req: CutRequest):
    """Start an SDK cut. Returns a job id to poll — re-fusing takes minutes."""
    if not obscan_sdk.is_available():
        raise HTTPException(400, "A Creality Scan SDK nem talalhato ezen a gepen.")
    if not os.path.isfile(req.obp):
        raise HTTPException(404, "A projektfajl nem talalhato.")
    if not _find_sdk_cloud(os.path.dirname(req.obp)):
        raise HTTPException(
            400, "Ebben a projektben nincs model.ply, igy az SDK-s vagas nem "
                 "cimezheto - hasznald a sima vagast.")
    ranges = [[float(r[0]), float(r[1])] for r in req.ranges
              if len(r) >= 2 and float(r[1]) > float(r[0])]
    if not ranges and req.start_pct is not None and req.end_pct is not None:
        ranges = [[req.start_pct, req.end_pct]]
    if not ranges:
        raise HTTPException(400, "Nem erkezett kijelolt szakasz.")

    # One job per project: the copy is deleted and rebuilt at the start, so two
    # cuts of the same scan would delete the directory out from under each other.
    work_dir = _work_dir_for(os.path.basename(os.path.dirname(req.obp)))
    job_id = str(uuid.uuid4())
    with _JOBS_LOCK:
        if any(j.get("state") == "running" and j.get("work_dir") == work_dir
               for j in _JOBS.values()):
            raise HTTPException(409, "Ezen a projekten mar fut egy vagas.")
        _JOBS[job_id] = {"state": "running", "stage": "copy", "progress": 0.0,
                         "work_dir": work_dir}
    threading.Thread(target=_run_sdk_cut,
                     args=(job_id, req.obp, ranges, req.refuse),
                     daemon=True).start()
    return {"job_id": job_id}


@router.get("/cut_sdk/{job_id}")
def cut_sdk_status(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "Ismeretlen feladat.")
        return dict(job)
