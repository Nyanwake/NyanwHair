"""One-shot creation of editable reverse guides; no generated reverse sheets."""
from .i18n import tr,ui as translated_layout
import bpy,bmesh
from bpy.types import Operator
from bpy.props import FloatProperty,BoolProperty
from mathutils import Vector
from . import addon as a,editing,extrusion


def create(bm,result,indices,distance,matrix,join=True,axes=(),bend=None,
           weld=False,positions=None,bend_gains=None,translation=None,copied_vertices=None):
    a._prepare_bmesh(bm,result);bm.faces.ensure_lookup_table();bm.verts.index_update()
    faces=[bm.faces[i] for i in indices]
    if not faces:raise ValueError(tr('Select a segment'))
    records=list(extrusion._records(bm));order=bm.faces.layers.int[a.L_ORDER]
    normal_matrix=matrix.to_3x3().inverted_safe().transposed();inverse=matrix.inverted_safe().to_3x3()
    jobs=[];boundary=[];selected=set()
    for face in faces:
        plan=result.plans_by_loop.get(tuple(v.index for v in face.verts))
        if plan is None:raise ValueError(tr('The selected segment has no valid layout'))
        normal=Vector(plan.normal);signed=plan.settings.bulge*(-1. if plan.settings.flip else 1.)
        if signed<0.:normal=-normal
        selected.update(face.verts)
        jobs.append((face,list(face.verts),extrusion._attributes(face,bm),editing.boundary_vertices(face,bm),plan,normal))
        if join:
            rails={a.core.edge_key(u,v) for rail in (plan.left,plan.right) for u,v in zip(rail,rail[1:])}
            for loop in face.loops:
                if len(loop.edge.link_faces)==1:
                    u,v=loop.vert,loop.link_loop_next.vert
                    if not any(on and abs(u.co[axis])<1e-7 and abs(v.co[axis])<1e-7 for axis,on in enumerate(axes)):
                        boundary.append((face,u,v,jobs[-1][2],plan,a.core.edge_key(u.index,v.index) in rails))
    source_indices={v:v.index for v in selected}
    welded={v for face,u,w,attrs,plan,rail in boundary for v in (u,w)} if weld else set()
    region=set(faces);fan={};normals={}
    # Point-only contacts need separate copies: a single copied connector
    # would otherwise collect four side walls on one edge.
    for source in sorted(selected,key=lambda v:v.index):
        pending=set(source.link_faces)&region
        while pending:
            first=min(pending,key=lambda f:f.index);pending.remove(first);neighbors=[first]
            for face in neighbors:
                for edge in source.link_edges:
                    if face not in edge.link_faces:continue
                    for other in set(edge.link_faces)&pending:pending.remove(other);neighbors.append(other)
            key=(source,first)
            for face in neighbors:fan[source,face]=key
    for face,ring,attrs,slots,plan,normal in jobs:
        for source in ring:
            key=fan[source,face];normals[key]=normals.get(key,Vector())+normal
    copies={}
    for key in sorted(normals,key=lambda item:(item[0].index,item[1].index)):
        normal=normals[key]
        source=key[0];normal=(normal_matrix@normal).normalized()
        if normal.length==0.:normal=(normal_matrix@Vector(jobs[0][4].normal)).normalized()
        if source in welded:copies[key]=source;continue
        position=(positions or {}).get(source.index)
        if position is None:position=source.co+translation if translation is not None else source.co-inverse@(normal*distance)
        else:position=Vector(position)-inverse@(normal*distance)
        vertex=bm.verts.new(position);copies[key]=vertex
        for axis,on in enumerate(axes):
            if on and abs(source.co[axis])<1e-7:vertex.co[axis]=source.co[axis]
        for layers in (bm.verts.layers.float,bm.verts.layers.int):
            for layer in layers.values():vertex[layer]=source[layer]
    if copied_vertices is not None:
        copied_vertices.extend((source,face,copies[key]) for (source,face),key in fan.items())
    next_order=max((int(f[order]) for f in bm.faces),default=0)+1;created=[]
    for source_face,ring,attrs,slots,plan,normal in jobs:
        clone=lambda v:copies[fan[v,source_face]]
        for loop in source_face.loops:
            u,v=loop.vert,loop.link_loop_next.vert
            for values in records:
                for edge in ((u,v),(v,u)):
                    if edge in values:values[clone(edge[0]),clone(edge[1])]=values[edge]
        face=extrusion._face(bm,[clone(v) for v in reversed(ring)],attrs,next_order,
            tuple(frozenset(clone(v) for v in slot) for slot in slots))
        if bend is not None:a._set_zone(face,bm,'bulge',bend*(bend_gains or {}).get(int(source_face[order]),1.))
        next_order+=1;created.append(face)
    borders={(face,frozenset((u,v))) for face,u,v,attrs,plan,rail in boundary}
    walls=[]
    for face,ring,attrs,slots,plan,normal in jobs:
        vertices={source_indices[v]:v for v in ring}
        for rail in (plan.left,plan.right):
            span=[]
            for a_index,b_index in zip(rail,rail[1:]):
                u,v=vertices[a_index],vertices[b_index]
                if (face,frozenset((u,v))) in borders:
                    if not span:span=[u]
                    span.append(v)
                elif span:walls.append((face,span,attrs,plan,True));span=[]
            if span:walls.append((face,span,attrs,plan,True))
    walls.extend((face,[u,v],attrs,plan,False) for face,u,v,attrs,plan,rail in boundary if not rail)
    for face,span,attrs,plan,rail in walls:
        if weld:continue
        clone=lambda v:copies[fan[v,face]]
        pieces=[span]
        polygon=span+list(reversed([clone(v) for v in span]))
        if len(span)>2 and a.core.length(a.core.newell([tuple(v.co) for v in polygon],tuple(range(len(polygon)))))<a.core.EPS:
            pieces=[span[i:i+2] for i in range(len(span)-1)]
        for path in pieces:
            copied=[clone(v) for v in path]
            if rail:
                polygon=path+copied[::-1]
                loop=next(loop for loop in face.loops if set(loop.edge.verts)==set(path[:2]))
                if loop.vert is path[0]:polygon.reverse()
                ends=(frozenset((path[0],copied[0])),frozenset((path[-1],copied[-1])))
            else:
                polygon=(path[1],path[0],copied[0],copied[1]);ends=(frozenset(path),frozenset(copied))
            wall=extrusion._face(bm,polygon,attrs,next_order,ends);next_order+=1
            a._set_zone(wall,bm,'bulge',0.);a._set_zone(wall,bm,'bias',0.);a._set_zone(wall,bm,'flip',False)
            a._set_zone(wall,bm,'width',2 if rail else plan.settings.width)
            if not rail:a._set_zone(wall,bm,'detail',2)
    a._restore_directions(bm,records[0]);a._restore_density(bm,records[1]);a._restore_layout(bm,records[2])
    for face in bm.faces:face.select_set(False)
    for edge in bm.edges:edge.select_set(False)
    for vertex in bm.verts:vertex.select_set(False)
    bm.select_history.clear()
    for face in created:face.select_set(True)
    bm.faces.active=created[-1];bm.select_history.add(created[-1]);bm.normal_update();bm.verts.index_update();bm.faces.index_update()
    if any(len(edge.link_faces)>2 for edge in bm.edges):raise ValueError(tr('Extrusion would join three surfaces on one edge'))
    return tuple(face.index for face in created)


