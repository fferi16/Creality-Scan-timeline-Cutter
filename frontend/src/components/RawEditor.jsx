import React, { useState, useEffect, useRef, useCallback } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader';

const BACKEND_URL = import.meta.env.DEV ? 'http://127.0.0.1:8000' : '';

// FastAPI returns `detail` as a string (HTTPException) or an array of objects
// (422 validation) — turn either into readable text instead of "[object Object]".
function errText(detail) {
  if (!detail) return 'Ismeretlen hiba';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
  return JSON.stringify(detail);
}

// The raw cloud is stored in acquisition (time) order, so a point's position
// in the array IS its position in scan time. That's what makes the timeline work.
export default function RawEditor() {
  const [projects, setProjects] = useState(null);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [busy, setBusy] = useState(null); // status text while a job runs
  const [work, setWork] = useState(null); // { work_id, work_dir, num_points }
  const [numPoints, setNumPoints] = useState(0);
  // The bottom slider is a visibility window over scan time: only points whose
  // acquisition order falls in [visLo%, visHi%] are drawn (like scrubbing 3D
  // print layers). "Cut" removes exactly this window's frames.
  const [visLo, setVisLo] = useState(0);
  const [visHi, setVisHi] = useState(100);
  const [lastCut, setLastCut] = useState(null);

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
  }, []);

  // ---- load the fused surface mesh (what makes the view recognisable) ----
  const renderMesh = useCallback((url) => new Promise((resolve) => {
    const scene = sceneRef.current;
    if (meshRef.current) { scene.remove(meshRef.current); meshRef.current = null; }
    if (!url) return resolve(false);
    new GLTFLoader().load(`${BACKEND_URL}${url}`, (gltf) => {
      const model = gltf.scene;
      // the mesh spans the whole model, so derive the shared transform from it
      const box = new THREE.Box3().setFromObject(model);
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      const scale = 2 / (Math.max(size.x, size.y, size.z) || 1);
      xformRef.current = { center, scale };
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
    const xyz = new Float32Array(buffer);
    const n = xyz.length / 3;
    let { center, scale } = xformRef.current;
    if (!haveMesh) {
      // no mesh: normalise from the points themselves
      const box = new THREE.Box3();
      const v = new THREE.Vector3();
      for (let i = 0; i < n; i++) { v.set(xyz[i * 3], xyz[i * 3 + 1], xyz[i * 3 + 2]); box.expandByPoint(v); }
      center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      scale = 2 / (Math.max(size.x, size.y, size.z) || 1);
      xformRef.current = { center, scale };
    }

    const positions = new Float32Array(n * 3);
    const colors = new Float32Array(n * 3);
    const c = new THREE.Color();
    for (let i = 0; i < n; i++) {
      positions[i * 3] = (xyz[i * 3] - center.x) * scale;
      positions[i * 3 + 1] = (xyz[i * 3 + 1] - center.y) * scale;
      positions[i * 3 + 2] = (xyz[i * 3 + 2] - center.z) * scale;
      c.setHSL(0.6 - 0.5 * (i / n), 0.75, 0.55); // blue (scan start) -> warm (end)
      colors[i * 3] = c.r; colors[i * 3 + 1] = c.g; colors[i * 3 + 2] = c.b;
    }
    baseColorsRef.current = colors.slice();
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    const mat = new THREE.PointsMaterial({ size: haveMesh ? 0.008 : 0.014, vertexColors: true, sizeAttenuation: true });
    const pts = new THREE.Points(geo, mat);
    scene.add(pts);
    pointsRef.current = pts;
    setNumPoints(n);
  }, []);

  // ---- show only the scan-time window [visLo, visHi] (layer-scrub) ----
  useEffect(() => {
    const pts = pointsRef.current;
    if (!pts) return;
    const n = pts.geometry.getAttribute('position').count;
    const a = Math.floor(visLo / 100 * n);
    const b = Math.ceil(visHi / 100 * n);
    pts.geometry.setDrawRange(a, Math.max(0, b - a));
  }, [visLo, visHi, numPoints]);

  // ---- toggle the fused surface on/off ----
  useEffect(() => {
    showMeshRef.current = showMesh;
    if (meshRef.current) meshRef.current.visible = showMesh;
  }, [showMesh]);

  const loadProject = async (obp) => {
    setBusy('Projekt másolása és betöltése… (nagy projektnél ez egy percig is tarthat)');
    setLastCut(null);
    try {
      const r = await fetch(`${BACKEND_URL}/api/raw/load`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ obp }),
      });
      if (!r.ok) throw new Error(errText((await r.json().catch(() => ({}))).detail));
      const data = await r.json();
      setWork(data);
      setVisLo(0); setVisHi(100); // start with the whole scan visible
      const haveMesh = await renderMesh(data.mesh_url);
      const buf = await (await fetch(`${BACKEND_URL}${data.cloud_url}`)).arrayBuffer();
      renderCloud(buf, haveMesh);
    } catch (e) { alert(e.message || String(e)); } finally { setBusy(null); }
  };

  const doCut = async () => {
    if (!work) return;
    if (visHi - visLo >= 99.9) {
      alert('Szűkítsd a csúszkát a kivágandó szakaszra — most az egész szken ki lenne vágva.');
      return;
    }
    setBusy('Képkockák törlése és mentés a Creality projektbe…');
    try {
      const r = await fetch(`${BACKEND_URL}/api/raw/cut`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ obp: work.obp, start_pct: visLo, end_pct: visHi }),
      });
      if (!r.ok) throw new Error(errText((await r.json().catch(() => ({}))).detail));
      setLastCut(await r.json());
    } catch (e) { alert(e.message || String(e)); } finally { setBusy(null); }
  };

  return (
    <div className="raw-editor">
      <aside className="sidebar">
        <div className="sidebar-section">
          <h3>🎞️ Nyers Szken Vágó</h3>
          <p style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            Válaszd ki a szkennelt projektet. A szken pontjai <b>valódi szkennelési sorrendben</b> jelennek meg —
            az alsó csúszkával végignézed az időt, kijelölöd a bemozdult szakaszt, és kivágod. A program azt a
            szakaszt törli a nyers képkockákból, a <b>fúziót utána a Creality Scanben</b> végzed el.
          </p>
        </div>

        {!work && (
          <div className="sidebar-section" style={{ flex: 1, overflowY: 'auto' }}>
            <h3>📂 Projektek</h3>
            {loadingProjects && <p style={{ color: 'var(--accent-blue)' }}>Betöltés…</p>}
            {projects && projects.length === 0 && (
              <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                Nem található CrealityScan projekt ezen a gépen.
              </p>
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
            <h3>⚙️ Vágás</h3>
            <div className="stats-grid">
              <div className="stat-item"><div className="stat-label">Pontok</div><div className="stat-value">{numPoints.toLocaleString('hu-HU')}</div></div>
              <div className="stat-item"><div className="stat-label">Projekt</div><div className="stat-value" style={{ fontSize: '0.8rem' }}>{work.name}</div></div>
            </div>

            <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              Az alsó csúszkával végignézed a szkennelést időben — csak a beállított szakasz látszik.
              Állítsd a csúszkát a <b>bemozdult szakaszra</b>, és nyomd meg a kivágást: a program azt a
              szakaszt törli a képkockákból, a fúziót a <b>Creality Scanben</b> végzed el rajta.
            </p>

            <label className="switch-control" style={{ cursor: 'pointer' }}>
              <div className="switch-label">
                <span className="switch-title">Modell felület</span>
                <span className="switch-desc">A fúzionált felület mutatása a pontok alatt</span>
              </div>
              <label className="switch">
                <input type="checkbox" checked={showMesh} onChange={(e) => setShowMesh(e.target.checked)} />
                <span className="slider-toggle"></span>
              </label>
            </label>

            <button className="btn-primary" onClick={doCut} disabled={!!busy} style={{ marginTop: '1rem' }}>
              {busy ? 'Folyamatban…' : '✂️ Beállított szakasz kivágása'}
            </button>
            <button className="btn-secondary" onClick={() => { setWork(null); setLastCut(null); setVisLo(0); setVisHi(100); }} disabled={!!busy} style={{ marginTop: '0.5rem' }}>
              ← Másik projekt
            </button>

            {lastCut && (
              <div className="sidebar-section" style={{ background: 'rgba(16,185,129,0.05)', marginTop: '1rem' }}>
                <h3>✅ Kivágva</h3>
                <p style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                  {(lastCut.deleted_frames || 0).toLocaleString('hu-HU')} képkocka törölve
                  (a szken {visLo.toFixed(0)}–{visHi.toFixed(0)}%-a). Nyisd meg a
                  <b> {lastCut.work_id}</b> projektet a Creality Scanben, és futtasd le a fúziót —
                  a bemozdult szakasz nélkül fog összeállni.
                </p>
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
              <h2 style={{ fontFamily: 'Space Grotesk' }}>Válassz egy szkennelt projektet</h2>
              <p style={{ color: 'var(--text-secondary)', maxWidth: 420, textAlign: 'center' }}>
                A pontok a szkennelés idejében színeződnek (kék → meleg). A bemozdulás jellemzően időben elkülönülő,
                kettőződött rétegként tűnik fel.
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
                <span>⏱️ Szkennelési idő — csak a látható szakasz jelenik meg</span>
                <span className="timeline-window">{visLo.toFixed(0)}% – {visHi.toFixed(0)}%</span>
              </div>
              <div className="timeline-track">
                <input type="range" min="0" max="100" step="0.5" value={visLo}
                  onChange={(e) => setVisLo(Math.min(+e.target.value, visHi - 0.5))} />
                <input type="range" min="0" max="100" step="0.5" value={visHi}
                  onChange={(e) => setVisHi(Math.max(+e.target.value, visLo + 0.5))} />
              </div>
              <div className="timeline-labels">
                <span>Szken eleje</span>
                <span>{Math.round((visHi - visLo) / 100 * numPoints).toLocaleString('hu-HU')} / {numPoints.toLocaleString('hu-HU')} pont látszik</span>
                <span>Szken vége</span>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
