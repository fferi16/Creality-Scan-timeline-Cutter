# Creality Scan Timeline Cutter

*[English](#english) · [Magyar](#magyar)*

---

## English

A tool for **Creality Otter** (and similar) 3D scans of **people**. When the
subject moves during scanning, the surface **doubles up** ("double shell"). This
tool lets you scrub through the scan in **real acquisition order**, isolate the
moment the subject moved, and **cut that section** — then you re-fuse the cleaned
project in Creality Scan.

### How it works
- The raw scan points are read straight from the project's plain-text
  `pc_after.ply`, which the scanner writes in **capture (time) order** — so a
  point's position in the file is its position in scan time. **No proprietary
  Creality SDK or DLL is used or needed.**
- A bottom slider (Bambu-Lab layer-preview style) controls which slice of scan
  time is visible; you narrow it to the moved section and cut it.
- Cutting removes those raw frames from a **copy** of the project (the original
  is never touched) and registers the copy so it appears in Creality Scan. You
  open it there and run the fusion — it rebuilds without the moved section.

### Project layout
- `backend/` — FastAPI server (Python). Point-cloud read + surface-mesh preview
  via [PyMeshLab](https://pymeshlab.readthedocs.io/) + [trimesh](https://trimesh.org/).
- `frontend/` — React + Vite + [three.js](https://threejs.org/), with an
  English/Hungarian language switch.
- `launcher.py` — the desktop app: opens the tool in its own native window
  (Edge WebView2, no browser), starts the backend invisibly on a free port,
  and shuts it down when the window closes (this is what the standalone
  `.exe` is built from with PyInstaller).

### Setup & run
```bash
# backend (Python 3.12 recommended)
python -m venv .venv
.venv\Scripts\activate                 # Windows
pip install -r backend/requirements.txt

# frontend (Node.js)
cd frontend
npm install
npm run build                          # the backend serves frontend/dist

# start
cd ../backend
python main.py                         # http://127.0.0.1:8000
```
For development, run the frontend separately with `npm run dev` (in `frontend`).

### Notes
- Reads Creality Scan's local projects from
  `%LOCALAPPDATA%\Creality\CrealityScan\Projects`, so Creality Scan projects
  must exist on the machine (Creality Scan itself is needed to scan and to fuse).
- **Not affiliated with Creality.** This is an independent interoperability tool
  for fixing your own scans. Creality's own files/DLLs are **not** included.

---

## Magyar

Eszköz **Creality Otter** (és hasonló) 3D szkennerrel készült **emberi szkenek**
javítására. Ha az alany szkennelés közben bemozdul, a felület **kettőződik**
("dupla héj"). Ezzel az eszközzel a szken **valódi keletkezési sorrendjében**
lépegetsz végig, kijelölöd a bemozdulás pillanatát, és **kivágod azt a
szakaszt** — utána a megtisztított projektet a Creality Scanben fuzionálod újra.

### Hogyan működik
- A nyers pontok közvetlenül a projekt sima szöveges `pc_after.ply` fájljából
  jönnek, amit a szkenner **keletkezési (idő) sorrendben** ír — így egy pont
  helye a fájlban a szkennelési időben elfoglalt helye. **Semmilyen jogvédett
  Creality SDK vagy DLL nem kell és nem is használ.**
- Egy alsó csúszka (Bambu Lab réteg-nézegető stílus) szabályozza, hogy a
  szkennelési idő melyik szelete látszik; erre szűkíted a bemozdult szakaszt, és
  kivágod.
- A vágás a nyers képkockákat a projekt egy **másolatából** törli (az eredetihez
  sosem nyúl), és bejegyzi a másolatot, hogy megjelenjen a Creality Scanben. Ott
  megnyitod és lefuttatod a fúziót — a bemozdult szakasz nélkül áll össze.

### Felépítés
- `backend/` — FastAPI szerver (Python). Pontfelhő-olvasás + felület-előnézet
  [PyMeshLab](https://pymeshlab.readthedocs.io/) + [trimesh](https://trimesh.org/).
- `frontend/` — React + Vite + [three.js](https://threejs.org/), magyar/angol
  nyelvváltóval.
- `launcher.py` — az asztali alkalmazás: saját natív ablakban nyitja meg az
  eszközt (Edge WebView2, böngésző nélkül), a szervert láthatatlanul, szabad
  porton indítja, és az ablak bezárásakor le is állítja (ebből készül a
  `.exe` PyInstallerrel).

### Telepítés és futtatás
```bash
# backend (Python 3.12 ajánlott)
python -m venv .venv
.venv\Scripts\activate                 # Windows
pip install -r backend/requirements.txt

# frontend (Node.js)
cd frontend
npm install
npm run build                          # a backend a frontend/dist-et szolgálja ki

# indítás
cd ../backend
python main.py                         # http://127.0.0.1:8000
```
Fejlesztéshez a frontend külön: `npm run dev` (a `frontend` mappában).

### Megjegyzések
- A Creality Scan lokális projektjeit olvassa a
  `%LOCALAPPDATA%\Creality\CrealityScan\Projects` mappából, tehát Creality Scan
  projektek kellenek a gépen (szkennelni és fuzionálni a Creality Scan kell).
- **Nem áll kapcsolatban a Creality-vel.** Ez egy független interoperabilitási
  eszköz a saját szkenjeid javítására. Creality saját fájljai/DLL-jei **nincsenek**
  benne.