def prepare_closed_base(bm,result,indices,obj,axes=()):
    """Give a zero-offset shell its own interior guide, sharing the outer rim.

    A reversed n-gon with the same vertices is a duplicate Blender face.
    A longitudinal guide on each side supplies independent editable interiors.
    """
    from . import cuts
    from . import mirror
    mirror.snap_bmesh(bm,obj)
    a._prepare_bmesh(bm,result);bm.faces.ensure_lookup_table()
    order=bm.faces.layers.int[a.L_ORDER]
    originals={int(bm.faces[i][order]):result.plans_by_loop[tuple(v.index for v in bm.faces[i].verts)] for i in indices}
    source=result;provenance={};pending=set(originals)
    while pending:
        identity=min(pending)
        face=next(f for f in bm.faces if int(f[order])==identity)
        cuts.split(bm,face.index,.5,result,provenance=provenance)
        pending.difference_update(parent for parent,gain in provenance.values())
        result=editing.validate_stage(bm,obj)
    # A short internal rail can join two outer-rim points. Give it an interior
    # support so the front and reverse do not reuse that edge four times.
    while True:
        selected={face for face in bm.faces if provenance.get(int(face[order]),(int(face[order]),1.))[0] in originals}
        def on_mirror(edge):return any(on and all(abs(v.co[axis])<1e-7 for v in edge.verts) for axis,on in enumerate(axes))
        rim={v for face in selected for edge in face.edges if len(edge.link_faces)==1 and not on_mirror(edge) for v in edge.verts}
        edge=next((edge for edge in bm.edges if set(edge.link_faces)<=selected and set(edge.verts)<=rim and
            (len(edge.link_faces)==2 or len(edge.link_faces)==1 and on_mirror(edge))),None)
        if edge is None:break
        bm.edges.index_update();one,two=edge.verts
        p,q,ma,mb=map(Vector,extrusion._edge_curve(result,one,two))
        a._insert_anchor_bmesh(bm,dict(edge_index=edge.index,ends=(one.index,two.index),t=.5,
            point_local=(p+q)*.5+(ma-mb)*.125),result)
        result=editing.validate_stage(bm,obj)
    indices=[];gains={};owners={}
    for face in bm.faces:
        identity=int(face[order]);parent,gain=provenance.get(identity,(identity,1.))
        if parent not in originals:continue
        indices.append(face.index);gains[identity]=gain
        for vertex in face.verts:owners.setdefault(vertex.index,set()).add(parent)
    bm.verts.ensure_lookup_table();recipes={}
    for index,parents in owners.items():
        point=bm.verts[index].co;values=[]
        for parent in parents:
            plan=originals[parent];rows=source.surface_sections[plan.index]
            centers=[Vector(a.core.mix(row[(len(row)-1)//2],row[len(row)//2],.5)) for row in rows]
            best=None
            for i,(one,two) in enumerate(zip(centers,centers[1:])):
                chord=two-one;fraction=max(0.,min(1.,(point-one).dot(chord)/max(1e-16,chord.length_squared)))
                distance=(point-one-chord*fraction).length_squared
                if best is None or distance<best[0]:best=(distance,i,fraction)
            _,i,fraction=best
            left=Vector(a.core.mix(rows[i][0],rows[i+1][0],fraction));right=Vector(a.core.mix(rows[i][-1],rows[i+1][-1],fraction))
            across=right-left;width=across.length
            x=max(0.,min(1.,(point-left).dot(across)/max(1e-16,across.length_squared)))
            base=left+across*x;normal=Vector(plan.normal)
            if plan.settings.bulge*(-1. if plan.settings.flip else 1.)<0.:normal=-normal
            baseline=point-normal*(point-base).dot(normal)
            values.append((baseline,normal*width*max(.3,4*x*(1-x))))
        recipes[index]=(sum((v[0] for v in values),Vector())/len(values),sum((v[1] for v in values),Vector())/len(values))
    return result,indices,gains,recipes


def construct(bm,result,indices,obj,distance,join,bend,axes):
    from . import backside
    prepared=backside.prepare(bm,result,indices,obj,axes)
    return backside.apply(bm,prepared,distance,bend,join)


_ACTIVE=set()


class NYANW35_OT_reverse(Operator):
    bl_idname='nyanw35.create_reverse'
    bl_label='Create reverse side'
    bl_description='Create an independent editable copy behind the selected segments'
    bl_options={'REGISTER','UNDO'}
    distance:FloatProperty(name='Offset',description='Distance behind the selected guides',default=.01,min=0.,soft_max=.1,subtype='DISTANCE')
    bend:FloatProperty(name='Convexity',description='Shape the reverse side across the strand',default=.08,min=0.,max=3.,step=5,precision=3)
    join:BoolProperty(name='Connect edges',default=True,description='Join the side guides; at zero offset share them without walls. End caps stay open')
    @classmethod
    def poll(cls,context):return bool(a.is_guide(context.edit_object))
    def _begin(self,context):
        self._obj=context.edit_object;a._rebuild(self._obj)
        if self._obj.data.shape_keys:
            self.report({'WARNING'},tr('Topology editing requires guides without Shape Keys'));return False
        cache=a._CACHE[self._obj.as_pointer()];bm=bmesh.from_edit_mesh(self._obj.data)
        faces=a._zone_ui_faces(bm,self._obj)
        if not faces:return False
        if any(tuple(v.index for v in face.verts) not in cache['result'].plans_by_loop for face in faces):
            self.report({'WARNING'},tr('The selected segment has no valid layout'));return False
        self._backup=bm.copy();self._selection=a._selection_state(bm)
        self._source=cache['result'];self._indices=tuple(face.index for face in faces)
        self._zone_state={key:cache[key] for key in ('active','zone_selection','zone_selection_explicit') if key in cache}
        from . import groups
        group=groups.settings(self._obj);self._axes=tuple(getattr(group,'mirror_'+axis) for axis in 'xyz') if group and group.mirror_merge else ()
        self._signature=None;self._error='';self._created=();self._rear_base=None
        _ACTIVE.add(self);return True
    def _preview(self,context):
        signature=(self.distance,self.bend,self.join)
        if signature==self._signature:return not self._error
        stage=None
        try:
            from . import backside
            if self._rear_base is None:
                base=self._backup.copy()
                try:self._rear_base=(base,backside.prepare(base,self._source,self._indices,self._obj,self._axes))
                except Exception:base.free();raise
            base,prepared=self._rear_base;stage=base.copy()
            created=backside.apply(stage,prepared,self.distance,self.bend,self.join)
            editing.validate_stage(stage,self._obj)
            editing.restore_backup(self._obj,stage,a._selection_state(stage))
            self._created=created;self._error='';self._signature=signature
            self._refresh(context)
            return True
        except (ValueError,RuntimeError,ReferenceError) as exc:
            self._error=str(exc);self._signature=signature
            return False
        finally:
            if stage is not None:stage.free()
    def _refresh(self,context):
        obj=self._obj;a._CACHE.pop(obj.as_pointer(),None);a._rebuild(obj,True)
        bm=bmesh.from_edit_mesh(obj.data);cache=a._CACHE[obj.as_pointer()]
        lookup={p.index:a._zone_key(p) for p in cache['result'].plans_by_loop.values()}
        cache['zone_selection']=frozenset(lookup[i] for i in self._created if i in lookup)
        cache['zone_selection_explicit']=True;cache['active']=self._created[-1]
        cache['native_selection_stamp']=a._native_selection_stamp(bm);a._sync_ui(obj)
        if context.area:context.area.tag_redraw()
    def invoke(self,context,event):
        if not self._begin(context):return {'CANCELLED'}
        widths=[]
        for index in self._indices:
            rows=self._source.surface_sections.get(index)
            if rows:
                row=rows[len(rows)//2];widths.append((self._obj.matrix_world@Vector(row[-1])-self._obj.matrix_world@Vector(row[0])).length)
        if not self.properties.is_property_set('distance') and widths:self.distance=sorted(widths)[len(widths)//2]*.15
        self._preview(context)
        return context.window_manager.invoke_props_dialog(self,width=320,title=tr('Create reverse side'),translate=False)
    def check(self,context):
        if getattr(self,'_backup',None) is not None:self._preview(context)
        return True
    def draw(self,context):
        layout=translated_layout(self.layout)
        layout.prop(self,'distance',text=tr('Offset'));layout.prop(self,'bend',text=tr('Convexity'))
        layout.prop(self,'join',text=tr('Connect edges'))
        if self.distance==0. and self.join:layout.label(text=tr('Shared sides · open ends'),icon='AUTOMERGE_ON')
        if getattr(self,'_error',''):layout.label(text=self._error,icon='ERROR')
    def _end(self,context,restore):
        try:
            if restore and self._obj.mode=='EDIT':
                editing.restore_backup(self._obj,self._backup,self._selection)
                a._CACHE.pop(self._obj.as_pointer(),None);a._rebuild(self._obj,True)
                if self._obj.as_pointer() in a._CACHE:
                    a._CACHE[self._obj.as_pointer()].update(self._zone_state);a._sync_ui(self._obj)
        except (ReferenceError,RuntimeError):pass
        finally:
            if self._backup is not None:self._backup.free();self._backup=None
            if self._rear_base is not None:self._rear_base[0].free();self._rear_base=None
            _ACTIVE.discard(self)
            if context.area:context.area.tag_redraw()
    def execute(self,context):
        if getattr(self,'_invalidated',False):return {'CANCELLED'}
        if getattr(self,'_backup',None) is None and not self._begin(context):return {'CANCELLED'}
        if not self._preview(context):
            self.report({'WARNING'},self._error);self._end(context,True);return {'CANCELLED'}
        a._prepare_object(self._obj);self._refresh(context);self._end(context,False)
        return {'FINISHED'}
    def cancel(self,context):
        if getattr(self,'_backup',None) is not None:self._end(context,True)


def cancel_all(restore=True):
    for operator in tuple(_ACTIVE):
        operator._invalidated=True;operator._end(bpy.context,restore)
