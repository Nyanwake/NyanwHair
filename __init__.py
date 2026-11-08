"""NyanwHair 0.35.11 Guided Surfaces (preview)."""
bl_info = {
    'name': 'NyanwHair - Guided Surfaces',
    'author': 'NyanWake',
    'version': (0,35,11),
    'blender': (5,1,0),
    'location': 'View3D > Sidebar > NyanwHair',
    'description': 'Editable face-zones with independent guided Catmull curves and unified hair quads',
    'category': 'Mesh',
}

try:
    import bpy
except ModuleNotFoundError:
    pass
else:
    from .addon import register, unregister

if __name__ == '__main__':
    register()
