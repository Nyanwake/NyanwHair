"""Prepare rear-only guides once, then preview their shape on cheap copies."""
import math
import bmesh
from mathutils import Vector
from . import addon as a,editing as e,extrusion as x,cuts
from .i18n import tr


def _frame(plan,points,across):
    flow=Vector(a.core.cap_location(points,plan.boundary,plan.end))-Vector(a.core.cap_location(points,plan.boundary,plan.start))
    normal=Vector(across).cross(flow).normalized()
    if not normal.length:normal=Vector(plan.normal)
    if normal.dot(Vector(plan.normal))<0.:normal=-normal
    if plan.settings.signed_bulge<0.:normal=-normal
    return -normal


def prepare(bm,result,indices,obj,axes=()):
    from . import reverse,mirror
    mirror.snap_bmesh(bm,obj)
    a._prepare_bmesh(bm,result);bm.faces.ensure_lookup_table();bm.verts.index_update()
    layer=bm.faces.layers.int[a.L_ORDER];sources=[];rails=[]
    for index in indices:
        face=bm.faces[index];plan=result.plans_by_loop[tuple(v.index for v in face.verts)]
        sources.append((face,plan));vertices={v.index:v for v in face.verts}
        for path in (plan.left,plan.right):
            run=[]
            for one,two in zip(path,path[1:]):
                u,v=vertices[one],vertices[two];edge=bm.edges.get((u,v))
                plane=any(on and abs(result.source_points[one][axis])<1e-7 and abs(result.source_points[two][axis])<1e-7 for axis,on in enumerate(axes))
                if len(edge.link_faces)==1 and not plane:
                    if not run:run=[u]
                    run.append(v)
                elif run:rails.append((face,run));run=[]
            if run:rails.append((face,run))
    source_coords={v:result.source_points[v.index] for face,plan in sources for v in face.verts}
    pairs=[];created=reverse.create(bm,result,indices,0.,obj.matrix_world,False,axes,0.,copied_vertices=pairs)
    bm.faces.ensure_lookup_table();parents={}
    for (source,plan),index in zip(sources,created):
        face=bm.faces[index];parents[int(face[layer])]=(source,plan)
        a._set_zone(face,bm,'flip',plan.settings.signed_bulge<0.)
    flat=e.validate_stage(bm,obj);flat_plans={p.settings.order:p for p in flat.plans_by_loop.values()}
    pending=set(parents);provenance={};solved=flat
    while pending:
        identity=min(pending);face=next(f for f in bm.faces if int(f[layer])==identity)
        cuts.split(bm,face.index,.5,solved,provenance=provenance)
        pending.difference_update(parent for parent,fraction in provenance.values())
        solved=e.validate_stage(bm,obj)
    clones={(source,face):vertex for source,face,vertex in pairs}
    rim={clones[v,face] for face,path in rails for v in path}
    # A rear rail between two shared rim points needs its own support.
    # Otherwise welding those ends reuses the front edge for four faces.
    while True:
        rear={f for f in bm.faces if provenance.get(int(f[layer]),(int(f[layer]),1.))[0] in parents}
        edge=next((edge for edge in bm.edges if len(edge.link_faces)==2 and set(edge.link_faces)<=rear and set(edge.verts)<=rim),None)
        if edge is None:break
        bm.edges.index_update();one,two=edge.verts
        p,q,ma,mb=map(Vector,x._edge_curve(solved,one,two))
        a._insert_anchor_bmesh(bm,dict(edge_index=edge.index,ends=(one.index,two.index),t=.5,
            point_local=(p+q)*.5+(ma-mb)*.125),solved)
        solved=e.validate_stage(bm,obj)
    bm.verts.index_update();bm.faces.index_update()
    selected=[];owners={};fractions={}
    for face in bm.faces:
        identity=int(face[layer]);parent,fraction=provenance.get(identity,(identity,1.))
        if parent not in parents:continue
        selected.append(identity);fractions[identity]=fraction
        for vertex in face.verts:owners.setdefault(vertex,set()).add(parent)
    originals={vertex for source,face,vertex in pairs}
    locks={vertex:tuple(axis for axis,on in enumerate(axes) if on and abs(source_coords[source][axis])<1e-7) for source,face,vertex in pairs}
    normal_matrix=obj.matrix_world.to_3x3().inverted_safe().transposed();inverse=obj.matrix_world.inverted_safe().to_3x3()
    recipes=[]
    for vertex,identities in owners.items():
        bends=[];offsets=[]
        for identity in identities:
            source,plan=parents[identity];rows=flat.surface_sections[flat_plans[identity].index]
            centers=[Vector(a.core.mix(row[(len(row)-1)//2],row[len(row)//2],.5)) for row in rows]
            best=None
            for i,(one,two) in enumerate(zip(centers,centers[1:])):
                chord=two-one;t=max(0.,min(1.,(vertex.co-one).dot(chord)/max(1e-16,chord.length_squared)))
                distance=(vertex.co-one-chord*t).length_squared
                if best is None or distance<best[0]:best=(distance,i,t)
            _,i,t=best
            left=Vector(a.core.mix(rows[i][0],rows[i+1][0],t));right=Vector(a.core.mix(rows[i][-1],rows[i+1][-1],t));across=right-left
            fraction=max(0.,min(1.,(vertex.co-left).dot(across)/max(1e-16,across.length_squared)))
            weight=(0. if vertex in rim else .3) if vertex in originals else math.sin(math.pi*fraction)
            normal=_frame(plan,result.source_points,across)
            bends.append(normal*across.length*weight)
            offsets.append(inverse@(normal_matrix@normal).normalized())
        locked=locks.get(vertex,tuple(axis for axis,on in enumerate(axes) if on and abs(vertex.co[axis])<1e-7))
        recipes.append((vertex.index,tuple(vertex.co),tuple(sum(bends,Vector())/len(bends)),tuple(sum(offsets,Vector())/len(offsets)),locked))
    paths=[(int(face[layer]),tuple(v.index for v in path),tuple(clones[v,face].index for v in path)) for face,path in rails]
    return dict(orders=tuple(selected),fractions=fractions,recipes=tuple(recipes),rails=tuple(paths))


def _weld(bm,mapping):
    if not mapping:return
    layer=bm.faces.layers.int[a.L_ORDER]
    caps={int(f[layer]):tuple(frozenset(mapping.get(v,v) for v in cap) for cap in e.boundary_vertices(f,bm)) for f in bm.faces}
    records=[e._mapped(values,mapping,maximum=i==1) for i,values in enumerate(x._records(bm))]
    bmesh.ops.weld_verts(bm,targetmap=mapping)
    for face in bm.faces:e.assign_vertices(face,bm,caps[int(face[layer])])
    a._restore_directions(bm,records[0]);a._restore_density(bm,records[1]);a._restore_layout(bm,records[2])


def apply(bm,prepared,distance,bend,join):
    bm.verts.ensure_lookup_table();layer=bm.faces.layers.int[a.L_ORDER]
    for index,base,vector,shift,locked in prepared['recipes']:
        vertex=bm.verts[index];vertex.co=Vector(base)+Vector(vector)*bend+Vector(shift)*distance
        for axis in locked:vertex.co[axis]=0.
    for face in bm.faces:
        fraction=prepared['fractions'].get(int(face[layer]))
        if fraction is not None:
            gain=fraction*fraction/max(1e-12,math.hypot(fraction,bend*math.sin(math.pi*fraction)))
            a._set_zone(face,bm,'bulge',bend*gain*(-1. if face[bm.faces.layers.int[a.L_NORMAL_FLIP]] else 1.))
    paths=[(identity,[bm.verts[i] for i in one],[bm.verts[i] for i in two]) for identity,one,two in prepared['rails']]
    mapping={v:u for identity,one,two in paths for u,v in zip(one,two) if (u.co-v.co).length<1e-7} if join else {}
    paths=[(parent,one,[mapping.get(v,v) for v in two]) for parent,one,two in paths]
    _weld(bm,mapping)
    if join:
        faces={int(f[layer]):f for f in bm.faces};identity=max(faces,default=0)+1
        for parent,one,two in paths:
            start=0
            for k in range(1,len(one)):
                if k<len(one)-1 and one[k] is not two[k]:continue
                a_path=one[start:k+1];b_path=two[start:k+1];start=k
                if all(a is b for a,b in zip(a_path,b_path)):continue
                face=faces[parent];polygon=a_path+b_path[::-1]
                loop=next(l for l in face.loops if set(l.edge.verts)==set(a_path[:2]))
                if loop.vert is a_path[0]:polygon.reverse()
                wall=x._face(bm,polygon,x._attributes(face,bm),identity,
                    (frozenset((a_path[0],b_path[0])),frozenset((a_path[-1],b_path[-1]))));identity+=1
                for name,value in (('bulge',0.),('bias',0.),('flip',False),('width',2),('auto_curve',False)):a._set_zone(wall,bm,name,value)
        records=list(x._records(bm));x.straight_connections(bm,records,((u,v) for parent,one,two in paths for u,v in zip(one,two)))
        a._restore_directions(bm,records[0])
    chosen=set(prepared['orders']);created=[f for f in bm.faces if int(f[layer]) in chosen]
    for face in bm.faces:face.select_set(False)
    for edge in bm.edges:edge.select_set(False)
    for vertex in bm.verts:vertex.select_set(False)
    bm.select_history.clear()
    for face in created:face.select_set(True)
    bm.faces.active=created[-1];bm.select_history.add(created[-1]);bm.normal_update();bm.verts.index_update();bm.faces.index_update()
    if any(len(edge.link_faces)>2 for edge in bm.edges):raise ValueError(tr('Extrusion would join three surfaces on one edge'))
    return tuple(face.index for face in created)
