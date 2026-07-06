import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader';

export default function ThreeViewer({ modelUrl, wireframe, pointsMode, autoRotate }) {
  const containerRef = useRef(null);
  const canvasRef = useRef(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Keep references to clean up in effects
  const sceneRef = useRef(null);
  const rendererRef = useRef(null);
  const controlsRef = useRef(null);
  const cameraRef = useRef(null);
  const currentModelRef = useRef(null);

  // Initialize Three.js scene once
  useEffect(() => {
    if (!containerRef.current || !canvasRef.current) return;

    // Scene
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x060810);
    sceneRef.current = scene;

    // Camera
    const camera = new THREE.PerspectiveCamera(
      45,
      containerRef.current.clientWidth / containerRef.current.clientHeight,
      0.1,
      1000
    );
    camera.position.set(0, 0, 5);
    cameraRef.current = camera;

    // Renderer
    const renderer = new THREE.WebGLRenderer({
      canvas: canvasRef.current,
      antialias: true,
      alpha: false,
    });
    renderer.setSize(containerRef.current.clientWidth, containerRef.current.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    rendererRef.current = renderer;

    // Grid Floor
    const grid = new THREE.GridHelper(20, 20, 0x00f2fe, 0x1e293b);
    grid.position.y = -1;
    scene.add(grid);

    // Lights
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.4);
    scene.add(ambientLight);

    const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight1.position.set(5, 10, 7);
    dirLight1.castShadow = true;
    scene.add(dirLight1);

    const dirLight2 = new THREE.DirectionalLight(0x4facfe, 0.5);
    dirLight2.position.set(-5, 5, -5);
    scene.add(dirLight2);

    const pointLight = new THREE.PointLight(0x00f2fe, 1, 10);
    pointLight.position.set(0, 3, 0);
    scene.add(pointLight);

    // Controls
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.maxDistance = 50;
    controls.minDistance = 0.5;
    controlsRef.current = controls;

    // Resize Handler
    const handleResize = () => {
      if (!containerRef.current || !cameraRef.current || !rendererRef.current) return;
      const width = containerRef.current.clientWidth;
      const height = containerRef.current.clientHeight;

      cameraRef.current.aspect = width / height;
      cameraRef.current.updateProjectionMatrix();
      rendererRef.current.setSize(width, height);
    };

    window.addEventListener('resize', handleResize);

    // Animation Loop
    let animationFrameId;
    const animate = () => {
      animationFrameId = requestAnimationFrame(animate);

      if (controlsRef.current) {
        controlsRef.current.update();
      }

      if (autoRotate && currentModelRef.current) {
        currentModelRef.current.rotation.y += 0.005;
      }

      if (rendererRef.current && sceneRef.current && cameraRef.current) {
        rendererRef.current.render(sceneRef.current, cameraRef.current);
      }
    };
    animate();

    // Clean up
    return () => {
      window.removeEventListener('resize', handleResize);
      cancelAnimationFrame(animationFrameId);

      if (rendererRef.current) {
        rendererRef.current.dispose();
      }
      if (controlsRef.current) {
        controlsRef.current.dispose();
      }
    };
  }, []);

  // Update Auto-rotate status dynamically
  useEffect(() => {
    // handled inside animate loop using ref
  }, [autoRotate]);

  // Load 3D model GLTF/GLB
  useEffect(() => {
    if (!modelUrl || !sceneRef.current) return;

    setLoading(true);
    setError(null);

    // Clean up old model if exists
    if (currentModelRef.current) {
      sceneRef.current.remove(currentModelRef.current);
      currentModelRef.current.traverse((child) => {
        if (child.isMesh || child.isPoints) {
          child.geometry.dispose();
          if (Array.isArray(child.material)) {
            child.material.forEach((m) => m.dispose());
          } else {
            child.material.dispose();
          }
        }
      });
      currentModelRef.current = null;
    }

    const loader = new GLTFLoader();
    // Resolve absolute URL to backend local server if needed
    const backendUrl = modelUrl.startsWith('http') ? modelUrl : `http://127.0.0.1:8000${modelUrl}`;

    loader.load(
      backendUrl,
      (gltf) => {
        const model = gltf.scene;

        // Compute bounding box, then normalize the model to a fixed size.
        // Scans come in wildly different units (a person scanned in mm is
        // ~1800 units tall), so we scale everything to ~2 units — this keeps
        // the camera, grid, lights and point sizes working for any input.
        const box = new THREE.Box3().setFromObject(model);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z);

        const scaleF = maxDim > 0 ? 2.0 / maxDim : 1;
        model.scale.setScalar(scaleF);
        model.position.set(-center.x * scaleF, -center.y * scaleF, -center.z * scaleF);

        // Position camera based on normalized size (always ~2 units)
        const fov = cameraRef.current.fov * (Math.PI / 180);
        let cameraZ = Math.abs(1 / Math.tan(fov / 2));
        cameraZ *= 1.5; // zoom out a bit for padding

        cameraRef.current.position.set(0, 0, cameraZ || 5);
        cameraRef.current.lookAt(new THREE.Vector3(0, 0, 0));

        if (controlsRef.current) {
          controlsRef.current.target.set(0, 0, 0);
          controlsRef.current.update();
        }

        // Store model ref
        currentModelRef.current = model;
        sceneRef.current.add(model);

        // Apply render modes (wireframe or points)
        applyRenderModes(model, wireframe, pointsMode);

        setLoading(false);
      },
      undefined,
      (err) => {
        console.error('Error loading GLTF model:', err);
        setError('Nem sikerült betölteni a 3D modellt. Kérlek, próbáld újra.');
        setLoading(false);
      }
    );
  }, [modelUrl]);

  // Update render mode options (wireframe, pointsMode) on properties change
  useEffect(() => {
    if (currentModelRef.current) {
      applyRenderModes(currentModelRef.current, wireframe, pointsMode);
    }
  }, [wireframe, pointsMode]);

  // Function to apply material properties
  const applyRenderModes = (object, isWireframe, isPoints) => {
    object.traverse((child) => {
      if (child.isMesh) {
        // Toggle standard mesh visibility
        child.visible = !isPoints;
        
        if (child.material) {
          child.material.wireframe = isWireframe;
          child.material.roughness = 0.5;
          child.material.metalness = 0.1;
          child.material.side = THREE.DoubleSide;
        }
      }

      // Check if we need to render as a point cloud
      if (child.isPoints) {
        child.visible = isPoints;
      }
    });

    // Handle point cloud creation dynamically if it doesn't exist
    if (isPoints) {
      let pointsObject = object.getObjectByName('points-cloud-rep');
      
      if (!pointsObject) {
        // Build point cloud representation from meshes
        const pointsGroup = new THREE.Group();
        pointsGroup.name = 'points-cloud-rep';

        object.traverse((child) => {
          if (child.isMesh && child.name !== 'points-cloud-rep') {
            const geometry = child.geometry.clone();
            const material = new THREE.PointsMaterial({
              color: 0x00f2fe,
              size: 0.05,
              sizeAttenuation: true
            });
            const points = new THREE.Points(geometry, material);
            // Copy transform matrices
            points.position.copy(child.position);
            points.rotation.copy(child.rotation);
            points.scale.copy(child.scale);
            pointsGroup.add(points);
          }
        });
        
        object.add(pointsGroup);
      } else {
        pointsObject.visible = true;
      }
    } else {
      const pointsObject = object.getObjectByName('points-cloud-rep');
      if (pointsObject) {
        pointsObject.visible = false;
      }
    }
  };

  return (
    <div ref={containerRef} className="canvas-container">
      <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />
      {loading && (
        <div className="loading-overlay">
          <div className="spinner" />
          <div className="loading-text">3D Modell Betöltése...</div>
        </div>
      )}
      {error && (
        <div className="loading-overlay">
          <div style={{ color: '#ef4444', fontWeight: 600, textAlign: 'center', padding: '1rem' }}>
            {error}
          </div>
        </div>
      )}
    </div>
  );
}
