"""
API for the timeline cutter: browse Creality Scan projects, load a project's
acquisition-ordered point cloud (read straight from the plain-text pc_after.ply,
no proprietary SDK needed), and delete a time interval by removing its raw
frames from a copy. The user then re-fuses the edited project in Creality Scan.
"""
import os
import glob as _glob
import json
import uuid
import datetime
import subprocess

import pymeshlab
import trimesh

from cleaner import MeshCleaner

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


class CutRequest(BaseModel):
    obp: str          # the ORIGINAL project's project.obp (copied on cut)
    start_pct: float  # 0..100 — interval start as % of scan time
    end_pct: float    # 0..100 — interval end


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

    # Fast path: already viewed this project — return the cached view instantly.
    if not req.reset and os.path.exists(cloud_bin) and os.path.exists(mesh_glb):
        return {
            "name": name, "obp": req.obp,
            "num_points": os.path.getsize(cloud_bin) // 12,
            "cloud_url": f"/static/processed/{name}_cloud.bin",
            "mesh_url": f"/static/processed/{name}_mesh.glb",
            "cached": True,
        }

    ply = _find_capture_cloud(src_dir)
    if not ply:
        raise HTTPException(404, "Ebben a projektben nincs pc_after.ply (nyers, "
                                 "időrendes pontfelhő) — nem szerkeszthető így.")
    with open(cloud_bin, "wb") as f:
        f.write(_read_ply_xyz(ply))

    mesh_url = None
    try:
        if _export_mesh_glb(src_dir, mesh_glb):  # reads the fused surface read-only
            mesh_url = f"/static/processed/{name}_mesh.glb"
    except Exception:
        mesh_url = None  # mesh view is optional; points always work
    return {
        "name": name, "obp": req.obp,
        "num_points": os.path.getsize(cloud_bin) // 12,
        "cloud_url": f"/static/processed/{name}_cloud.bin",
        "mesh_url": mesh_url,
    }


@router.post("/cut")
def cut(req: CutRequest):
    """Delete a time interval of the scan by removing its RAW FRAMES from a copy
    of the project, so Creality Scan re-fuses without them. Creality fuses from
    the frames (not the derived clouds), so this is what actually makes the cut
    stick. The copy is registered so it shows up in Creality's project list."""
    import re as _re
    import sqlite3
    if not os.path.isfile(req.obp):
        raise HTTPException(404, "A projektfájl nem található.")
    src_dir = os.path.dirname(req.obp)
    name = os.path.basename(src_dir)
    work_dir = _work_dir_for(name)
    work_id = name + CUT_SUFFIX

    if not os.path.isdir(work_dir):
        subprocess.run(
            ["robocopy", src_dir, work_dir, "/E", "/MT:16", "/J",
             "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
            capture_output=True,
        )
    obscan_hits = _glob.glob(os.path.join(work_dir, "*", "resources.obscan"))
    if not obscan_hits:
        raise HTTPException(500, "Nem találom a resources.obscan-t a másolatban.")
    obscan = obscan_hits[0]

    con = sqlite3.connect(obscan)
    try:
        rows = con.execute("SELECT name FROM files WHERE name LIKE 'd~%'").fetchall()
        nums = sorted(int(m.group(1)) for (nm,) in rows
                      for m in [_re.match(r"d~0*(\d+)", nm)] if m)
        if not nums:
            raise HTTPException(400, "Nincsenek nyers képkockák ebben a projektben.")
        # map the time interval to a frame-number window (frames are time-ordered)
        lo = nums[min(len(nums) - 1, int(req.start_pct / 100.0 * len(nums)))]
        hi = nums[min(len(nums) - 1, int(req.end_pct / 100.0 * len(nums)))]
        before = len(nums)
        # delete the depth + colour frames in that window
        cur = con.cursor()
        deleted = 0
        for (nm,) in con.execute("SELECT name FROM files WHERE name LIKE 'd~%' OR name LIKE 'c~%'").fetchall():
            m = _re.match(r"[dc]~0*(\d+)", nm)
            if m and lo <= int(m.group(1)) <= hi:
                cur.execute("DELETE FROM files WHERE name=?", (nm,))
                deleted += 1
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()

    _register_in_creality(work_dir)
    return {
        "work_id": work_id,
        "work_dir": work_dir,
        "before": before,
        "after": before - (hi - lo + 1),
        "deleted_frames": deleted,
        "frame_window": [lo, hi],
    }
