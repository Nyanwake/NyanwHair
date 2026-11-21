"""Alt+V creates one editable strip per chain, or a pointed fork."""
import bpy,bmesh
from bpy.types import Operator
from bpy.props import BoolProperty,FloatVectorProperty,IntProperty,FloatProperty
from mathutils import Vector
from . import addon as a,editing as e,extrusion as x
from .i18n import tr


def point(bm,source,offset):
    parents=[f for f in source.link_faces if not f.hide]
    if not parents:return x._wire(bm,source,offset)
    parent=bm.faces.active if bm.faces.active in parents else min(parents,key=lambda f:int(f[bm.faces.layers.int[a.L_ORDER]]))
    normal=parent.normal.copy();origin=source.co.copy();attrs=x._attributes(parent,bm)
    left=bm.verts.new(origin);right=bm.verts.new(origin);tip=bm.verts.new(origin+offset)
    for v in (left,right,tip):v[bm.verts.layers.int[a.L_VERTEX_GROUP]]=source[bm.verts.layers.int[a.L_VERTEX_GROUP]]
    identity=max(int(f[bm.faces.layers.int[a.L_ORDER]]) for f in bm.faces)+1
    face=x._face(bm,[source,left,tip,right],attrs,identity,(frozenset((source,)),frozenset((tip,))))
    direction=sum((source.co-edge.other_vert(source).co for edge in source.link_edges if edge.other_vert(source) not in (left,right)),Vector())
    data=x._finish(bm,offset,[tip],origin,direction,'BRANCH',1,x._records(bm))
    data['branch_supports']=(left.index,right.index,tuple(origin),tuple(normal));update(bm,data,offset)
    bm.faces.active=face;return data


def update(bm,data,offset):
    bm.verts.ensure_lookup_table();left,right,origin,normal=data['branch_supports'];origin=Vector(origin)
    across=Vector(normal).cross(offset)
    if across.length<1e-8:across=Vector((1.,0.,0.))
    across.normalize();across*=offset.length*.2
    bm.verts[left].co=origin+offset*.5+across;bm.verts[right].co=origin+offset*.5-across


def stage(bm,result,offset):
    a._prepare_bmesh(bm,result);bm.verts.index_update();bm.faces.index_update();bm.normal_update()
    selected={v for v in bm.verts if v.select and not v.hide}
    if len(selected)==1:return point(bm,next(iter(selected)),offset)
    chains=x._edge_chains(bm,selected)
    if not chains or any(closed for path,closed,edges in chains):raise ValueError(tr('Select an open guide chain or one point'))
    return x._chain(bm,result,offset,chains)


class NYANW35_OT_branch(Operator):
    bl_idname='nyanw35.branch';bl_label='Branch guides'
    bl_description='Alt+V: create one segment from an edge chain, or a fork from a point'
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
