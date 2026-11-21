"""Run Blender's Knife on curved guide rims, then retain only the actual cuts."""
import bpy,bmesh
from collections import Counter
from bpy.types import Operator
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from bpy_extras import view3d_utils as view
from . import addon as a,editing
from .i18n import tr

_ACTIVE=set()

def active(obj):return obj is not None and any(getattr(op,'_obj',None)==obj for op in _ACTIVE)


def proxy(bm,result):
    bm.verts.index_update();bm.faces.index_update()
    records=[(int(f[bm.faces.layers.int[a.L_ORDER]]),editing.boundary_vertices(f,bm)) for f in bm.faces]
    curves=result.edge_evaluator;pending=[]
    for edge in bm.edges:
        if edge.hide or not edge.link_faces:continue
        one,two=edge.verts
        if (one.index,two.index) not in curves.coefficients and (two.index,one.index) not in curves.coefficients:continue
        samples=[Vector(curves(one.index,two.index,i/12)) for i in range(1,12)]
        if max((point-one.co.lerp(two.co,(i+1)/12)).length for i,point in enumerate(samples))<1e-7:continue
        pending.append((one,two,samples))
    created=[]
    for one,two,samples in pending:
        previous=one;edge=bm.edges.get((one,two))
        for i,point in enumerate(samples):
            _,vertex=bmesh.utils.edge_split(edge,previous,1./(12-i));vertex.co=point;created.append(vertex)
            vertex[bm.verts.layers.int[a.L_VERTEX_GROUP]]=one[bm.verts.layers.int[a.L_VERTEX_GROUP]]
            previous=vertex;edge=bm.edges.get((previous,two))
    for identity,slots in records:
        face=next(f for f in bm.faces if int(f[bm.faces.layers.int[a.L_ORDER]])==identity)
        editing.assign_vertices(face,bm,slots)
    bm.verts.index_update();bm.verts.ensure_lookup_table()
    return tuple((v.index,tuple(v.co)) for v in created)


def finish(bm,result,original_count,proxy_points,context,matrix,smoothing=None):
    bm.verts.ensure_lookup_table();bm.verts.index_update()
    original={i:bm.verts[i] for i in range(original_count)}
    temporary={i:Vector(co) for i,co in proxy_points};new=[v for v in bm.verts if v.index>=original_count and (v.index not in temporary or len(v.link_edges)!=2)]
    order=bm.faces.layers.int[a.L_ORDER];owners_by_order={p.settings.order:p.index for p in result.plans_by_loop.values()};trees={}
    inverse=matrix.inverted_safe()
    def cast(point,owners):
        world=matrix@point;screen=view.location_3d_to_region_2d(context.region,context.region_data,world)
        if screen is None:return point
        origin=view.region_2d_to_origin_3d(context.region,context.region_data,screen);direction=view.region_2d_to_vector_3d(context.region,context.region_data,screen)
        if owners not in trees:
            faces=[f for f,o in zip(result.faces,result.owners) if o in owners];trees[owners]=BVHTree.FromPolygons(result.vertices,faces) if faces else None
        tree=trees[owners]
        if tree is None:return point
        hit,normal,index,distance=tree.ray_cast(inverse@origin,(inverse.to_3x3()@direction).normalized())
        if hit is not None:return tree.find_nearest(hit)[0]
        nearest=tree.find_nearest(point)[0]
        return nearest if nearest is not None else point
    for vertex in new:
        owners=frozenset(owners_by_order[int(f[order])] for f in vertex.link_faces if int(f[order]) in owners_by_order)
        vertex.co=cast(vertex.co,owners)
        vertex[bm.verts.layers.float[a.L_POINT_DENSITY]]=0.
        vertex[bm.verts.layers.float[a.L_POINT_RADIUS]]=0.
    dissolve=[bm.verts[i] for i,co in temporary.items() if i<len(bm.verts) and len(bm.verts[i].link_edges)==2 and (bm.verts[i].co-co).length<1e-7]
    if dissolve:bmesh.ops.dissolve_verts(bm,verts=dissolve,use_face_split=False,use_boundary_tear=False)
    counts=Counter(int(f[order]) for f in bm.faces)
    for face in bm.faces:
        if counts[int(face[order])]>1:a._write_boundary_pair(face,bm,(None,None))
    # Keep directed endpoint weights; new cut points start smooth.
    directions={};evaluate=result.edge_evaluator
    for (u,v),strength in (smoothing if smoothing is not None else result.directional_smoothing).items():
        one,two=original.get(u),original.get(v)
        if one is None or two is None or not one.is_valid or not two.is_valid:continue
        if bm.edges.get((one,two)) is not None:neighbor=two
        else:
            curved=(u,v) in evaluate.coefficients or (v,u) in evaluate.coefficients
            path=[Vector(evaluate(u,v,i/24)) if curved else one.co.lerp(two.co,i/24) for i in range(25)]
            def gap(vertex):
                distances=[]
                for p,q in zip(path,path[1:]):
                    chord=q-p;t=max(0.,min(1.,(vertex.co-p).dot(chord)/max(1e-20,chord.length_squared)))
                    distances.append((vertex.co-p-chord*t).length_squared)
                return min(distances)
            neighbor=min((edge.other_vert(one) for edge in one.link_edges),key=gap,default=None)
        if neighbor is not None:directions[one,neighbor]=strength
    a._restore_directions(bm,directions)
    a._prepare_bmesh(bm,result);bm.normal_update();bm.verts.index_update();bm.faces.index_update()


