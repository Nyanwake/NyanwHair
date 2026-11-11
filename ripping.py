"""Rip sparse guide corners; ordinary mesh fans keep Blender's native V."""
import bpy,bmesh
from bpy.types import Operator
from bpy.props import BoolProperty,FloatVectorProperty,IntProperty
from mathutils import Vector
from . import addon as a,editing as e,extrusion as x
from .i18n import tr


def candidate(bm):
    selected=[v for v in bm.verts if v.select and not v.hide]
    return selected[0] if len(selected)==1 and len(selected[0].link_edges)==2 and selected[0].link_faces else None


def split(bm,offset,before=False):
    a._prepare_bmesh(bm);bm.normal_update();bm.verts.index_update();bm.faces.index_update()
    selected=[v for v in bm.verts if v.select and not v.hide]
    if len(selected)!=1:raise ValueError(tr('Select one guide point to split'))
    source=selected[0];faces=[f for f in source.link_faces if not f.hide]
    if not faces:raise ValueError(tr('Select a point on a segment'))
    face=bm.faces.active if bm.faces.active in faces else min(faces,key=lambda f:int(f[bm.faces.layers.int[a.L_ORDER]]))
    attrs=x._attributes(face,bm);identity=int(face[bm.faces.layers.int[a.L_ORDER]])
    ring=list(face.verts);slots=list(e.boundary_vertices(face,bm));i=ring.index(source);old_edges=list(face.edges)
    records=list(x._records(bm));origin=source.co.copy();direction=face.calc_center_median()-origin
    moving=bm.verts.new(source.co+offset)
    for layers in (bm.verts.layers.float,bm.verts.layers.int):
        for layer in layers.values():moving[layer]=source[layer]
    if len(faces)==1:
        cap=next((cap for cap in slots if source in cap),frozenset())
        if len(cap)==2:before=ring[(i+1)%len(ring)] in cap
        insert=i if before else i+1;polygon=ring[:insert]+[moving]+ring[insert:]
        slots=[frozenset((source,moving)) if cap==frozenset((source,)) else cap for cap in slots]
        neighbor=ring[i-1 if before else (i+1)%len(ring)]
        changed={frozenset((source,neighbor))}
        records[0][source,moving]=0.;records[0][moving,source]=0.
    else:
        polygon=[moving if v is source else v for v in ring]
        slots=[frozenset(moving if v is source else v for v in cap) for cap in slots]
        changed={frozenset(edge.verts) for edge in face.edges if source in edge.verts}
    for values in records:
        for (one,two),value in list(values.items()):
            if frozenset((one,two)) in changed:
                values[moving if one is source else one,moving if two is source else two]=value
    bm.faces.remove(face);new=x._face(bm,polygon,attrs,identity,slots)
    for edge in old_edges:
        if edge.is_valid and not edge.link_faces:bm.edges.remove(edge)
    a._restore_directions(bm,records[0]);a._restore_density(bm,records[1]);a._restore_layout(bm,records[2])
    e._select(bm,[moving]);bm.faces.active=new;bm.normal_update();bm.verts.index_update();bm.faces.index_update()
    return dict(mode='RIP',count=1,indices=(moving.index,),bases=(tuple(origin),),anchor=tuple(origin),
        direction=tuple(direction.normalized() if direction.length else Vector((1,0,0))))


class NYANW35_OT_rip(Operator):
    bl_idname='nyanw35.rip'
    bl_label='Rip guides'
    bl_description='Split a boundary guide point and move the new point'
    bl_options={'REGISTER','UNDO','BLOCKING'}
    from_panel:BoolProperty(default=False,options={'HIDDEN','SKIP_SAVE'})
    offset:FloatVectorProperty(name='Offset',subtype='TRANSLATION',default=(.01,0.,0.))
    before:BoolProperty(default=False,options={'HIDDEN','SKIP_SAVE'})
    curve_points:IntProperty(default=0,options={'HIDDEN','SKIP_SAVE'})
    curve_bend:FloatVectorProperty(default=(0.,0.,0.),options={'HIDDEN','SKIP_SAVE'})
    @classmethod
    def poll(cls,context):return bool(a.is_guide(context.edit_object))
    def _stage(self,bm,result,offset):return split(bm,offset,self.before)
    def invoke(self,context,event):
        bm=bmesh.from_edit_mesh(context.edit_object.data);vertex=candidate(bm)
        if vertex is None:return {'PASS_THROUGH'}
        self.before=False
        if not self.from_panel and context.region_data:
            ring=list(vertex.link_faces[0].verts);i=ring.index(vertex);matrix=context.edit_object.matrix_world
            scores=[a._screen_edge(context,event.mouse_region_x,event.mouse_region_y,matrix@vertex.co,matrix@ring[j].co)[0] for j in ((i-1)%len(ring),(i+1)%len(ring))]
            self.before=scores[0]<scores[1]
        return e.NYANW35_OT_extrude.invoke(self,context,event)
    execute=e.NYANW35_OT_extrude.execute
    _status=e.NYANW35_OT_extrude._status
    _move=e.NYANW35_OT_extrude._move
    _end=e.NYANW35_OT_extrude._end
    modal=e.NYANW35_OT_extrude.modal
    _modal=e.NYANW35_OT_extrude._modal
    cancel=e.NYANW35_OT_extrude.cancel
