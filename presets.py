"""Explicit creation of independent guide objects from small control templates."""
import bpy,bmesh
from bpy.types import Operator
from . import starter,cap_template


def create(context,cap=False):
    from . import addon as a,groups
    if context.edit_object:bpy.ops.object.mode_set(mode='OBJECT')
    mesh=bpy.data.meshes.new('NyanwHair Cap' if cap else 'NyanwHair Guide');bm=bmesh.new()
    try:
        (cap_template if cap else starter).add(bm);bm.to_mesh(mesh)
    finally:bm.free()
    mesh.update();obj=bpy.data.objects.new('NyanwHair Hair Cap' if cap else 'NyanwHair Guided Hair',mesh)
    context.collection.objects.link(obj);obj.location=context.scene.cursor.location
    for other in context.selected_objects:other.select_set(False)
    obj.select_set(True);context.view_layer.objects.active=obj
    obj.nh35.enabled=True;obj.nh35.show_preview=True;obj.display_type='WIRE';obj.show_in_front=False
    if cap:obj.nh35.detail_length=cap_template.REFERENCE_LENGTH
    a._prepare_object(obj)
    if cap:groups.settings(obj).mirror_x=True
    a._rebuild(obj,True)
    return obj


class NYANW35_OT_create_cap(Operator):
    bl_idname='nyanw35.create_cap'
    bl_label='Create hair cap'
    bl_description='Create a mirrored head cap with automatic cross-section curvature'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        create(context,cap=True)
        return {'FINISHED'}
