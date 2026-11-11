"""Read-only Live seam coordinates; explicit guide snapping and Mirror application."""
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


def _copy_data(target,source,domain):
    for kind in ('int','float','string','float_vector','float_color','color','bool'):
        layers=getattr(domain.layers,kind,None)
        if layers is not None:
            for layer in layers.values():target[layer]=source[layer]
    layers=getattr(domain.layers,'uv',None)
    if layers is not None:
        for layer in layers.values():
            target[layer].uv=source[layer].uv
            for name in ('pin_uv','select','select_edge'):
                if hasattr(source[layer],name):setattr(target[layer],name,getattr(source[layer],name))


def apply_guides(bm,obj):
    from . import addon as a,groups,editing,extrusion
    a._prepare_bmesh(bm);uid=groups.active_id(obj);axes,distance=configuration(obj)
    faces=[f for f in bm.faces if groups.face_group(f,bm,uid)==uid]
    wires=[edge for edge in bm.edges if not edge.link_faces and all(groups.vertex_group(v,bm,uid)==uid for v in edge.verts)]
    vertices={v for face in faces for v in face.verts}|{v for edge in wires for v in edge.verts}|{v for v in bm.verts if not v.link_edges and groups.vertex_group(v,bm,uid)==uid}
    if not vertices:return False
    vertices=sorted(vertices,key=lambda v:v.index);edges=list({edge for face in faces for edge in face.edges}|set(wires))
    records=list(extrusion._records(bm));slots={face:editing.boundary_vertices(face,bm) for face in faces}
    layer=bm.faces.layers.int[a.L_ORDER];identity=max((int(f[layer]) for f in bm.faces),default=0)+1
    for vertex in vertices:
        for axis,on in enumerate(axes):
            if on and distance and abs(vertex.co[axis])<=distance:vertex.co[axis]=0.
    masks=[0]
    for axis,on in enumerate(axes):
        if on:masks+=[mask|(1<<axis) for mask in list(masks)]
    copies={};seen={frozenset(f.verts) for f in bm.faces}
    snapshots=[dict(values) for values in records]
    for mask in masks[1:]:
        mapping={}
        for source in vertices:
            effective=mask
            if distance:
                for axis,on in enumerate(axes):
                    if on and source.co[axis]==0.:effective&=~(1<<axis)
            if effective==0:mapping[source]=source;continue
            key=source,effective
            if key not in copies:
                vertex=bm.verts.new(source.co,source)
                vertex.co=tuple(-value if effective&(1<<axis) else value for axis,value in enumerate(source.co))
                vertex.select=source.select;vertex.hide=source.hide;copies[key]=vertex
            mapping[source]=copies[key]
        for source in faces:
            ring=[mapping[v] for v in source.verts]
            if mask.bit_count()%2:ring.reverse()
            signature=frozenset(ring)
            if signature in seen:continue
            face=bm.faces.new(ring,source);face[layer]=identity;identity+=1
            face.material_index=source.material_index;face.smooth=source.smooth;face.select=source.select;face.hide=source.hide
            corners={mapping[loop.vert]:loop for loop in source.loops}
            for loop in face.loops:_copy_data(loop,corners[loop.vert],bm.loops)
            editing.assign_vertices(face,bm,tuple(frozenset(mapping[v] for v in cap) for cap in slots[source]));seen.add(signature)
        for source in edges:
            pair=tuple(mapping[v] for v in source.verts);edge=bm.edges.get(pair)
            if edge is None:edge=bm.edges.new(pair)
            if edge is not source:
                _copy_data(edge,source,bm.edges);edge.smooth=source.smooth;edge.seam=source.seam
                edge.select=source.select;edge.hide=source.hide
        for target,source in zip(records,snapshots):
            target.update({(mapping[u],mapping[v]):value for (u,v),value in source.items() if u in mapping and v in mapping})
    a._restore_directions(bm,records[0]);a._restore_density(bm,records[1]);a._restore_layout(bm,records[2])
    bm.normal_update();bm.verts.index_update();bm.faces.index_update()
    if faces:editing.validate_stage(bm,obj)
    return True


def _axes(obj,values):
    from . import groups
    restoring=groups._RESTORING;groups._RESTORING=True
    try:
        group=groups.settings(obj)
        for axis,on in zip('xyz',values):setattr(group,'mirror_'+axis,on)
    finally:groups._RESTORING=restoring
    groups.remember(obj)


class NYANW35_OT_apply_mirror(Operator):
    bl_idname='nyanw35.apply_mirror'
    bl_label='Apply Mirror'
    bl_description="Make mirrored guides editable and turn off the active group's Mirror axes"
    bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):
        from . import addon as a
        return bool(a.is_guide(context.object) and context.object.mode in ('OBJECT','EDIT') and any(configuration(context.object)[0]))
    def execute(self,context):
        from . import addon as a,editing,groups
        obj=context.object
        if obj.data.shape_keys:
            self.report({'WARNING'},tr('Topology editing requires guides without Shape Keys'));return {'CANCELLED'}
        axes=configuration(obj)[0];bm,owned=a._mesh_bm(obj);backup=bm.copy();selection=a._selection_state(bm);committed=False
        try:
            uid=groups.active_id(obj)
            if not any(groups.face_group(f,bm,uid)==uid for f in bm.faces) and not any(not v.link_faces and groups.vertex_group(v,bm,uid)==uid for v in bm.verts):return {'CANCELLED'}
            if owned:
                if not apply_guides(bm,obj):return {'CANCELLED'}
                bm.to_mesh(obj.data);obj.data.update()
            elif not a._edit_transaction(obj,lambda stage:apply_guides(stage,obj)):return {'CANCELLED'}
            committed=True;_axes(obj,(False,False,False));a._prepare_object(obj);a._rebuild(obj,True);a._sync_ui(obj)
        except (ValueError,RuntimeError,ReferenceError) as exc:
            if committed:
                if owned:backup.to_mesh(obj.data);obj.data.update()
                else:editing.restore_backup(obj,backup,selection)
                _axes(obj,axes);a._CACHE.pop(obj.as_pointer(),None);a._rebuild(obj,True)
            self.report({'WARNING'},str(exc));return {'CANCELLED'}
        finally:
            backup.free()
            if owned:bm.free()
        if context.area:context.area.tag_redraw()
        return {'FINISHED'}


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
