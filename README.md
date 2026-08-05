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
  point's position in the file is its position in scan time. This is what makes
  a real timeline possible, and it needs no SDK.
- A bottom slider (Bambu-Lab layer-preview style) controls which slice of scan
  time is visible; you narrow it to the moved section and cut it.
- The cut always works on a **copy** of the project — the original is never
  touched — and the copy is registered so it shows up in Creality Scan.

There are two ways the cut is applied, picked automatically:

- **With Creality Scan installed** (the normal case) the tool drives Creality's
  own scan engine (`lib_orbbec_scan.dll`, loaded from your installation): it
  deletes the selected points and **rebuilds the mesh itself**, on the GPU, with
  the settings the project was already fused with. The edited project comes back
  finished — nothing left to do by hand. Re-fusing a full body scan takes a few
  minutes, so the UI shows progress.
- **Without it**, the tool falls back to editing the project's own files and you
  run the fusion in Creality Scan yourself.

The SDK is only ever loaded from your own installation, in a separate process,
and no Creality file is redistributed with this tool.

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
  must exist on the machine. Creality Scan is needed to scan; with it installed
  the tool also re-fuses the cut project for you, otherwise you fuse it there.
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
  helye a fájlban a szkennelési időben elfoglalt helye. Ez teszi lehetővé a
  valódi idővonalat, és ehhez nem kell SDK.
- Egy alsó csúszka (Bambu Lab réteg-nézegető stílus) szabályozza, hogy a
  szkennelési idő melyik szelete látszik; erre szűkíted a bemozdult szakaszt, és
  kivágod.
- A vágás mindig a projekt egy **másolatán** dolgozik — az eredetihez sosem nyúl
  —, és a másolatot bejegyzi, hogy megjelenjen a Creality Scanben.

A vágás kétféleképpen történhet, a program automatikusan választ:

- **Ha a Creality Scan telepítve van** (a szokásos eset), a program a Creality
  saját szken-motorját vezérli (`lib_orbbec_scan.dll`, a te telepítésedből
  betöltve): törli a kijelölt pontokat, és **maga építi újra a mesh-t**, GPU-n,
  azzal a beállítással, amivel a projekt eredetileg is fuzionált. A kész projekt
  nem igényel további teendőt. Egy teljes alakos szken újrafúziója néhány perc,
  ezért a felület haladást mutat.
- **Ha nincs telepítve**, a program a projekt fájljait szerkeszti, és a fúziót
  neked kell lefuttatnod a Creality Scanben.

Az SDK-t kizárólag a saját telepítésedből tölti be, külön folyamatban, és
semmilyen Creality-fájlt nem terjeszt együtt az eszközzel.

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
  projektek kellenek a gépen. Szkennelni a Creality Scan kell; ha telepítve van,
  a vágott projektet a program újra is fuzionálja, különben ott fuzionálod.
- **Nem áll kapcsolatban a Creality-vel.** Ez egy független interoperabilitási
  eszköz a saját szkenjeid javítására. Creality saját fájljai/DLL-jei **nincsenek**
  benne.
