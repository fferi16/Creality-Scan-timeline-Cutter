import React, { useState, useEffect, useRef, useCallback } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader';
import { useT } from '../i18n';

const BACKEND_URL = import.meta.env.DEV ? 'http://127.0.0.1:8000' : '';

// FastAPI returns `detail` as a string (HTTPException) or an array of objects
// (422 validation) — turn either into readable text instead of "[object Object]".
function errText(detail) {
  if (!detail) return 'Ismeretlen hiba';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
  return JSON.stringify(detail);
}

// The cloud is drawn in scan order, so a point's position in the array is its
// position in scan time — that is what makes the timeline work. On the frame
// timeline the points are grouped BY FRAME, so a slider position names actual
// frames and `frameOffsets` turns those back into a contiguous draw range.
export default function RawEditor() {
  const { t } = useT();
  const [projects, setProjects] = useState(null);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [busy, setBusy] = useState(null); // status text while a job runs
  const [work, setWork] = useState(null); // { work_id, work_dir, num_points }
  const [numPoints, setNumPoints] = useState(0);
  // Frame timeline: [frameNumber, pointCount] per frame, in scan order, plus the
  // running point offsets. Empty means we are on the older pc_after cloud.
  const [frames, setFrames] = useState(null);
  const [framesError, setFramesError] = useState(null);
  const frameOffsetsRef = useRef(null);
  // The bottom slider is a visibility window over scan time: only points whose
  // acquisition order falls in [visLo%, visHi%] are drawn (like scrubbing 3D
  // print layers). "Cut" removes exactly this window's frames.
  const [visLo, setVisLo] = useState(0);
  const [visHi, setVisHi] = useState(100);
  const [cuts, setCuts] = useState([]); // [{ lo, hi, removed_points }] — sections removed so far
  // When Creality Scan is installed we drive its own SDK: it does the cut AND
  // rebuilds the mesh, so the user gets a finished project instead of having to
  // re-fuse by hand. Without it we fall back to the manual cut.
  const [sdk, setSdk] = useState(false);
  // Re-fusing a full body scan takes minutes and can run out of memory, so it
  // is the user's call. Off still gives a correctly cut project — only its mesh
  // is stale, and Creality Scan fuses it in one click.
  const [refuse, setRefuse] = useState(true);
  const [meshBusy, setMeshBusy] = useState(false);

  const containerRef = useRef(null);
  const canvasRef = useRef(null);
  const sceneRef = useRef(null);
  const rendererRef = useRef(null);
  const cameraRef = useRef(null);
  const controlsRef = useRef(null);
  const pointsRef = useRef(null);
  const meshRef = useRef(null);
  const xformRef = useRef({ center: new THREE.Vector3(), scale: 1 }); // shared mesh+points transform
  const baseColorsRef = useRef(null); // time-gradient colors (Float32Array)
  const [showMesh, setShowMesh] = useState(false); // overlay is opt-in
  const showMeshRef = useRef(false);

  // ---- three.js scene bootstrap ----
  useEffect(() => {
    if (!containerRef.current || !canvasRef.current) return;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x060810);
    sceneRef.current = scene;
    const cam = new THREE.PerspectiveCamera(
      45, containerRef.current.clientWidth / containerRef.current.clientHeight, 0.01, 100);
    cam.position.set(0, 0, 3);
    cameraRef.current = cam;
    const renderer = new THREE.WebGLRenderer({ canvas: canvasRef.current, antialias: true });
    renderer.setSize(containerRef.current.clientWidth, containerRef.current.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    rendererRef.current = renderer;
    const controls = new OrbitControls(cam, renderer.domElement);
    controls.enableDamping = true;
    controlsRef.current = controls;
    const grid = new THREE.GridHelper(4, 16, 0x00f2fe, 0x1e293b);
    grid.position.y = -1;
    scene.add(grid);
    scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const dir1 = new THREE.DirectionalLight(0xffffff, 0.8); dir1.position.set(5, 8, 6); scene.add(dir1);
    const dir2 = new THREE.DirectionalLight(0xffffff, 0.4); dir2.position.set(-5, -3, -6); scene.add(dir2);

    let raf;
    const loop = () => { controls.update(); renderer.render(scene, cam); raf = requestAnimationFrame(loop); };
    loop();
    const onResize = () => {
      if (!containerRef.current) return;
      const w = containerRef.current.clientWidth, h = containerRef.current.clientHeight;
      cam.aspect = w / h; cam.updateProjectionMatrix(); renderer.setSize(w, h);
    };
    window.addEventListener('resize', onResize);
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', onResize); renderer.dispose(); controls.dispose(); };
  }, []);

  // ---- fetch project list on mount ----
  useEffect(() => {
    setLoadingProjects(true);
    fetch(`${BACKEND_URL}/api/raw/projects`)
      .then((r) => r.ok ? r.json() : Promise.reject(new Error('A projektek listája nem tölthető be.')))
      .then(setProjects)
      .catch(() => setProjects([]))
      .finally(() => setLoadingProjects(false));
    fetch(`${BACKEND_URL}/api/raw/sdk_available`)
      .then((r) => (r.ok ? r.json() : { available: false }))
      .then((d) => setSdk(!!d.available))
      .catch(() => setSdk(false));
  }, []);

  // ---- load the fused surface mesh (what makes the view recognisable) ----
  // `reuseTransform` matters when the surface is switched on after the cloud is
  // already drawn: deriving a fresh transform from the mesh would shift it off
  // the points. Both are in metres, so the cloud's transform fits the mesh too.
  const renderMesh = useCallback((url, reuseTransform = false) => new Promise((resolve) => {
    const scene = sceneRef.current;
    if (meshRef.current) { scene.remove(meshRef.current); meshRef.current = null; }
    if (!url) return resolve(false);
    new GLTFLoader().load(`${BACKEND_URL}${url}`, (gltf) => {
      const model = gltf.scene;
      let center, scale;
      if (reuseTransform) {
        ({ center, scale } = xformRef.current);
      } else {
        // the mesh spans the whole model, so derive the shared transform from it
        const box = new THREE.Box3().setFromObject(model);
        center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        scale = 2 / (Math.max(size.x, size.y, size.z) || 1);
        xformRef.current = { center, scale };
      }
      model.scale.setScalar(scale);
      model.position.set(-center.x * scale, -center.y * scale, -center.z * scale);
      model.traverse((ch) => {
        if (ch.isMesh) {
          ch.material = new THREE.MeshStandardMaterial({ color: 0x8a94a6, roughness: 0.75, metalness: 0.05, side: THREE.DoubleSide });
        }
      });
      model.visible = showMeshRef.current; // opt-in overlay, hidden by default
      scene.add(model);
      meshRef.current = model;
      resolve(true);
    }, undefined, () => resolve(false));
  }), []);

  // ---- load the time-ordered point cloud, aligned to the mesh transform ----
  const renderCloud = useCallback((buffer, haveMesh) => {
    const scene = sceneRef.current;
    if (pointsRef.current) {
      scene.remove(pointsRef.current);
      pointsRef.current.geometry.dispose();
      pointsRef.current.material.dispose();
    }
    // The frame cloud runs to tens of millions of points, so nothing here may
    // allocate a second copy of it: the fetched buffer IS the position array
    // (normalised in place), and the colours are bytes, not floats. Building
    // float positions + float colours + a float backup used to cost four times
    // the buffer — around a gigabyte at 20M points.
    const positions = new Float32Array(buffer);
    const n = positions.length / 3;
    let { center, scale } = xformRef.current;
    if (!haveMesh) {
      // no mesh: normalise from the points themselves
      let minX = Infinity, minY = Infinity, minZ = Infinity;
      let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
      for (let i = 0; i < positions.length; i += 3) {
        const x = positions[i], y = positions[i + 1], z = positions[i + 2];
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
        if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
      }
      center = new THREE.Vector3((minX + maxX) / 2, (minY + maxY) / 2, (minZ + maxZ) / 2);
      scale = 2 / (Math.max(maxX - minX, maxY - minY, maxZ - minZ) || 1);
      xformRef.current = { center, scale };
    }
    for (let i = 0; i < positions.length; i += 3) {
      positions[i] = (positions[i] - center.x) * scale;
      positions[i + 1] = (positions[i + 1] - center.y) * scale;
      positions[i + 2] = (positions[i + 2] - center.z) * scale;
    }

    // Time gradient, blue (scan start) -> warm (end). Computed in bands rather
    // than per point: with millions of points a per-point setHSL is seconds of
    // work, and a band is far narrower than anything the eye separates.
    const colors = new Uint8Array(n * 3);
    const c = new THREE.Color();
    const BANDS = 2048;
    for (let b = 0; b < BANDS; b++) {
      const from = Math.floor((b * n) / BANDS), to = Math.floor(((b + 1) * n) / BANDS);
      if (to <= from) continue;
      c.setHSL(0.6 - 0.5 * (b / (BANDS - 1)), 0.75, 0.55);
      const r = c.r * 255, g = c.g * 255, bl = c.b * 255;
      for (let i = from; i < to; i++) {
        colors[i * 3] = r; colors[i * 3 + 1] = g; colors[i * 3 + 2] = bl;
      }
    }
    baseColorsRef.current = colors.slice();
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3, true));
    const mat = new THREE.PointsMaterial({ size: haveMesh ? 0.008 : 0.014, vertexColors: true, sizeAttenuation: true });
    const pts = new THREE.Points(geo, mat);
    scene.add(pts);
    pointsRef.current = pts;
    setNumPoints(n);
  }, []);

  // ---- a slider window as frame positions ----
  // On the frame timeline a percentage is first snapped to whole frames, and
  // everything else (what is drawn, what is greyed out, what is cut) is derived
  // from those same frame positions — so the three can never disagree.
  const frameSpan = useCallback((lo, hi) => {
    const nf = frames ? frames.length : 0;
    if (!nf) return null;
    const a = Math.max(0, Math.min(nf, Math.floor(lo / 100 * nf)));
    const b = Math.max(a, Math.min(nf, Math.ceil(hi / 100 * nf)));
    return [a, b];
  }, [frames]);

  // the point range a slider window covers, in either mode
  const pointSpan = useCallback((lo, hi, total) => {
    const span = frameSpan(lo, hi);
    const offs = frameOffsetsRef.current;
    if (span && offs) return [offs[span[0]], offs[span[1]]];
    return [Math.floor(lo / 100 * total), Math.ceil(hi / 100 * total)];
  }, [frameSpan]);

  // ---- show only the scan-time window [visLo, visHi] (layer-scrub) ----
  useEffect(() => {
    const pts = pointsRef.current;
    if (!pts) return;
    const n = pts.geometry.getAttribute('position').count;
    const [a, b] = pointSpan(visLo, visHi, n);
    pts.geometry.setDrawRange(a, Math.max(0, b - a));
  }, [visLo, visHi, numPoints, pointSpan]);

  // ---- grey out the already-cut time sections ----
  useEffect(() => {
    const pts = pointsRef.current;
    const base = baseColorsRef.current;
    if (!pts || !base) return;
    const col = pts.geometry.getAttribute('color');
    col.array.set(base); // reset to the time gradient
    const n = base.length / 3;
    for (const c of cuts) {
      const [a, b] = pointSpan(c.lo, c.hi, n);
      for (let i = a; i < b; i++) {          // bytes, like the base colours
        col.array[i * 3] = 66; col.array[i * 3 + 1] = 66; col.array[i * 3 + 2] = 77;
      }
    }
    col.needsUpdate = true;
  }, [cuts, numPoints, pointSpan]);

  // ---- toggle the fused surface on/off, building it on first use ----
  useEffect(() => {
    showMeshRef.current = showMesh;
    if (meshRef.current) { meshRef.current.visible = showMesh; return; }
    if (!showMesh || !work?.mesh_available || meshBusy) return;
    // not built yet: this is the slow half of loading, so it happens here
    // rather than on every project open
    setMeshBusy(true);
    fetch(`${BACKEND_URL}/api/raw/mesh`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ obp: work.obp }),
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(t('errMeshFailed')))))
      .then((d) => renderMesh(d.mesh_url, true))
      .catch((e) => alert(e.message || String(e)))
      .finally(() => setMeshBusy(false));
  }, [showMesh, work, meshBusy, renderMesh, t]);

  // ---- the frame timeline: decode the scan into per-frame clouds ----
  // Slow enough on a full body scan (~30 s) that the backend runs it as a job;
  // once built it is cached, so re-opening a project is instant.
  const loadFrames = async (obp) => {
    const start = await postJson('/api/raw/frames', { obp });
    let res = start.ready ? start : null;
    while (!res) {
      await new Promise((r) => setTimeout(r, 700));
      const r = await fetch(`${BACKEND_URL}/api/raw/frames/${start.job_id}`);
      if (!r.ok) throw new Error(errText((await r.json().catch(() => ({}))).detail));
      const st = await r.json();
      if (st.state === 'error') throw new Error(st.error || t('errUnknown'));
      if (st.state === 'done') { res = st.result; break; }
      setBusy(`${t('busyFrames')} — ${Math.round((st.progress || 0) * 100)}%`);
    }
    // running point offsets, so a frame position maps to a draw range with no
    // searching: frame i covers [offs[i], offs[i+1])
    const offs = new Uint32Array(res.frames.length + 1);
    let acc = 0;
    res.frames.forEach((f, i) => { offs[i] = acc; acc += f[1]; });
    offs[res.frames.length] = acc;
    frameOffsetsRef.current = offs;
    setFrames(res.frames);
    return res;
  };

  const loadProject = async (obp) => {
    setBusy(t('busyLoad'));
    setCuts([]);
    setFrames(null); frameOffsetsRef.current = null; setFramesError(null);
    try {
      const data = await postJson('/api/raw/load', { obp });
      setVisLo(0); setVisHi(100); // start with the whole scan visible
      setShowMesh(false);
      const haveMesh = await renderMesh(data.mesh_url);

      // Prefer the frame timeline; the registered cloud stays as the fallback,
      // because a coarse timeline is still far better than none.
      let view = data;
      if (data.frames_available) {
        try {
          view = { ...data, ...(await loadFrames(obp)) };
        } catch (e) {
          setFramesError(e.message || String(e));
        }
      }
      // /load does not build the pc_after cloud when the frames are expected to
      // carry the timeline, so ask for it only if we ended up needing it
      if (!view.cloud_url) {
        setBusy(t('busyLoad'));
        view = { ...view, ...(await postJson('/api/raw/cloud', { obp })) };
      }
      setWork(view);
      const buf = await (await fetch(`${BACKEND_URL}${view.cloud_url}`)).arrayBuffer();
      renderCloud(buf, haveMesh);
    } catch (e) { alert(e.message || String(e)); } finally { setBusy(null); }
  };

  const doCut = async () => {
    if (!work) return;
    if (visHi - visLo >= 99.9) {
      alert(t('errWholeScan'));
      return;
    }
    setBusy(t('busyCut'));
    const lo = visLo, hi = visHi;
    // send every cut section: the copy is rebuilt from the original minus all of
    // them, so repeated cuts stay exact (no drift from earlier removals)
    const sections = [...cuts.map((c) => [c.lo, c.hi]), [lo, hi]];
    // On the frame timeline, name the frames as well. The backend then deletes
    // exactly these instead of re-deriving them from the percentages, so the
    // selection cannot shift between the view and the cut.
    const doomed = [];
    for (const [a, b] of sections) {
      const span = frameSpan(a, b);
      if (span) for (let i = span[0]; i < span[1]; i++) doomed.push(frames[i][0]);
    }
    try {
      // follow the cloud actually on screen, not a global capability flag:
      // cutting in a different index space than the view is what made earlier
      // cuts remove a different section than the one selected
      const useSdk = work.cloud_source === 'sdk';
      const data = useSdk
        ? await runSdkCut(sections)
        : await postJson('/api/raw/cut',
                         { obp: work.obp, ranges: sections, frames: doomed });
      // remember this section (for the grey overlay + the list); keep editing
      const span = frameSpan(lo, hi);
      setCuts((prev) => [...prev, {
        lo, hi, sdk: useSdk, ...data,
        section_frames: span ? span[1] - span[0] : null,
      }]);
      setVisLo(0); setVisHi(100); // reset the window so the whole scan is visible again
    } catch (e) { alert(e.message || String(e)); } finally { setBusy(null); }
  };

  const postJson = async (path, body) => {
    const r = await fetch(`${BACKEND_URL}${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(errText((await r.json().catch(() => ({}))).detail));
    return r.json();
  };

  // The SDK re-fuses the mesh, which takes minutes on a full body scan, so the
  // backend runs it as a job and we poll it for progress.
  const runSdkCut = async (ranges) => {
    const { job_id: jobId } = await postJson('/api/raw/cut_sdk',
      { obp: work.obp, ranges, refuse });
    for (;;) {
      await new Promise((res) => setTimeout(res, 1000));
      const r = await fetch(`${BACKEND_URL}/api/raw/cut_sdk/${jobId}`);
      if (!r.ok) throw new Error(errText((await r.json().catch(() => ({}))).detail));
      const st = await r.json();
      if (st.state === 'error') throw new Error(st.error || t('errUnknown'));
      if (st.state === 'done') return st.result;
      // the backend sends a stage key so both languages stay translatable here
      const key = st.stage ? `stage${st.stage[0].toUpperCase()}${st.stage.slice(1)}` : null;
      const label = (key && t(key) !== key) ? t(key) : t('busyCut');
      setBusy(`${label} — ${Math.round((st.progress || 0) * 100)}%`);
    }
  };

  const startOver = () => {
    setCuts([]);
    setVisLo(0); setVisHi(100);
  };

  return (
    <div className="raw-editor">
      <aside className="sidebar">
        <div className="sidebar-section">
          <h3>🎞️ {t('toolTitle')}</h3>
          <p style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            {t('intro')}
          </p>
        </div>

        {!work && (
          <div className="sidebar-section" style={{ flex: 1, overflowY: 'auto' }}>
            <h3>📂 {t('projects')}</h3>
            {loadingProjects && <p style={{ color: 'var(--accent-blue)' }}>{t('loading')}</p>}
            {projects && projects.length === 0 && (
              <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{t('noProjects')}</p>
            )}
            {projects && projects.map((p) => (
              <button key={p.obp} className="project-item" onClick={() => loadProject(p.obp)} disabled={!!busy}>
                {p.thumbnail && <img src={`${BACKEND_URL}/api/raw/thumb?path=${encodeURIComponent(p.thumbnail)}`} alt="" />}
                <span>{p.name}</span>
              </button>
            ))}
          </div>
        )}

        {work && (
          <div className="sidebar-section" style={{ flex: 1 }}>
            <h3>⚙️ {t('cut')}</h3>
            <div className="stats-grid">
              <div className="stat-item">
                <div className="stat-label">{frames ? t('frames') : t('points')}</div>
                <div className="stat-value">
                  {(frames ? frames.length : numPoints).toLocaleString()}
                </div>
              </div>
              <div className="stat-item"><div className="stat-label">{t('project')}</div><div className="stat-value" style={{ fontSize: '0.8rem' }}>{work.name}</div></div>
            </div>

            {frames && (
              <p style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', lineHeight: 1.5, marginTop: '0.6rem' }}>
                🎯 <strong>{t('frameTimeline')}</strong> — {t('frameTimelineDesc')}
              </p>
            )}
            {frames && work.blank_frames > 0 && (
              <p style={{ fontSize: '0.76rem', color: 'var(--text-secondary)', lineHeight: 1.5, marginTop: '0.4rem' }}>
                {t('frameBlank', { count: work.blank_frames.toLocaleString() })}
              </p>
            )}
            {framesError && (
              <p style={{ fontSize: '0.78rem', color: 'var(--accent-amber, #f59e0b)', lineHeight: 1.5, marginTop: '0.6rem' }}>
                ⚠️ {t('frameFallback', { reason: framesError })}
              </p>
            )}

            <label className="switch-control" style={{ cursor: 'pointer', marginTop: '0.75rem' }}>
              <div className="switch-label">
                <span className="switch-title">{t('modelSurface')}</span>
                <span className="switch-desc">
                  {meshBusy ? t('meshLoading') : t('modelSurfaceDesc')}
                </span>
              </div>
              <label className="switch">
                <input type="checkbox" checked={showMesh}
                  onChange={(e) => setShowMesh(e.target.checked)}
                  disabled={meshBusy || work.mesh_available === false} />
                <span className="slider-toggle"></span>
              </label>
            </label>

            {work.cloud_source === 'sdk' && (
              <label className="switch-control" style={{ cursor: 'pointer', marginTop: '0.75rem' }}>
                <div className="switch-label">
                  <span className="switch-title">⚡ {t('refuseLabel')}</span>
                  <span className="switch-desc">{t('refuseDesc')}</span>
                </div>
                <label className="switch">
                  <input type="checkbox" checked={refuse}
                    onChange={(e) => setRefuse(e.target.checked)} disabled={!!busy} />
                  <span className="slider-toggle"></span>
                </label>
              </label>
            )}

            <button className="btn-primary" onClick={doCut} disabled={!!busy} style={{ marginTop: '1rem' }}>
              {busy ? t('inProgress') : t('cutButton')}
            </button>
            <button className="btn-secondary" onClick={() => { setWork(null); setCuts([]); setVisLo(0); setVisHi(100); setFrames(null); frameOffsetsRef.current = null; setFramesError(null); }} disabled={!!busy} style={{ marginTop: '0.5rem' }}>
              {t('anotherProject')}
            </button>

            {cuts.length > 0 && (
              <div className="sidebar-section" style={{ background: 'rgba(16,185,129,0.05)', marginTop: '1rem' }}>
                <h3>{t('cutsHeader')}</h3>
                <div className="cut-list">
                  {cuts.map((c, i) => (
                    <div key={i} className="cut-row">
                      {c.section_frames != null
                        ? t('cutRowFrames', { from: c.lo.toFixed(2), to: c.hi.toFixed(2), frames: c.section_frames.toLocaleString() })
                        : t('cutRow', { from: c.lo.toFixed(2), to: c.hi.toFixed(2), points: (c.removed_points ?? 0).toLocaleString() })}
                    </div>
                  ))}
                </div>
                <p style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', margin: '0.5rem 0' }}>
                  {cuts[cuts.length - 1].cut_by === 'frames'
                    ? t('keepInfoFrames', {
                        remaining: (cuts[cuts.length - 1].remaining_frames ?? 0).toLocaleString(),
                        total: (cuts[cuts.length - 1].total_frames ?? 0).toLocaleString(),
                      })
                    : t('keepInfo', {
                        remaining: (cuts[cuts.length - 1].remaining_points ?? 0).toLocaleString(),
                        total: (cuts[cuts.length - 1].total_points ?? 0).toLocaleString(),
                      })}
                </p>
                {/* the cut can succeed while the rebuild does not (running out
                    of memory on a big scan); say so and fall back to the
                    "fuse it yourself" instruction rather than claiming success */}
                {cuts[cuts.length - 1].fuse_error && (
                  <p style={{ fontSize: '0.8rem', color: 'var(--accent-amber, #f59e0b)', lineHeight: 1.5 }}>
                    ⚠️ {t('cutNotFused', { reason: cuts[cuts.length - 1].fuse_error })}
                  </p>
                )}
                <p style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                  {t(cuts[cuts.length - 1].fused ? 'crealityHintSdk' : 'crealityHint',
                     { name: cuts[cuts.length - 1].work_id })}
                </p>
                <button className="btn-secondary" onClick={startOver} disabled={!!busy} style={{ marginTop: '0.5rem' }}>
                  {t('startOver')}
                </button>
              </div>
            )}
          </div>
        )}
      </aside>

      <main className="viewport-container">
        <div className="viewport-area" ref={containerRef}>
          <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />
          {!work && !busy && (
            <div className="loading-overlay" style={{ background: 'rgba(6,8,16,0.85)' }}>
              <h2 style={{ fontFamily: 'Space Grotesk' }}>{t('placeholderTitle')}</h2>
              <p style={{ color: 'var(--text-secondary)', maxWidth: 420, textAlign: 'center' }}>
                {t('placeholderDesc')}
              </p>
            </div>
          )}
          {busy && (
            <div className="loading-overlay" style={{ background: 'rgba(6,8,16,0.9)' }}>
              <div className="spinner" />
              <h3 className="loading-text">{busy}</h3>
            </div>
          )}

          {work && !busy && (
            <div className="timeline-bar">
              <div className="timeline-head">
                <span>⏱️ {t('timelineHead')}</span>
                <div className="timeline-inputs">
                  <label>{t('intervalStart')}
                    <input type="number" min="0" max="100" step="0.01" value={visLo.toFixed(2)}
                      onChange={(e) => setVisLo(Math.max(0, Math.min(+e.target.value, visHi - 0.01)))} />%
                  </label>
                  <label>{t('intervalEnd')}
                    <input type="number" min="0" max="100" step="0.01" value={visHi.toFixed(2)}
                      onChange={(e) => setVisHi(Math.min(100, Math.max(+e.target.value, visLo + 0.01)))} />%
                  </label>
                </div>
              </div>
              <div className="timeline-track">
                <input type="range" min="0" max="100" step="0.01" value={visLo}
                  onChange={(e) => setVisLo(Math.min(+e.target.value, visHi - 0.01))} />
                <input type="range" min="0" max="100" step="0.01" value={visHi}
                  onChange={(e) => setVisHi(Math.max(+e.target.value, visLo + 0.01))} />
              </div>
              <div className="timeline-labels">
                <span>{t('scanStart')}</span>
                {/* on the frame timeline the honest unit is the frame: that is
                    what the window snaps to and what the cut deletes */}
                <span>{frames
                  ? t('framesShown', {
                      shown: (() => { const s = frameSpan(visLo, visHi); return (s ? s[1] - s[0] : 0).toLocaleString(); })(),
                      total: frames.length.toLocaleString(),
                    })
                  : t('pointsShown', {
                      shown: Math.round((visHi - visLo) / 100 * numPoints).toLocaleString(),
                      total: numPoints.toLocaleString(),
                    })}</span>
                <span>{t('scanEnd')}</span>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
