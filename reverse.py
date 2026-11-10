"""One-shot creation of editable reverse guides; no generated reverse sheets."""
from .i18n import tr,ui as translated_layout
import bmesh
from bpy.types import Operator
from bpy.props import FloatProperty,BoolProperty
from mathutils import Vector
from . import addon as a,editing,extrusion


def create(bm,result,indices,distance,matrix,join=True,axes=()):
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
        vertex=bm.verts.new(source.co-inverse@(normal*distance));copies[key]=vertex
        for axis,on in enumerate(axes):
            if on and abs(source.co[axis])<1e-7:vertex.co[axis]=source.co[axis]
        for layers in (bm.verts.layers.float,bm.verts.layers.int):
            for layer in layers.values():vertex[layer]=source[layer]
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
        next_order+=1;created.append(face)
    borders={(face,frozenset((u,v))) for face,u,v,attrs,plan,rail in boundary}
    walls=[]
    for face,ring,attrs,slots,plan,normal in jobs:
        vertices={v.index:v for v in ring}
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


class NYANW35_OT_reverse(Operator):
    bl_idname='nyanw35.create_reverse'
    bl_label='Create reverse side'
    bl_description='Create an independent editable copy behind the selected segments'
    bl_options={'REGISTER','UNDO'}
    distance:FloatProperty(name='Offset',description='Distance behind the selected guides',default=.01,min=.000001,soft_max=.1,subtype='DISTANCE')
    join:BoolProperty(name='Connect free borders',default=True,description='Bridge open borders with editable segments; shared borders stay open')
    @classmethod
    def poll(cls,context):return bool(a.is_guide(context.edit_object))
    def invoke(self,context,event):
        obj=context.edit_object;a._rebuild(obj);bm=bmesh.from_edit_mesh(obj.data);result=a._CACHE[obj.as_pointer()]['result']
        faces=a._zone_ui_faces(bm,obj)
        if not faces:return {'CANCELLED'}
        widths=[]
        for face in faces:
            rows=result.surface_sections.get(face.index)
            if rows:
                row=rows[len(rows)//2];widths.append((obj.matrix_world@Vector(row[-1])-obj.matrix_world@Vector(row[0])).length)
        if widths:self.distance=max(.000001,sorted(widths)[len(widths)//2]*.15)
        return context.window_manager.invoke_props_dialog(self,width=320)
    def draw(self,context):
        layout=translated_layout(self.layout);layout.prop(self,'distance',text=tr('Offset'));layout.prop(self,'join',text=tr('Connect free borders'))
    def execute(self,context):
        obj=context.edit_object;a._rebuild(obj);cache=a._CACHE.get(obj.as_pointer(),{});result=cache.get('result')
        bm=bmesh.from_edit_mesh(obj.data);faces=a._zone_ui_faces(bm,obj)
        if not faces or result is None:return {'CANCELLED'}
        indices=[face.index for face in faces]
        try:
            def operation(stage):
                from . import groups
                group=groups.settings(obj);axes=tuple(getattr(group,'mirror_'+axis) for axis in 'xyz') if group else ()
                created=create(stage,result,indices,self.distance,obj.matrix_world,self.join,axes);editing.validate_stage(stage,obj);return created
            created=a._edit_transaction(obj,operation)
        except (ValueError,RuntimeError,ReferenceError) as exc:self.report({'WARNING'},str(exc));return {'CANCELLED'}
        a._CACHE.pop(obj.as_pointer(),None);a._prepare_object(obj);a._rebuild(obj,True)
        bm=bmesh.from_edit_mesh(obj.data);bm.faces.ensure_lookup_table();cached=a._CACHE[obj.as_pointer()]
        lookup={p.index:a._zone_key(p) for p in cached['result'].plans_by_loop.values()}
        cached['zone_selection']=frozenset(lookup[i] for i in created if i in lookup);cached['zone_selection_explicit']=True;cached['active']=created[-1]
        cached['native_selection_stamp']=a._native_selection_stamp(bm);a._sync_ui(obj)
        if context.area:context.area.tag_redraw()
        return {'FINISHED'}
