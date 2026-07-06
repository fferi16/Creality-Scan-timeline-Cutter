import os
import pymeshlab
import trimesh

# Browser-friendly face budget: previews above this are decimated for viewing,
# but the full-resolution mesh is always kept for download.
PREVIEW_MAX_FACES = 800_000


class MeshCleaner:
    @staticmethod
    def export_glb(ms, output_path: str):
        """Export the current mesh of a MeshSet to GLB via a temp OBJ."""
        temp_obj_path = output_path.replace(".glb", "_temp.obj")
        ms.save_current_mesh(temp_obj_path)
        try:
            t_mesh = trimesh.load(temp_obj_path)
            t_mesh.export(output_path)
        finally:
            if os.path.exists(temp_obj_path):
                os.remove(temp_obj_path)

    @staticmethod
    def export_preview_glb(ms, preview_path: str, max_faces: int = PREVIEW_MAX_FACES):
        """
        Export a GLB the browser can handle. Large meshes (full-body scans can be
        millions of faces) are decimated on a temporary copy; the MeshSet's
        current mesh is left untouched.
        """
        if ms.current_mesh().face_number() > max_faces:
            ms.generate_copy_of_current_mesh()
            # Clear any leftover selection: quadric collapse silently restricts
            # itself to selected faces (e.g. the ones close_holes marks)
            ms.set_selection_none(allverts=True, allfaces=True)
            ms.meshing_decimation_quadric_edge_collapse(
                targetfacenum=max_faces,
                preservenormal=True
            )
            try:
                MeshCleaner.export_glb(ms, preview_path)
            finally:
                ms.delete_current_mesh()
        else:
            MeshCleaner.export_glb(ms, preview_path)

    @staticmethod
    def clean(input_path: str, output_path: str, options: dict, preview_path: str = None) -> dict:
        """
        Cleans and optimizes a 3D mesh using PyMeshLab and converts it to GLB.
        
        :param input_path: Path to the input 3D model file (.obj, .ply, .stl)
        :param output_path: Path where the output .glb file will be saved
        :param options: Dictionary containing processing parameters:
            - remove_noise: bool (default False)
            - noise_min_faces: int (default 25)
            - fix_shells: bool (default False) - rebuild surface to merge double/overlapping shells
            - shell_detail: int (default 9) - Poisson octree depth (6=coarse, 11=very detailed)
            - shell_trim: float (default 2.0) - remove rebuilt surface farther than this % of
              the bounding box diagonal from the original scan (0 disables trimming)
            - close_holes: bool (default False)
            - max_hole_size: int (default 100)
            - smooth_type: str ('laplacian', 'taubin', or 'none', default 'none')
            - smooth_steps: int (default 5)
            - decimate: bool (default False)
            - decimate_perc: float (default 0.5) - percentage of faces to keep (0.0 to 1.0)
        :return: Dict containing stats ('before' and 'after' vertex/face counts)
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")
            
        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(input_path)
        
        # Get stats before processing
        current_mesh = ms.current_mesh()
        before_stats = {
            "vertices": current_mesh.vertex_number(),
            "faces": current_mesh.face_number(),
            "is_watertight": current_mesh.face_number() > 0 and current_mesh.vertex_number() > 0 # basic check
        }
        
        # 1. Remove disconnected components (noise removal)
        if options.get("remove_noise", False):
            min_faces = int(options.get("noise_min_faces", 25))
            ms.meshing_remove_connected_component_by_face_number(
                mincomponentsize=min_faces,
                removeunref=True
            )
            
        # 2. Double-shell repair: rebuild a single surface with Screened Poisson
        # reconstruction, then trim the parts that ballooned far from the scan.
        if options.get("fix_shells", False):
            depth = max(6, min(11, int(options.get("shell_detail", 9))))
            trim_perc = float(options.get("shell_trim", 2.0))

            original_id = ms.current_mesh_id()
            diag = ms.current_mesh().bounding_box().diagonal()

            ms.generate_copy_of_current_mesh()
            ms.compute_normal_per_vertex()
            ms.generate_surface_reconstruction_screened_poisson(
                depth=depth,
                preclean=True
            )
            recon_id = ms.current_mesh_id()

            if trim_perc > 0:
                ms.compute_scalar_by_distance_from_another_mesh_per_vertex(
                    measuremesh=recon_id,
                    refmesh=original_id,
                    signeddist=False
                )
                threshold = diag * (trim_perc / 100.0)
                ms.compute_selection_by_condition_per_vertex(condselect=f"q > {threshold}")
                ms.meshing_remove_selected_vertices_and_faces()

            # Drop small leftover bubbles the reconstruction may have created
            ms.meshing_remove_connected_component_by_diameter(
                mincomponentdiag=pymeshlab.PercentageValue(15)
            )

            if ms.current_mesh().face_number() == 0:
                raise ValueError(
                    "Double-shell repair removed the entire mesh. "
                    "Try a lower trim value or higher detail."
                )

        # 3. Close holes
        if options.get("close_holes", False):
            max_size = int(options.get("max_hole_size", 100))
            ms.meshing_close_holes(
                maxholesize=max_size,
                selfintersection=True
            )
            
        # 4. Denoising / Smoothing
        smooth_type = options.get("smooth_type", "none")
        if smooth_type == "laplacian":
            steps = int(options.get("smooth_steps", 5))
            ms.apply_coord_laplacian_smoothing(
                stepsmoothnum=steps,
                boundary=True,
                cotangentweight=True
            )
        elif smooth_type == "taubin":
            steps = int(options.get("smooth_steps", 5))
            ms.apply_coord_taubin_smoothing(
                stepsmoothnum=steps,
                lambda_=0.5,
                mu=-0.53
            )
            
        # 5. Decimation / Polygon count reduction
        if options.get("decimate", False):
            perc = float(options.get("decimate_perc", 0.5))
            # targetperc must be between 0.0 and 1.0
            if 0.0 < perc < 1.0:
                ms.set_selection_none(allverts=True, allfaces=True)
                ms.meshing_decimation_quadric_edge_collapse(
                    targetperc=perc,
                    preservetopology=True,
                    preservenormal=True
                )
                
        # Get stats after processing
        current_mesh = ms.current_mesh()
        after_stats = {
            "vertices": current_mesh.vertex_number(),
            "faces": current_mesh.face_number(),
            "is_watertight": current_mesh.face_number() > 0 and current_mesh.vertex_number() > 0
        }
        
        # Full-resolution GLB (this is what gets downloaded)
        MeshCleaner.export_glb(ms, output_path)

        # Decimated copy for the browser viewer, if requested
        if preview_path:
            MeshCleaner.export_preview_glb(ms, preview_path)

        return {
            "before": before_stats,
            "after": after_stats
        }
