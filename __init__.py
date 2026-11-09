"""NyanwHair 0.35.27 Guided Surfaces (preview)."""
bl_info = {
    'name': 'NyanwHair - Guided Surfaces',
    'author': 'NyanWake',
    'version': (0,35,27),
    'blender': (5,1,0),
    'location': 'View3D > Sidebar > NyanwHair',
    'description': 'Guided hair with five viewport themes, fast live editing and independent volume',
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
