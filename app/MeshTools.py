#!/usr/bin/env python3

import json
import sys
import os
import pathlib
import subprocess
import tempfile
import numpy as np
import trimesh
import re
from typing import Dict, Any, List, Tuple
from tempfile import TemporaryDirectory

MAX_INPUT_SIZE = 80 * 1024 * 1024  # 80MB
MAX_FACES = 1000000
MAX_SOURCE_CHARS = 100000

# Security checks for OpenSCAD source
BLOCKED_DIRECTIVES = [r'\b(import|include|use|surface)\b']


def handle_error(message: str) -> None:
    """Output error message in specified format and exit."""
    print(json.dumps({'error': message}), file=sys.stdout)
    sys.exit(1)


def read_json_input() -> Dict[str, Any]:
    """Read JSON from stdin."""
    try:
        input_data = sys.stdin.read()
        if not input_data:
            handle_error('No input provided')
        return json.loads(input_data)
    except json.JSONDecodeError as e:
        handle_error(f'Invalid JSON: {str(e)}')


def validate_path(path: str, root: str = None) -> pathlib.Path:
    """Validate and resolve path within root directory."""
    if not path:
        handle_error('Path is required')
    
    # Resolve the path
    original = pathlib.Path(path)
    if original.is_symlink():
        handle_error("Symlinks are not allowed")
    resolved_path = original.resolve()
    
    # Check for symlinks
    if resolved_path.is_symlink():
        handle_error('Symlinks are not allowed')
    
    # If root is specified, check that path is within it
    if root:
        try:
            root_path = pathlib.Path(root).resolve()
            resolved_path.relative_to(root_path)
        except ValueError:
            handle_error('Path must be within the specified root directory')
    
    return resolved_path


def validate_stl_file(path: pathlib.Path) -> None:
    """Validate that file is an STL and within size limits."""
    if not path.exists():
        handle_error('File does not exist')
    
    # Check file size
    file_size = path.stat().st_size
    if file_size > MAX_INPUT_SIZE:
        handle_error(f'Input file exceeds maximum size of {MAX_INPUT_SIZE} bytes')
    
    # Check file extension
    if path.suffix.lower() != '.stl':
        handle_error('Only STL files are supported')


def check_mesh_validity(mesh: trimesh.Trimesh) -> None:
    """Check face cap and finite vertices on mesh."""
    # Check for finite vertices
    if len(mesh.faces) == 0 or not np.isfinite(mesh.vertices).all():
        handle_error('Mesh contains non-finite vertices')
    
    # Check face count
    if len(mesh.faces) > MAX_FACES:
        handle_error(f'Mesh exceeds maximum face count of {MAX_FACES}')


def inspect_mesh(path: str, root: str = None) -> Dict[str, Any]:
    """Inspect mesh data and return metadata."""
    resolved_path = validate_path(path)
    validate_stl_file(resolved_path)
    
    try:
        # Load mesh
        mesh = trimesh.load(str(resolved_path), force='mesh')
        
        # Check validity before expensive properties
        check_mesh_validity(mesh)
        
        # Get bounds
        bounds = mesh.extents.tolist()  # [x, y, z] in mm
        
        # Get triangle count
        triangles = len(mesh.faces)
        
        # Check if watertight
        watertight = mesh.is_watertight
        
        # Calculate volume
        volume = float(mesh.volume)  # Convert to float explicitly
        
        # Create preview data (cap at 50000 faces)
        preview_faces = min(triangles, 50000)
        preview_mesh = mesh.submesh([np.arange(preview_faces)], append=True)
        
        preview_info = {
            'vertices': preview_mesh.vertices.tolist(),
            'faces': preview_mesh.faces.tolist()
        }
        
        return {
            'bounds': bounds,
            'triangles': triangles,
            'watertight': bool(watertight),
            'path': str(resolved_path),
            'volume': volume,
            'preview': preview_info
        }
    except Exception as e:
        handle_error(f'Error inspecting mesh: {str(e)}')


