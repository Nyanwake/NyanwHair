"""Alt+V opens a guide seam and fills the gap with one segment."""
import bpy,bmesh
from bpy.types import Operator
from bpy.props import BoolProperty,FloatVectorProperty,IntProperty,FloatProperty
from mathutils import Vector
from . import addon as a,editing as e,extrusion as x
from .i18n import tr


def point(bm,result,source,offset):
    parents=[f for f in source.link_faces if not f.hide]
    if not parents:return x._wire(bm,source,offset)
    candidates=tuple((int(f[bm.faces.layers.int[a.L_ORDER]]),tuple(f.calc_center_median()-source.co)) for f in parents)
    selected=max(candidates,key=lambda item:(Vector(item[1]).dot(offset),item[0]))[0]
    parent=next(f for f in parents if int(f[bm.faces.layers.int[a.L_ORDER]])==selected)
    slots=e.boundary_vertices(parent,bm)
    if len(parents)==1 and frozenset((source,)) in slots:
        from . import ripping
        ring=list(parent.verts);i=ring.index(source)
        choices=((0,tuple(ring[i-1].co-source.co)),(1,tuple(ring[(i+1)%len(ring)].co-source.co)))
        side=max(choices,key=lambda item:(Vector(item[1]).dot(offset),item[0]))[0]
        data=ripping.split(bm,offset,before=side==0)
        data.update(ripfill_corner=True,ripfill_choices=choices,ripfill_key=(side,False));return data
    ring=list(parent.verts);i=ring.index(source);attrs=x._attributes(parent,bm);identity=int(parent[bm.faces.layers.int[a.L_ORDER]])
    next_order=max(int(f[bm.faces.layers.int[a.L_ORDER]]) for f in bm.faces)+1
    shared=[v for v in (ring[i-1],ring[(i+1)%len(ring)]) if len(bm.edges.get((source,v)).link_faces)>1]
    path=[shared[0],source] if len(shared)==1 else [ring[i-1],source,ring[(i+1)%len(ring)]]
    records=list(x._records(bm));old_edges=list(parent.edges);origin=source.co.copy()
    moving=bm.verts.new(source.co+offset,source)
    changed={frozenset(edge.verts) for edge in parent.edges if source in edge.verts}
    for values in records:
        values.update({(moving if u is source else u,moving if v is source else v):value for (u,v),value in list(values.items()) if frozenset((u,v)) in changed})
    keep=len(parents)==1 and (parent.calc_center_median()-source.co).dot(offset)<0.
    if not keep:bm.faces.remove(parent)
    main=parent if keep else x._face(bm,[moving if v is source else v for v in ring],attrs,identity,tuple(frozenset(moving if v is source else v for v in cap) for cap in slots))
    gap=path+[moving] if len(path)==3 else [path[0],source,moving]
    ends=(frozenset((path[0],)),frozenset((path[-1],))) if len(path)==3 else (frozenset((path[0],)),frozenset((source,moving)))
    band=x._face(bm,gap,attrs,next_order,ends)
    for name,value in (('width',1),('bulge',0.),('bias',0.)):a._set_zone(band,bm,name,value)
    for edge in old_edges:
        if edge.is_valid and not edge.link_faces:bm.edges.remove(edge)
    data=x._finish(bm,offset,[moving],origin,main.calc_center_median()-origin,'RIPFILL',1,records)
    bm.faces.active=main
    data.update(ripfill_choices=candidates,ripfill_boundary=len(parents)==1,ripfill_key=(selected,keep))
    return data


def choice(data,offset):
    selected,vector=max(data['ripfill_choices'],key=lambda item:(Vector(item[1]).dot(offset),item[0]))
    keep=bool(data.get('ripfill_boundary') and Vector(vector).dot(offset)<0.)
    return selected,keep


def stage(bm,result,offset):
    a._prepare_bmesh(bm,result);bm.verts.index_update();bm.faces.index_update();bm.normal_update()
    selected={v for v in bm.verts if v.select and not v.hide}
    if len(selected)==1:return point(bm,result,next(iter(selected)),offset)
    chains=x._edge_chains(bm,selected)
    if not chains or any(closed for path,closed,edges in chains):raise ValueError(tr('Select an open guide chain or one point'))
    return x._chain(bm,result,offset,chains)


class NYANW35_OT_branch(Operator):
    bl_idname='nyanw35.branch';bl_label='Rip and fill guides'
    bl_description='Alt+V: open a guide seam and fill the gap with one segment'
    bl_options={'REGISTER','UNDO','BLOCKING'}
    from_panel:BoolProperty(default=False,options={'HIDDEN','SKIP_SAVE'})
    offset:FloatVectorProperty(name='Offset',subtype='TRANSLATION',default=(.05,0.,0.))
    curve_points:IntProperty(default=0,min=0,max=64)
    curve_bend:FloatVectorProperty(default=(0.,0.,0.))
    curve_peak:FloatProperty(default=.5,min=.05,max=.95)
    @classmethod
    def poll(cls,context):return bool(a.is_guide(context.edit_object))
    def _stage(self,bm,result,offset):return stage(bm,result,offset)
    execute=e.NYANW35_OT_extrude.execute
    invoke=e.NYANW35_OT_extrude.invoke
    _status=e.NYANW35_OT_extrude._status
    _move=e.NYANW35_OT_extrude._move
    _end=e.NYANW35_OT_extrude._end
    _curve_update=e.NYANW35_OT_extrude._curve_update
    _curve_wheel=e.NYANW35_OT_extrude._curve_wheel
    _curve_draw=e.NYANW35_OT_extrude._curve_draw
    modal=e.NYANW35_OT_extrude.modal
    _modal=e.NYANW35_OT_extrude._modal
    cancel=e.NYANW35_OT_extrude.cancel
