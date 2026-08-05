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


class CutRequest(BaseModel):
    obp: str            # the ORIGINAL project's project.obp (copied on cut)
    # Every cut section, as [start_pct, end_pct] over the ORIGINAL point cloud
    # (the slider works in point/time order). The copy is always rebuilt from the
    # original minus the union of these, so repeated cuts never drift.
    ranges: list = []
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
    faces (some result files, e.g. model.ply, are empty)."""
    candidates = _glob.glob(os.path.join(work_dir, "*", "result", "*.ply"))

    def rank(p):
        b = os.path.basename(p).lower()
        return 0 if b == "mesh.ply" else 1 if b == "model.ply" else 2

    for ply in sorted(candidates, key=rank):
        try:
            ms = pymeshlab.MeshSet()
            ms.load_new_mesh(ply)
            if ms.current_mesh().face_number() > 0:
                return ply
        except Exception:
            continue
    return None


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


@router.post("/load")
def load(req: LoadRequest):
    """View a project's scan in ACQUISITION (time) order. Reads pc_after.ply
    directly — a plain PLY the scanner writes in capture order — so no SDK, no
    scanner and no copy are needed, and the original is only ever read."""
    if not os.path.isfile(req.obp):
        raise HTTPException(404, "A projektfájl nem található.")
    src_dir = os.path.dirname(req.obp)
    name = os.path.basename(src_dir)
    cloud_bin = os.path.join(PROCESSED_DIR, f"{name}_cloud.bin")
    mesh_glb = os.path.join(PROCESSED_DIR, f"{name}_mesh.glb")

    # The surface overlay is off by default in the viewer and decimating a
    # multi-million-face mesh is the slow part of loading, so it is only built
    # when asked for — /mesh adds it later if the user switches it on.
    def _reply(cached=False):
        return {
            "name": name, "obp": req.obp,
            # which cloud the viewer is drawing: the cut MUST address the same
            # one, or the slider and the deletion mean different things again
            "cloud_source": "capture",
            "num_points": os.path.getsize(cloud_bin) // 12,
            "cloud_url": f"/static/processed/{name}_cloud.bin",
            "mesh_url": (f"/static/processed/{name}_mesh.glb"
                         if os.path.exists(mesh_glb) else None),
            "mesh_available": _find_fused_mesh(src_dir) is not None,
            "cached": cached,
        }

    # A cached cloud is only usable if it came from the cloud we serve today.
    # It carries no provenance, so compare its point count with the source's:
    # an experiment that cached a different cloud once left the viewer showing
    # voxel-ordered points while the code had already moved back to pc_after,
    # and nothing revealed the mismatch.
    def _cache_is_current():
        src = _find_capture_cloud(src_dir)
        if not src:
            return False
        try:
            _, n_src, _, _ = _ply_parts(src)
        except (OSError, ValueError, StopIteration):
            return False
        return os.path.getsize(cloud_bin) // 12 == n_src

    if not req.reset and os.path.exists(cloud_bin) and _cache_is_current():
        if req.with_mesh and not os.path.exists(mesh_glb):
            try:
                _export_mesh_glb(src_dir, mesh_glb)
            except Exception:
                pass
        return _reply(cached=True)

    # Only pc_after.ply carries scan time: it is written in capture order, so a
    # point's place in the file is its place in time. The SDK's own cloud is
    # ordered spatially (voxel traversal) — scrubbing it builds the model up in
    # scattered patches, not along the scan — so it cannot back the timeline.
    ply = _find_capture_cloud(src_dir)
    if not ply:
        raise HTTPException(404, "Ebben a projektben nincs pc_after.ply (nyers, "
                                 "időrendes pontfelhő) — nem szerkeszthető így.")
    with open(cloud_bin, "wb") as f:
        f.write(_read_ply_xyz(ply))

    if req.with_mesh:
        try:
            _export_mesh_glb(src_dir, mesh_glb)  # reads the fused surface read-only
        except Exception:
            pass  # mesh view is optional; points always work
    return _reply()


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
    """Remove the selected time sections directly from the project's point clouds
    (pc_after.ply + pc_before.ply) in a copy, then let the user re-fuse in
    Creality Scan. Creality fuses the mesh from these registered clouds, NOT from
    the raw depth frames, so editing the clouds is what actually drops a section.
    The clouds are in capture (time) order, so a point's index is its position in
    scan time and the slider selection maps to it 1:1. The copy is always rebuilt
    from the original minus the union of `ranges`, so repeated cuts never drift."""
    import numpy as np
    if not os.path.isfile(req.obp):
        raise HTTPException(404, "A projektfájl nem található.")
    src_dir = os.path.dirname(req.obp)
    name = os.path.basename(src_dir)
    work_dir = _work_dir_for(name)
    work_id = name + CUT_SUFFIX

    # the clouds Creality fuses from (same session subfolder, same point order)
    src_after = _glob.glob(os.path.join(src_dir, "*", "pc_after.ply"))
    src_before = _glob.glob(os.path.join(src_dir, "*", "pc_before.ply"))
    if not src_after:
        raise HTTPException(400, "Ebben a projektben nincs pc_after.ply pontfelhő.")

    _, total_points, _, _ = _ply_parts(src_after[0])
    # accept both the new `ranges` list and a legacy single start/end interval
    ranges = list(req.ranges)
    if not ranges and req.start_pct is not None and req.end_pct is not None:
        ranges = [[req.start_pct, req.end_pct]]
    mask = np.zeros(total_points, dtype=bool)
    # keep the ranges that actually select something — the frame cut below must
    # use exactly these, or a malformed entry would be skipped here and still
    # blow up (or delete frames) there.
    valid_ranges = []
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
    removed = int(mask.sum())
    if removed == 0:
        # No points selected — almost always a stale frontend that didn't send
        # the cut range. Fail loudly instead of silently writing a full copy.
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

    _cut_ply(src_after[0], _rel_in_copy(src_after[0]), mask)
    if src_before:
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
    src_obscan = _glob.glob(os.path.join(src_dir, "*", "resources.obscan"))
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
            # closed frame-NUMBER windows, derived from list positions. Frame
            # numbers are not contiguous (dropped frames leave gaps), but they
            # are ordered, so a number window still selects a contiguous run in
            # scan time — and the colour (c~) frames share this numbering, so
            # the same window picks up their matching frames too.
            windows = []
            for s, e in valid_ranges:
                a = _pct_to_index(axis, s)
                b = _pct_to_index(axis, e)
                if b > a:
                    windows.append((axis[a][0], axis[b - 1][0]))
            con = sqlite3.connect(dst_obscan)
            try:
                cur = con.cursor()
                frames_deleted = 0
                for (nm,) in con.execute(
                        "SELECT name FROM files WHERE name LIKE 'd~%' OR name LIKE 'c~%'").fetchall():
                    m = _re.match(r"[dc]~0*(\d+)", nm)
                    if m and any(lo <= int(m.group(1)) <= hi for lo, hi in windows):
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
_JOBS = {}
_JOBS_LOCK = threading.Lock()

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


def _set(job_id, **kw):
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(kw)


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