def transform_mesh(path: str, output: str, scale: List[float], rotate: List[float], repair: bool, root: str = None) -> Dict[str, Any]:
    """Transform mesh with specified operations."""
    resolved_path = validate_path(path)
    validate_stl_file(resolved_path)
    
    # Validate output path
    if not root or not pathlib.Path(output).is_absolute() or pathlib.Path(output).suffix.lower() != '.stl':
        handle_error('Absolute STL output and root directory are required')
    output_path = validate_path(output, root)
    
    # Check if output file already exists
    if output_path.exists():
        handle_error('Output file already exists')
    
    # Validate scale values
    if not scale or len(scale) != 3:
        handle_error('Scale must be a list of 3 values')
    for s in scale:
        if not isinstance(s, (int, float)) or not np.isfinite(s) or s <= 0 or s > 100:
            handle_error('Scale values must be finite numbers between 0 and 100')
    
    # Validate rotation values
    if not rotate or len(rotate) != 3:
        handle_error('Rotation must be a list of 3 values')
    for r in rotate:
        if not isinstance(r, (int, float)) or not np.isfinite(r):
            handle_error('Rotation values must be finite numbers')
    
    try:
        # Load mesh
        mesh = trimesh.load(str(resolved_path), force='mesh')
        
        # Check validity before expensive properties
        check_mesh_validity(mesh)
        
        # Apply transformations
        if scale != [1.0, 1.0, 1.0]:
            mesh.apply_scale(scale)
        
        if rotate != [0.0, 0.0, 0.0]:
            # Use euler_matrix with radians
            rotation_matrix = trimesh.transformations.euler_matrix(*np.radians(rotate))
            mesh.apply_transform(rotation_matrix)
        
        # Repair if requested
        if repair:
            mesh.fix_normals()
            mesh.fill_holes()
            
        # Check face count after transformations
        check_mesh_validity(mesh)
        
        # Export to output path atomically using TemporaryDirectory
        with TemporaryDirectory(dir=output_path.parent) as temp_dir:
            temp_path = pathlib.Path(temp_dir) / 'temp.stl'
            
            # Export to temporary file
            mesh.export(str(temp_path))
            
            # Verify the temporary file is valid
            try:
                temp_mesh = trimesh.load(str(temp_path), force='mesh')
                check_mesh_validity(temp_mesh)
            except Exception as e:
                handle_error(f'Error validating temporary mesh: {str(e)}')
            
            # Atomic move using os.link
            os.link(str(temp_path), str(output_path))
        
        # Return inspection of the output
        return inspect_mesh(str(output_path), root)
        
    except Exception as e:
        handle_error(f'Error transforming mesh: {str(e)}')


def render_scad(source: str, output: str, root: str = None) -> Dict[str, Any]:
    """Render OpenSCAD source to STL."""
    # Validate source length
    if len(source) > MAX_SOURCE_CHARS:
        handle_error(f'Source exceeds maximum character limit of {MAX_SOURCE_CHARS}')
    
    # Strip comments and check for blocked directives
    # Remove multiline comments
    source_no_comments = re.sub(r'/\*.*?\*/', '', source, flags=re.DOTALL)
    # Remove line comments
    source_no_comments = re.sub(r'//.*', '', source_no_comments)
    
    # Check for blocked directives
    for directive_pattern in BLOCKED_DIRECTIVES:
        if re.search(directive_pattern, source_no_comments):
            handle_error('Blocked directive found in source')
    
    # Validate output path
    if not root or not pathlib.Path(output).is_absolute() or pathlib.Path(output).suffix.lower() != '.stl':
        handle_error('Absolute STL output and root directory are required')
    output_path = validate_path(output, root)
    
    # Check if output file already exists
    if output_path.exists():
        handle_error('Output file already exists')
    
    try:
        # Write source to temporary file
        with TemporaryDirectory(dir=output_path.parent) as temp_dir:
            tmp_scad_path = pathlib.Path(temp_dir) / 'source.scad'
            
            with open(tmp_scad_path, 'w') as f:
                f.write(source)
            
            # Create temporary STL path
            temp_stl_path = pathlib.Path(temp_dir) / 'temp.stl'
            
            # Run OpenSCAD
            cmd = ['/opt/homebrew/bin/openscad', '--export-format=binstl', '-o', str(temp_stl_path), str(tmp_scad_path)]
            result = subprocess.run(cmd, timeout=120, check=True, capture_output=True)
            
            # Verify the temporary STL is valid
            try:
                temp_mesh = trimesh.load(str(temp_stl_path), force='mesh')
                check_mesh_validity(temp_mesh)
            except Exception as e:
                handle_error(f'Error validating rendered mesh: {str(e)}')
            
            # Atomic move using os.link
            os.link(str(temp_stl_path), str(output_path))
        
        # Return inspection of the output
        return inspect_mesh(str(output_path), root)
        
    except subprocess.TimeoutExpired:
        handle_error('OpenSCAD rendering timed out')
    except subprocess.CalledProcessError as e:
        handle_error(f'OpenSCAD error: {e.stderr.decode("utf-8")}')
    except Exception as e:
        handle_error(f'Error rendering SCAD: {str(e)}')


def main() -> None:
    """Main entry point."""
    try:
        input_data = read_json_input()
        
        action = input_data.get('action')
        root = input_data.get('root')
        if action == 'inspect':
            result = inspect_mesh(input_data['path'])
        elif action == 'transform':
            result = transform_mesh(input_data['path'], input_data['output'], input_data.get('scale',[1,1,1]), input_data.get('rotate',[0,0,0]), input_data.get('repair',False), root)
        elif action == 'render':
            result = render_scad(input_data['source'], input_data['output'], root)
        else:
            handle_error('Unknown action')
        print(json.dumps(result, allow_nan=False))
    except Exception as e:
        handle_error(f'Unexpected error: {str(e)}')


if __name__ == '__main__':
    main()