class NYANW35_OT_knife(Operator):
    bl_idname='nyanw35.knife';bl_label='Knife hair guides'
    bl_description='Use Blender Knife along the visible curved hair surface'
    bl_options={'REGISTER','UNDO','BLOCKING'}
    @classmethod
    def poll(cls,context):return bool(a.is_guide(context.edit_object) and context.region_data)
    def invoke(self,context,event):
        obj=context.edit_object
        if active(obj) or obj.data.shape_keys:return {'CANCELLED'}
        a._rebuild(obj);self._obj=obj;self._result=a._CACHE[obj.as_pointer()]['result']
        if not self._result.faces or len(context.objects_in_mode_unique_data)>1:return {'PASS_THROUGH'}
        bm=bmesh.from_edit_mesh(obj.data);self._backup=bm.copy();self._selection=a._selection_state(bm);self._count=len(bm.verts);self._smoothing=a._directional_smoothing(bm)
        self._live=obj.nh35.live;self._timer=None;obj.nh35.live=False
        try:
            self._points=proxy(bm,self._result);bmesh.update_edit_mesh(obj.data,loop_triangles=True,destructive=True)
            _ACTIVE.add(self)
            result=bpy.ops.mesh.knife_tool('INVOKE_DEFAULT',False,use_occlude_geometry=True,only_selected=False)
            if result!={'RUNNING_MODAL'}:self._end(context,True);return {'CANCELLED'}
            self._timer=context.window_manager.event_timer_add(.08,window=context.window)
            context.window_manager.modal_handler_add(self);return {'RUNNING_MODAL'}
        except (ValueError,RuntimeError,ReferenceError) as exc:
            self.report({'WARNING'},str(exc));self._end(context,True);return {'CANCELLED'}
    def _end(self,context,restore):
        try:
            if restore and self._obj.mode=='EDIT':editing.restore_backup(self._obj,self._backup,self._selection)
            self._obj.nh35.live=self._live;a._CACHE.pop(self._obj.as_pointer(),None);a._prepare_object(self._obj);a._rebuild(self._obj,True)
        finally:
            if self._timer is not None:context.window_manager.event_timer_remove(self._timer);self._timer=None
            if self._backup is not None:self._backup.free();self._backup=None
            _ACTIVE.discard(self)
            if context.area:context.area.tag_redraw()
    def modal(self,context,event):
        if self._backup is None:return {'CANCELLED'}
        if event.type!='TIMER':return {'PASS_THROUGH'}
        if any(op.bl_idname=='MESH_OT_knife_tool' for op in context.window.modal_operators):return {'PASS_THROUGH'}
        try:
            bm=bmesh.from_edit_mesh(self._obj.data)
            if len(bm.verts)==self._count+len(self._points) and len(bm.faces)==len(self._backup.faces):
                self._end(context,True);return {'CANCELLED'}
            def commit(stage):
                finish(stage,self._result,self._count,self._points,context,self._obj.matrix_world,self._smoothing)
                editing.validate_stage(stage,self._obj)
            self._native_committed=True;self._native_undo_removed=False
            stage=bm.copy()
            try:
                commit(stage);selection=a._selection_state(stage)
                # Replace Knife's temporary edit-undo step with the cleaned mesh.
                self._committing=True
                try:bpy.ops.ed.undo()
                finally:self._committing=False
                self._obj=context.edit_object;self._native_undo_removed=True
                editing.restore_backup(self._obj,stage,selection)
            finally:stage.free()
            self._end(context,False);return {'FINISHED'}
        except (ValueError,RuntimeError,ReferenceError) as exc:
            if getattr(self,'_native_committed',False) and not self._native_undo_removed:
                self._committing=True
                try:bpy.ops.ed.undo();self._obj=context.edit_object;self._native_undo_removed=True
                finally:self._committing=False
            self.report({'WARNING'},str(exc));self._end(context,True)
            return {'FINISHED'} if getattr(self,'_native_undo_removed',False) else {'CANCELLED'}
    def cancel(self,context):
        if getattr(self,'_backup',None) is not None:self._end(context,True)


def cancel_all(restore=True):
    for op in tuple(_ACTIVE):
        if getattr(op,'_committing',False):continue
        if restore:op.cancel(bpy.context);continue
        try:
            op._obj.nh35.live=op._live
        except ReferenceError:pass
        if op._timer is not None:bpy.context.window_manager.event_timer_remove(op._timer);op._timer=None
        if op._backup is not None:op._backup.free();op._backup=None
        _ACTIVE.discard(op)
