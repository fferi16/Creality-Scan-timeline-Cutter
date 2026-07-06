# 3D Scan Doctor

Eszköz **Creality Otter** (és hasonló) 3D szkennerrel készült **emberi szkenek** javítására.
A fő probléma, amit megold: ha az alany szkennelés közben bemozdul, a felület
**kettőződik ("dupla héj")**. Két külön megközelítést kínál rá.

## Funkciók

### 🩹 Mesh Javító
Kész, exportált modellekre (OBJ / PLY / STL). A **dupla héj javítás** Screened
Poisson rekonstrukcióval egyetlen tiszta felületté olvasztja a kettőződött részt,
plusz zajszűrés, lyukkitöltés, simítás és decimálás. Osztott (előtte/utána) 3D nézet.

### 🎞️ Nyers Szken Vágó
Közvetlenül a Creality Scan **nyers projektjeivel** dolgozik. A pontok a szken
**valódi keletkezési (idő) sorrendjében** jelennek meg — egy alsó, réteg-nézegető
stílusú csúszkával végignézed az időt, kijelölöd a bemozdult szakaszt, és kivágod.
A kivágás a nyers képkockákat törli, így a **Creality Scanben** újrafuzionálva a
hibás szakasz nélkül áll össze a modell.

## Felépítés

- `backend/` — FastAPI szerver (Python). Mesh-feldolgozás [PyMeshLab](https://pymeshlab.readthedocs.io/)
  + [trimesh](https://trimesh.org/); a nyers-projekt olvasás sima PLY-ból.
- `frontend/` — React + Vite + [three.js](https://threejs.org/) felület.
- `launcher.py` — rejtett módban indítja a szervert és megnyitja a böngészőt
  (ebből készül a `3D Scan Doctor.exe` PyInstallerrel).

## Telepítés és futtatás

**Backend** (Python 3.12 ajánlott):
```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install fastapi uvicorn pymeshlab trimesh numpy scipy python-multipart
```

**Frontend** (Node.js):
```bash
cd frontend
npm install
npm run build      # a backend a frontend/dist-et szolgálja ki
```

**Indítás:**
```bash
cd backend
python main.py     # http://127.0.0.1:8000
```
Vagy fejlesztéshez a frontend külön: `npm run dev` (a `frontend` mappában).

## Megjegyzések

- A **Nyers Szken Vágó** a Creality Scan lokális projektjeit olvassa
  (`%LOCALAPPDATA%\Creality\CrealityScan\Projects`), és a `pc_after.ply` fájlból
  dolgozik — a Creality Scan telepítése szükséges hozzá.
- A projekt **nem áll kapcsolatban a Creality-vel**; interoperabilitási céllal
  készült saját eszköz, a felhasználó saját szkenjeinek javítására.
