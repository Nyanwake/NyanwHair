"""Click selection on cached Live surfaces, including mirrored instances."""
import bpy
from bpy.types import Operator
from mathutils.bvhtree import BVHTree
from bpy_extras import view3d_utils


def hit(context,origin,direction):
    from . import addon as a,groups,decorations
    nearest=None
    for obj in context.selectable_objects:
        if not a.is_guide(obj) or not obj.nh35.show_preview or obj.nh35.transparency>=1.:continue
        if not groups.all_caches(obj):a._rebuild(obj)
        for record in a._render_caches(obj):
            result=decorations.preview(record,obj)
            if not result.faces:continue
            if record.get('pick_result') is not result:
                record['pick_bvh']=BVHTree.FromPolygons(result.vertices,result.faces);record['pick_result']=result
            for mask,reflection in decorations.instance_matrices(result,obj.matrix_world):
                matrix=reflection@obj.matrix_world;inverse=matrix.inverted_safe()
                point,normal,index,distance=record['pick_bvh'].ray_cast(inverse@origin,(inverse.to_3x3()@direction).normalized())
                if point is None:continue
                distance=(matrix@point-origin).length
                if nearest is None or distance<nearest[0]:nearest=(distance,obj)
    if nearest is None:return None
    distance,obj=nearest
    if not a._xray_enabled(context):
        cursor=origin.copy();epsilon=max(1e-6,distance*1e-6);depsgraph=context.evaluated_depsgraph_get()
        for _ in range(32):
            remaining=distance-(cursor-origin).length-epsilon
            if remaining<=0:break
            found,point,normal,index,occluder,matrix=context.scene.ray_cast(depsgraph,cursor,direction,distance=remaining)
            if not found:break
            if not a.is_guide(occluder.original):return None
            cursor=point+direction*epsilon
    return obj


class NYANW35_OT_pick_object(Operator):
    bl_idname='nyanw35.pick_object'
    bl_label='Select Live surface'
    bl_options={'UNDO','INTERNAL'}
    @classmethod
    def poll(cls,context):
        return bool(context.mode=='OBJECT' and context.area and context.area.type=='VIEW_3D' and
                    context.region and context.region.type=='WINDOW' and context.region_data)
    def invoke(self,context,event):
        if event.ctrl or event.alt or event.oskey:return {'PASS_THROUGH'}
        tool=context.workspace.tools.from_space_view3d_mode('OBJECT',create=False)
        if tool and tool.idname not in ('builtin.select','builtin.select_box','builtin.move','builtin.rotate','builtin.scale','builtin.transform'):return {'PASS_THROUGH'}
        xy=(event.mouse_region_x,event.mouse_region_y)
        origin=view3d_utils.region_2d_to_origin_3d(context.region,context.region_data,xy)
        direction=view3d_utils.region_2d_to_vector_3d(context.region,context.region_data,xy)
        obj=hit(context,origin,direction)
        if obj is None:return {'PASS_THROUGH'}
        selected=obj.select_get()
        if not event.shift:
            for other in context.selected_objects:other.select_set(False)
        obj.select_set(not selected if event.shift else True)
        if obj.select_get():context.view_layer.objects.active=obj
        context.area.tag_redraw();return {'FINISHED'}


class NYANW35_OT_hide_retopology(Operator):
    bl_idname='nyanw35.hide_retopology'
    bl_label='Hide Retopology fill'
    bl_description='Turn off Blender Retopology overlay in this viewport; other overlays remain enabled'
    @classmethod
    def poll(cls,context):
        from . import addon as a
        return bool(a.is_guide(context.edit_object) and context.space_data and context.space_data.type=='VIEW_3D' and context.space_data.overlay.show_retopology)
    def execute(self,context):
        context.space_data.overlay.show_retopology=False;context.area.tag_redraw();return {'FINISHED'}
