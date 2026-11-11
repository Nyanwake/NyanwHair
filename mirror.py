"""Read-only seam snapping for Live; explicit source snapping for editing tools."""
import bpy,bmesh
from bpy.types import Operator
from bpy.props import EnumProperty
from .i18n import tr


def configuration(obj,group=None):
    from . import groups
    group=group or groups.settings(obj)
    if group is None:return (),0.
    return tuple(getattr(group,'mirror_'+axis) for axis in 'xyz'),group.mirror_distance if group.mirror_merge else 0.


def positions(obj,points,indices,group=None):
    axes,distance=configuration(obj,group)
    if not distance or not any(axes):return points
    output=list(points)
    for index in indices:
        point=points[index]
        output[index]=tuple(0. if on and abs(value)<=distance else value for value,on in zip(point,axes))
    return output


def snap_bmesh(bm,obj):
    from . import groups
    axes,distance=configuration(obj)
    if not distance or not any(axes):return
    uid=groups.active_id(obj)
    for vertex in bm.verts:
        if groups.vertex_group(vertex,bm,uid)!=uid:continue
        for axis,on in enumerate(axes):
            if on and abs(vertex.co[axis])<=distance:vertex.co[axis]=0.


class NYANW35_OT_mirror_seam(Operator):
    bl_idname='nyanw35.mirror_seam'
    bl_label='Merge to Mirror plane'
    bl_description='Place selected points on the Mirror plane to join both halves'
    bl_options={'REGISTER','UNDO'}
    axis:EnumProperty(items=[('X','X',''),('Y','Y',''),('Z','Z','')])
    @classmethod
    def poll(cls,context):
        from . import addon as a,groups
        return bool(a.is_guide(context.edit_object) and groups.settings(context.edit_object) and any(configuration(context.edit_object)[0]))
    def execute(self,context):
        from . import addon as a,editing
        obj=context.edit_object;axis='XYZ'.index(self.axis)
        if not configuration(obj)[0][axis]:return {'CANCELLED'}
        if not any(v.select and not v.hide for v in bmesh.from_edit_mesh(obj.data).verts):return {'CANCELLED'}
        def operation(stage):
            for vertex in stage.verts:
                if vertex.select and not vertex.hide:vertex.co[axis]=0.
            editing.validate_stage(stage,obj)
        try:a._edit_transaction(obj,operation)
        except (ValueError,RuntimeError,ReferenceError) as exc:
            self.report({'WARNING'},str(exc));return {'CANCELLED'}
        a._rebuild(obj,True)
        if context.area:context.area.tag_redraw()
        return {'FINISHED'}
