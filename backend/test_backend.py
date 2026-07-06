import os
import pymeshlab
from cleaner import MeshCleaner

def run_test():
    print("Starting backend logic verification...")
    
    # 1. Create a dummy sphere mesh using PyMeshLab
    ms = pymeshlab.MeshSet()
    ms.create_sphere(subdiv=3) # creates a sphere mesh
    
    temp_dir = "temp_uploads"
    processed_dir = "processed_downloads"
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(processed_dir, exist_ok=True)
    
    input_file = os.path.join(temp_dir, "test_sphere.obj")
    output_file = os.path.join(processed_dir, "test_sphere_processed.glb")
    
    # Save the procedurally generated sphere to file
    ms.save_current_mesh(input_file)
    print(f"Created test sphere at: {input_file}")
    
    # 2. Run MeshCleaner with processing options
    options = {
        "remove_noise": True,
        "noise_min_faces": 10,
        "close_holes": True,
        "max_hole_size": 50,
        "smooth_type": "taubin",
        "smooth_steps": 5,
        "decimate": True,
        "decimate_perc": 0.5 # reduce faces by 50%
    }
    
    print("Running MeshCleaner.clean...")
    stats = MeshCleaner.clean(
        input_path=input_file,
        output_path=output_file,
        options=options
    )
    
    print("\n--- Processing Results ---")
    print(f"Before stats: {stats['before']}")
    print(f"After stats: {stats['after']}")
    print(f"Output GLB exists: {os.path.exists(output_file)}")
    
    # Clean up test files
    if os.path.exists(input_file):
        os.remove(input_file)
    if os.path.exists(output_file):
        os.remove(output_file)
        
    print("\nTest completed successfully!")

if __name__ == "__main__":
    run_test()
