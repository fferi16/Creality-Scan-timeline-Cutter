import os
import uuid
import shutil
import pymeshlab
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from cleaner import MeshCleaner
from raw_editor import router as raw_router

app = FastAPI(title="3D Scan Doctor API")

# Configure CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all for local dev ease, or restrict to http://localhost:5173
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "temp_uploads")
PROCESSED_DIR = os.path.join(BASE_DIR, "processed_downloads")

# Ensure directories exist
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PROCESSED_DIR, exist_ok=True)

# Mount static files so Three.js can load original/processed models directly
app.mount("/static/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/static/processed", StaticFiles(directory=PROCESSED_DIR), name="processed")

# Raw-project timeline editor (Creality Scan project browsing + interval cut)
app.include_router(raw_router)

class ProcessOptions(BaseModel):
    file_id: str
    remove_noise: bool = False
    noise_min_faces: int = 25
    fix_shells: bool = False
    shell_detail: int = 9
    shell_trim: float = 2.0
    close_holes: bool = False
    max_hole_size: int = 100
    smooth_type: str = "none" # 'laplacian', 'taubin', or 'none'
    smooth_steps: int = 5
    decimate: bool = False
    decimate_perc: float = 0.5

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    
    if ext not in [".obj", ".ply", ".stl"]:
        raise HTTPException(status_code=400, detail="Unsupported file format. Only OBJ, PLY, and STL are supported.")
        
    file_id = str(uuid.uuid4())
    
    # Save the original uploaded file
    original_save_name = f"{file_id}_original{ext}"
    original_path = os.path.join(UPLOAD_DIR, original_save_name)
    
    with open(original_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    try:
        # Get original mesh stats using PyMeshLab
        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(original_path)
        current_mesh = ms.current_mesh()
        
        stats = {
            "vertices": current_mesh.vertex_number(),
            "faces": current_mesh.face_number(),
        }
        
        # Convert original model to GLB for web viewing; huge scans get a
        # decimated preview so the browser stays responsive.
        original_glb_name = f"{file_id}_original.glb"
        original_glb_path = os.path.join(UPLOAD_DIR, original_glb_name)
        MeshCleaner.export_preview_glb(ms, original_glb_path)


        return {
            "file_id": file_id,
            "filename": filename,
            "stats": stats,
            "original_glb_url": f"/static/uploads/{original_glb_name}",
            "extension": ext
        }
        
    except Exception as e:
        # Clean up files in case of error
        if os.path.exists(original_path):
            os.remove(original_path)
        raise HTTPException(status_code=500, detail=f"Failed to process 3D file: {str(e)}")

@app.post("/api/process")
async def process_file(options: ProcessOptions):
    file_id = options.file_id
    
    # Find original file
    original_file = None
    for ext in [".obj", ".ply", ".stl"]:
        test_path = os.path.join(UPLOAD_DIR, f"{file_id}_original{ext}")
        if os.path.exists(test_path):
            original_file = test_path
            break
            
    if not original_file:
        raise HTTPException(status_code=404, detail="Uploaded file not found.")
        
    processed_glb_name = f"{file_id}_processed.glb"
    processed_glb_path = os.path.join(PROCESSED_DIR, processed_glb_name)
    preview_glb_name = f"{file_id}_processed_preview.glb"
    preview_glb_path = os.path.join(PROCESSED_DIR, preview_glb_name)

    try:
        # Clean the mesh using cleaner module
        stats = MeshCleaner.clean(
            input_path=original_file,
            output_path=processed_glb_path,
            options=options.model_dump(),
            preview_path=preview_glb_path
        )

        return {
            "success": True,
            "stats": stats,
            "processed_glb_url": f"/static/processed/{preview_glb_name}"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing mesh: {str(e)}")

@app.get("/api/download/{file_id}")
async def download_file(file_id: str):
    processed_path = os.path.join(PROCESSED_DIR, f"{file_id}_processed.glb")
    if not os.path.exists(processed_path):
        raise HTTPException(status_code=404, detail="Processed file not found.")
    return FileResponse(processed_path, filename=f"cleaned_{file_id}.glb", media_type="model/gltf-binary")

# Serve the built frontend (frontend/dist) at the root URL, if it exists.
# Must be mounted last so /api and /static routes take precedence.
FRONTEND_DIST = os.path.join(os.path.dirname(BASE_DIR), "frontend", "dist")
if os.path.isdir(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
