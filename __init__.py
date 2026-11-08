"""NyanwHair 0.35.13 Guided Surfaces (preview)."""
bl_info = {
    'name': 'NyanwHair - Guided Surfaces',
    'author': 'NyanWake',
    'version': (0,35,13),
    'blender': (5,1,0),
    'location': 'View3D > Sidebar > NyanwHair',
    'description': 'Continuous hair splines, proportional ribbon detail and viewport X-ray',
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
