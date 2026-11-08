"""NyanwHair 0.35.14 Guided Surfaces (preview)."""
bl_info = {
    'name': 'NyanwHair - Guided Surfaces',
    'author': 'NyanWake',
    'version': (0,35,14),
    'blender': (5,1,0),
    'location': 'View3D > Sidebar > NyanwHair',
    'description': 'Quad-oriented hair ribbons with directed curve endpoint controls',
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
