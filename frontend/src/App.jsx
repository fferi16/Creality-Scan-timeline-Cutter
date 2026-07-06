import React, { useState, useRef } from 'react';
import './App.css';
import ThreeViewer from './components/ThreeViewer';
import RawEditor from './components/RawEditor';

// In dev mode (npm run dev) talk to the separately running backend;
// in the built app the backend serves the UI itself, so relative URLs work.
const BACKEND_URL = import.meta.env.DEV ? 'http://127.0.0.1:8000' : '';

export default function App() {
  // Top-level view: 'mesh' = file-based mesh cleaner, 'raw' = raw project cutter
  const [appMode, setAppMode] = useState('mesh');

  // File upload state
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [fileId, setFileId] = useState(null);
  const [originalStats, setOriginalStats] = useState(null);
  const [originalGlbUrl, setOriginalGlbUrl] = useState(null);
  const [dragActive, setDragActive] = useState(false);

  // Processing settings state
  const [removeNoise, setRemoveNoise] = useState(true);
  const [noiseMinFaces, setNoiseMinFaces] = useState(50);
  const [fixShells, setFixShells] = useState(true);
  const [shellDetail, setShellDetail] = useState(9); // ~4mm cells: merges the ~5mm ghost layers motion leaves on body scans
  const [shellTrim, setShellTrim] = useState(2.0);
  const [closeHoles, setCloseHoles] = useState(true);
  const [maxHoleSize, setMaxHoleSize] = useState(150);
  const [smoothType, setSmoothType] = useState('taubin'); // 'none', 'laplacian', 'taubin'
  const [smoothSteps, setSmoothSteps] = useState(8);
  const [decimate, setDecimate] = useState(false);
  const [decimatePerc, setDecimatePerc] = useState(0.5);

  // Processed results state
  const [processing, setProcessing] = useState(false);
  const [processedGlbUrl, setProcessedGlbUrl] = useState(null);
  const [processedStats, setProcessedStats] = useState(null);

  // Visualization settings
  const [visMode, setVisMode] = useState('original'); // 'original', 'processed', 'split'
  const [wireframe, setWireframe] = useState(false);
  const [pointsMode, setPointsMode] = useState(false);
  const [autoRotate, setAutoRotate] = useState(false);

  const fileInputRef = useRef(null);

  // Drag-and-drop events
  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      await uploadFile(e.dataTransfer.files[0]);
    }
  };

  const handleFileChange = async (e) => {
    if (e.target.files && e.target.files[0]) {
      await uploadFile(e.target.files[0]);
    }
  };

  // Upload file to backend API
  const uploadFile = async (selectedFile) => {
    const ext = selectedFile.name.split('.').pop().toLowerCase();
    if (!['obj', 'ply', 'stl'].includes(ext)) {
      alert("Nem támogatott fájlformátum! Csak OBJ, PLY és STL fájlokat tudunk feldolgozni.");
      return;
    }

    setFile(selectedFile);
    setUploading(true);
    setFileId(null);
    setOriginalStats(null);
    setOriginalGlbUrl(null);
    setProcessedGlbUrl(null);
    setProcessedStats(null);
    setVisMode('original');

    const formData = new FormData();
    formData.append("file", selectedFile);

    try {
      const response = await fetch(`${BACKEND_URL}/api/upload`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Feltöltési hiba");
      }

      const data = await response.json();
      setFileId(data.file_id);
      setOriginalStats(data.stats);
      setOriginalGlbUrl(data.original_glb_url);
    } catch (err) {
      console.error(err);
      alert(`Sikertelen feltöltés: ${err.message}`);
      setFile(null);
    } finally {
      setUploading(false);
    }
  };

  const triggerFileSelect = () => {
    fileInputRef.current.click();
  };

  const removeFile = () => {
    setFile(null);
    setFileId(null);
    setOriginalStats(null);
    setOriginalGlbUrl(null);
    setProcessedGlbUrl(null);
    setProcessedStats(null);
    setVisMode('original');
  };

  // Run cleanup processing on backend API
  const handleProcess = async () => {
    if (!fileId) return;

    setProcessing(true);
    try {
      const response = await fetch(`${BACKEND_URL}/api/process`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          file_id: fileId,
          remove_noise: removeNoise,
          noise_min_faces: parseInt(noiseMinFaces),
          fix_shells: fixShells,
          shell_detail: parseInt(shellDetail),
          shell_trim: parseFloat(shellTrim),
          close_holes: closeHoles,
          max_hole_size: parseInt(maxHoleSize),
          smooth_type: smoothType,
          smooth_steps: parseInt(smoothSteps),
          decimate: decimate,
          decimate_perc: parseFloat(decimatePerc),
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Feldolgozási hiba");
      }

      const data = await response.json();
      setProcessedGlbUrl(data.processed_glb_url);
      setProcessedStats(data.stats.after);
      setOriginalStats(data.stats.before); // Update in case before filters altered it
      setVisMode('processed'); // Switch view tab to show processed version
    } catch (err) {
      console.error(err);
      alert(`Hiba történt a javítás során: ${err.message}`);
    } finally {
      setProcessing(false);
    }
  };

  // Trigger file download from backend API
  const handleDownload = () => {
    if (!fileId) return;
    window.open(`${BACKEND_URL}/api/download/${fileId}`, '_blank');
  };

  // Format big numbers
  const formatNum = (num) => {
    if (num === undefined || num === null) return "N/A";
    return num.toLocaleString('hu-HU');
  };

  // Calculate percentage reduction
  const getReductionPercent = (before, after) => {
    if (!before || !after) return 0;
    const diff = before - after;
    return ((diff / before) * 100).toFixed(1);
  };

  return (
    <>
      {/* App Header */}
      <header className="app-header">
        <div className="logo-container">
          <div className="logo-icon" />
          <h1 className="logo-text">3D Scan Doctor</h1>
        </div>
        <div className="mode-tabs">
          <button className={`mode-tab ${appMode === 'mesh' ? 'active' : ''}`} onClick={() => setAppMode('mesh')}>
            Mesh Javító
          </button>
          <button className={`mode-tab ${appMode === 'raw' ? 'active' : ''}`} onClick={() => setAppMode('raw')}>
            Nyers Szken Vágó
          </button>
        </div>
        <div className="status-badge">
          <div className="status-dot" />
          Helyi ágens elérhető
        </div>
      </header>

      {appMode === 'raw' ? (
        <div className="app-container">
          <RawEditor />
        </div>
      ) : (
      <div className="app-container">

        {/* Sidebar */}
        <aside className="sidebar">
          
          {/* File Upload Section */}
          <div className="sidebar-section">
            <h3>📂 Modell Feltöltése</h3>
            {!file ? (
              <div 
                className={`upload-zone ${dragActive ? 'drag-active' : ''}`}
                onDragEnter={handleDrag}
                onDragOver={handleDrag}
                onDragLeave={handleDrag}
                onDrop={handleDrop}
                onClick={triggerFileSelect}
              >
                <div className="upload-icon">↑</div>
                <div>
                  <p style={{ fontWeight: 500, marginBottom: '0.25rem' }}>Húzd ide a 3D fájlt</p>
                  <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>vagy kattints a tallózáshoz</p>
                </div>
                <p style={{ fontSize: '0.75rem', color: 'var(--accent-blue)' }}>OBJ, PLY, STL támogatott</p>
                <input 
                  type="file" 
                  ref={fileInputRef}
                  className="file-input"
                  onChange={handleFileChange}
                  accept=".obj,.ply,.stl"
                />
              </div>
            ) : (
              <div className="file-info-box">
                <span className="file-info-name" title={file.name}>📄 {file.name}</span>
                {uploading ? (
                  <span style={{ fontSize: '0.8rem', color: 'var(--accent-blue)' }}>Feltöltés...</span>
                ) : (
                  <button className="remove-file-btn" onClick={removeFile}>✕</button>
                )}
              </div>
            )}

            {originalStats && (
              <div className="stats-grid">
                <div className="stat-item">
                  <div className="stat-label">Pontok (Vertices)</div>
                  <div className="stat-value">{formatNum(originalStats.vertices)}</div>
                </div>
                <div className="stat-item">
                  <div className="stat-label">Lapok (Faces)</div>
                  <div className="stat-value">{formatNum(originalStats.faces)}</div>
                </div>
              </div>
            )}
          </div>

          {/* Cleaning Configurations */}
          <div className="sidebar-section" style={{ flex: 1 }}>
            <h3>⚙️ Javítási Beállítások</h3>
            
            {/* 1. Remove noise */}
            <div className="switch-control">
              <div className="switch-label">
                <span className="switch-title">Zajszűrés</span>
                <span className="switch-desc">Lebegő részecskék eltávolítása</span>
              </div>
              <label className="switch">
                <input 
                  type="checkbox" 
                  checked={removeNoise} 
                  onChange={(e) => setRemoveNoise(e.target.checked)} 
                />
                <span className="slider-toggle"></span>
              </label>
            </div>

            {removeNoise && (
              <div className="control-group">
                <label>
                  <span>Min. részecske méret:</span>
                  <span className="control-val">{noiseMinFaces} lap</span>
                </label>
                <input 
                  type="range" 
                  min="10" 
                  max="500" 
                  value={noiseMinFaces} 
                  onChange={(e) => setNoiseMinFaces(e.target.value)} 
                />
              </div>
            )}

            {/* 2. Double-shell repair */}
            <div className="switch-control">
              <div className="switch-label">
                <span className="switch-title">Dupla héj javítás</span>
                <span className="switch-desc">Bemozdult, kettőződött felületek egyesítése (újraépítés)</span>
              </div>
              <label className="switch">
                <input
                  type="checkbox"
                  checked={fixShells}
                  onChange={(e) => setFixShells(e.target.checked)}
                />
                <span className="slider-toggle"></span>
              </label>
            </div>

            {fixShells && (
              <>
                <div className="control-group">
                  <label>
                    <span>Részletesség:</span>
                    <span className="control-val">
                      {shellDetail <= 7 ? 'Erős összemosás' : shellDetail <= 9 ? 'Ajánlott — bemozdulás-javítás' : 'Finom részletek — a kettőződést megtartja!'} ({shellDetail})
                    </span>
                  </label>
                  <input
                    type="range"
                    min="6"
                    max="11"
                    value={shellDetail}
                    onChange={(e) => setShellDetail(e.target.value)}
                  />
                </div>
                <div className="control-group">
                  <label>
                    <span>Buborékok levágása:</span>
                    <span className="control-val">{shellTrim > 0 ? `${shellTrim}% távolságig` : 'Kikapcsolva'}</span>
                  </label>
                  <input
                    type="range"
                    min="0"
                    max="5"
                    step="0.5"
                    value={shellTrim}
                    onChange={(e) => setShellTrim(e.target.value)}
                  />
                </div>
              </>
            )}

            {/* 3. Close holes */}
            <div className="switch-control">
              <div className="switch-label">
                <span className="switch-title">Lyukak kitöltése</span>
                <span className="switch-desc">Mesh réseinek automatikus lezárása</span>
              </div>
              <label className="switch">
                <input 
                  type="checkbox" 
                  checked={closeHoles} 
                  onChange={(e) => setCloseHoles(e.target.checked)} 
                />
                <span className="slider-toggle"></span>
              </label>
            </div>

            {closeHoles && (
              <div className="control-group">
                <label>
                  <span>Max. lyuk kerület:</span>
                  <span className="control-val">{maxHoleSize} él</span>
                </label>
                <input 
                  type="range" 
                  min="10" 
                  max="500" 
                  value={maxHoleSize} 
                  onChange={(e) => setMaxHoleSize(e.target.value)} 
                />
              </div>
            )}

            {/* 3. Smoothing */}
            <div className="control-group">
              <label>Felületsimítás (Denoise)</label>
              <select 
                className="select-input" 
                value={smoothType} 
                onChange={(e) => setSmoothType(e.target.value)}
              >
                <option value="none">Nincs (Kikapcsolva)</option>
                <option value="taubin">Taubin simítás (Ajánlott - nem zsugorít)</option>
                <option value="laplacian">Laplacian simítás (Erős simítás)</option>
              </select>
            </div>

            {smoothType !== 'none' && (
              <div className="control-group">
                <label>
                  <span>Simítási lépések:</span>
                  <span className="control-val">{smoothSteps} iteráció</span>
                </label>
                <input 
                  type="range" 
                  min="1" 
                  max="30" 
                  value={smoothSteps} 
                  onChange={(e) => setSmoothSteps(e.target.value)} 
                />
              </div>
            )}

            {/* 4. Decimation */}
            <div className="switch-control" style={{ borderTop: '1px solid rgba(255,255,255,0.04)', paddingTop: '1rem', marginTop: '1rem' }}>
              <div className="switch-label">
                <span className="switch-title">Mesh egyszerűsítés</span>
                <span className="switch-desc">Poligonszám csökkentése</span>
              </div>
              <label className="switch">
                <input 
                  type="checkbox" 
                  checked={decimate} 
                  onChange={(e) => setDecimate(e.target.checked)} 
                />
                <span className="slider-toggle"></span>
              </label>
            </div>

            {decimate && (
              <div className="control-group">
                <label>
                  <span>Cél poligonszám megtartás:</span>
                  <span className="control-val">{Math.round(decimatePerc * 100)}%</span>
                </label>
                <input 
                  type="range" 
                  min="0.1" 
                  max="0.9" 
                  step="0.05"
                  value={decimatePerc} 
                  onChange={(e) => setDecimatePerc(e.target.value)} 
                />
              </div>
            )}

            <button 
              className="btn-primary" 
              onClick={handleProcess}
              disabled={!fileId || processing}
              style={{ marginTop: '1.5rem' }}
            >
              {processing ? 'Feldolgozás...' : 'Javítás Indítása'}
            </button>
          </div>

          {/* Download & Compare */}
          {processedStats && (
            <div className="sidebar-section" style={{ background: 'rgba(16, 185, 129, 0.03)' }}>
              <h3>📈 Összehasonlítás</h3>
              <div className="comparison-container">
                <div className="comp-row header">
                  <span>Metrika</span>
                  <span>Eredeti</span>
                  <span>Javított</span>
                </div>
                <div className="comp-row">
                  <span>Pontok</span>
                  <span>{formatNum(originalStats.vertices)}</span>
                  <span>{formatNum(processedStats.vertices)}</span>
                </div>
                <div className="comp-row">
                  <span>Lapok</span>
                  <span>{formatNum(originalStats.faces)}</span>
                  <span>{formatNum(processedStats.faces)}</span>
                </div>
                <div className="comp-row">
                  <span>Csökkenés</span>
                  <span />
                  <span className="comp-val-diff decrease">
                    -{getReductionPercent(originalStats.faces, processedStats.faces)}%
                  </span>
                </div>
              </div>

              <button 
                className="btn-secondary" 
                onClick={handleDownload}
                style={{ marginTop: '1rem', borderColor: 'var(--success-color)', color: 'var(--success-color)' }}
              >
                📥 Tisztított GLB Letöltése
              </button>
            </div>
          )}
        </aside>

        {/* Viewport Area */}
        <main className="viewport-container">
          
          {/* Top Bar Render options */}
          <div className="visualization-bar">
            {/* View Mode */}
            <div className="vis-mode-tabs">
              <button 
                className={`vis-tab ${visMode === 'original' ? 'active' : ''}`}
                onClick={() => setVisMode('original')}
                disabled={!originalGlbUrl}
              >
                Eredeti Scan
              </button>
              <button 
                className={`vis-tab ${visMode === 'processed' ? 'active' : ''}`}
                onClick={() => setVisMode('processed')}
                disabled={!processedGlbUrl}
              >
                Javított Scan
              </button>
              <button 
                className={`vis-tab ${visMode === 'split' ? 'active' : ''}`}
                onClick={() => setVisMode('split')}
                disabled={!originalGlbUrl || !processedGlbUrl}
              >
                Osztott Nézet (Side-by-Side)
              </button>
            </div>

            {/* Rendering Modes */}
            <div className="render-mode-controls">
              <button 
                className={`vis-toggle-btn ${wireframe ? 'active' : ''}`}
                onClick={() => setWireframe(!wireframe)}
              >
                Drótháló
              </button>
              <button 
                className={`vis-toggle-btn ${pointsMode ? 'active' : ''}`}
                onClick={() => setPointsMode(!pointsMode)}
              >
                Pontfelhő
              </button>
              <button 
                className={`vis-toggle-btn ${autoRotate ? 'active' : ''}`}
                onClick={() => setAutoRotate(!autoRotate)}
              >
                Forgatás
              </button>
            </div>
          </div>

          {/* 3D Viewport wrapper */}
          <div className="viewport-area">
            {visMode !== 'split' ? (
              <ThreeViewer 
                modelUrl={visMode === 'original' ? originalGlbUrl : processedGlbUrl}
                wireframe={wireframe}
                pointsMode={pointsMode}
                autoRotate={autoRotate}
              />
            ) : (
              <div className="dual-viewport-grid">
                <div className="split-pane">
                  <div className="pane-label">EREDETI MODELL</div>
                  <ThreeViewer 
                    modelUrl={originalGlbUrl}
                    wireframe={wireframe}
                    pointsMode={pointsMode}
                    autoRotate={autoRotate}
                  />
                </div>
                <div className="split-pane">
                  <div className="pane-label" style={{ color: 'var(--success-color)' }}>JAVÍTOTT MODELL</div>
                  <ThreeViewer 
                    modelUrl={processedGlbUrl}
                    wireframe={wireframe}
                    pointsMode={pointsMode}
                    autoRotate={autoRotate}
                  />
                </div>
              </div>
            )}

            {/* Overlay if no file uploaded */}
            {!fileId && !uploading && (
              <div className="loading-overlay" style={{ background: 'rgba(6, 8, 16, 0.9)' }}>
                <div className="logo-icon" style={{ width: '80px', height: '80px', borderRadius: '20px', marginBottom: '1rem' }} />
                <h2 style={{ fontFamily: 'Space Grotesk', fontSize: '1.8rem', fontWeight: 600 }}>Tölts fel egy 3D scannelt modellt</h2>
                <p style={{ color: 'var(--text-secondary)', maxWidth: '400px', textAlign: 'center', fontSize: '0.95rem' }}>
                  Húzz be egy OBJ, PLY vagy STL kiterjesztésű emberi scan fájlt a bal oldali sávba az interaktív megjelenítéshez és javításhoz.
                </p>
              </div>
            )}

            {uploading && (
              <div className="loading-overlay" style={{ background: 'rgba(6, 8, 16, 0.9)' }}>
                <div className="spinner" />
                <h3 className="loading-text">3D fájl feldolgozása és konvertálása...</h3>
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>Ez eltarthat pár másodpercig a modell méretétől függően</p>
              </div>
            )}

            {processing && (
              <div className="loading-overlay" style={{ background: 'rgba(6, 8, 16, 0.9)' }}>
                <div className="spinner" style={{ borderTopColor: 'var(--success-color)' }} />
                <h3 className="loading-text" style={{ color: 'var(--success-color)', textShadow: 'none' }}>Algoritmusok futtatása a modellen...</h3>
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>Zajszűrés, dupla héj újraépítés, lyukak kitöltése és simítás folyamatban — nagy modellnél ez percekig is eltarthat</p>
              </div>
            )}
          </div>
        </main>
      </div>
      )}
    </>
  );
}
