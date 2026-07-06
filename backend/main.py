import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from raw_editor import router as raw_router

app = FastAPI(title="Creality Scan Timeline Cutter API")

# Allow the dev frontend (vite) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROCESSED_DIR = os.path.join(BASE_DIR, "processed_downloads")
os.makedirs(PROCESSED_DIR, exist_ok=True)

# Serve exported clouds / surface meshes to the viewer.
app.mount("/static/processed", StaticFiles(directory=PROCESSED_DIR), name="processed")

# The timeline cutter API (browse Creality Scan projects, load, cut).
app.include_router(raw_router)

# Serve the built frontend (frontend/dist) at the root URL, if it exists.
# Must be mounted last so /api and /static routes take precedence.
FRONTEND_DIST = os.path.join(os.path.dirname(BASE_DIR), "frontend", "dist")
if os.path.isdir(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
