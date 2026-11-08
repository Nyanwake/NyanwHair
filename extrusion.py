"""Shared-vertex cap extension and open region extrusion on a staged BMesh."""
import bmesh
from mathutils import Vector
from . import addon as a
from . import editing as e


def _attributes(face,bm):
    values=[(layer,face[layer]) for layers in (bm.faces.layers.int,bm.faces.layers.float,bm.faces.layers.string) for layer in layers.values()]
    return values,face.material_index,face.smooth


def _face(bm,vertices,attributes,order,slots):
    ring=[]
    for vertex in vertices:
        if not ring or ring[-1] is not vertex:ring.append(vertex)
    if len(ring)>1 and ring[0] is ring[-1]:ring.pop()
    if len(ring)<3:return None
    face=bm.faces.new(ring);values,material,smooth=attributes
    for layer,value in values:face[layer]=value
    face.material_index=material;face.smooth=smooth;face[bm.faces.layers.int[a.L_ORDER]]=order
    e.assign_vertices(face,bm,slots)
    return face


def _records(bm):
    return a._directional_smoothing(bm,True),a._edge_density(bm,True),a._layout_lengths(bm,True)


def _finish(bm,offset,moving,origin,direction,mode,count,records):
    a._restore_directions(bm,records[0]);a._restore_density(bm,records[1]);a._restore_layout(bm,records[2])
    for vertex in moving:
        vertex[bm.verts.layers.float[a.L_POINT_DENSITY]]=0.
        vertex[bm.verts.layers.float[a.L_POINT_RADIUS]]=0.
    e._select(bm,list(moving));bm.normal_update();bm.verts.index_update();bm.faces.index_update()
    if any(len(edge.link_faces)>2 for edge in bm.edges):raise ValueError('Вытягивание создаёт три поверхности на одном ребре')
    return dict(mode=mode,count=count,indices=tuple(v.index for v in moving),bases=tuple(tuple(v.co-offset) for v in moving),anchor=tuple(origin),direction=tuple(direction.normalized() if direction.length else Vector((0,0,1))))


def _extend(bm,targets,offset):
    records=list(_records(bm));moving={};origins=[];directions=[]
    order=bm.faces.layers.int[a.L_ORDER]
    for face,chosen in targets:
        attributes=_attributes(face,bm);identity=int(face[order]);slots=list(e.boundary_vertices(face,bm));ring=list(face.verts);old_edges=list(face.edges)
        replacements={}
        for index in chosen:
            cap=slots[index];vertices=list(cap);center=sum((v.co for v in vertices),Vector())/len(vertices);origins.append(center)
            if len(cap)==1:
                first=vertices[0];i=ring.index(first);neighbor=ring[(i+1)%len(ring)];old=first.co.copy()
                directions.append(old-(ring[i-1].co+neighbor.co)*.5)
                across=(neighbor.co-ring[i-1].co)*.15
                if across.length<1e-8:raise ValueError('Не удалось раскрыть точечный кончик')
                first.co=old-across;second=bm.verts.new(old+across)
                for name in (a.L_POINT_DENSITY,a.L_POINT_RADIUS):second[bm.verts.layers.float[name]]=first[bm.verts.layers.float[name]]
                records=[{((second if u is first and v is neighbor else u),(second if v is first and u is neighbor else v)):value for (u,v),value in values.items()} for values in records]
                moving[first]=bm.verts.new(first.co+offset);moving[second]=bm.verts.new(second.co+offset)
                replacements[first]=[first,moving[first],moving[second],second]
                slots[index]=frozenset((moving[first],moving[second]))
            else:
                i=next(i for i,v in enumerate(ring) if v in cap and ring[(i+1)%len(ring)] in cap)
                first,second=ring[i],ring[(i+1)%len(ring)]
                directions.append(center-(ring[i-1].co+ring[(i+2)%len(ring)].co)*.5)
                for vertex in (first,second):
                    if vertex not in moving:moving[vertex]=bm.verts.new(vertex.co+offset)
                replacements[first]=[first,moving[first],moving[second]]
                slots[index]=frozenset((moving[first],moving[second]))
        polygon=[v for vertex in ring for v in replacements.get(vertex,(vertex,))]
        bm.faces.remove(face);_face(bm,polygon,attributes,identity,slots)
        for edge in old_edges:
            if edge.is_valid and not edge.link_faces:bm.edges.remove(edge)
    values=list(dict.fromkeys(moving.values()));origin=sum(origins,Vector())/len(origins);direction=sum(directions,Vector())
    return _finish(bm,offset,values,origin,direction,'EXTEND',sum(len(indices) for face,indices in targets),records)


def _edge_count(result,one,two,default=1):
    key=a.core.edge_key(one.index,two.index)
    return max(default,1+sum(k[0]=='s' and k[1:3]==key for k in result.vertex_keys))


def _volume(bm,result,offset,faces=(),band=None):
    records=list(_records(bm));layer=bm.faces.layers.int[a.L_ORDER];next_order=max((int(f[layer]) for f in bm.faces),default=0)+1
    walls=[];pieces=[];removed=[];old_edges=set();root=set();normal=Vector()
    if band is not None:
        source,plan,li,ri=band;verts={v.index:v for v in source.verts};left=[verts[i] for i in plan.left];right=[verts[i] for i in plan.right]
        attrs=_attributes(source,bm);old_edges.update(source.edges);removed.append(source);normal+=source.normal
        forward=list(source.verts)[(list(source.verts).index(left[0])+1)%len(source.verts)] is left[1]
        q=[left[li],left[li+1],right[ri+1],right[ri]]
        if not forward:q.reverse()
        q=[v for i,v in enumerate(q) if v is not q[i-1]]
        before=left[:li+1]+list(reversed(right[:ri+1]));after=left[li+1:]+list(reversed(right[ri+1:]))
        if not forward:before.reverse();after.reverse()
        original=list(e.boundary_vertices(source,bm));raw=a._read_caps(source,a._boundary_layer(bm));start=raw.index(plan.start);end=1-start
        one=original.copy();one[end]=frozenset((left[li],right[ri]));two=original.copy();two[start]=frozenset((left[li+1],right[ri+1]))
        pieces=[(before,attrs,one),(after,attrs,two)]
        cross={frozenset((left[li],right[ri])),frozenset((left[li+1],right[ri+1]))}
        for one,two in zip(q,q[1:]+q[:1]):
            width=plan.settings.width if frozenset((one,two)) in cross else _edge_count(result,one,two)
            walls.append((one,two,attrs,width));root.update((one,two))
    else:
        region=set(faces)
        for source in faces:
            attrs=_attributes(source,bm);normal+=source.normal;old_edges.update(source.edges);removed.append(source)
            plan=result.plans_by_loop.get(tuple(v.index for v in source.verts))
            if plan is None:raise ValueError('У выбранного сегмента нет действительной разметки')
            caps=e.boundary_vertices(source,bm)
            for loop in source.loops:
                if sum(f in region for f in loop.edge.link_faces)==2:continue
                one,two=loop.vert,loop.link_loop_next.vert
                default=plan.settings.width if frozenset((one,two)) in caps else 1
                width=_edge_count(result,one,two,default)
                walls.append((one,two,attrs,width));root.update((one,two))
    if not walls:raise ValueError('Не найден открытый контур основания')
    origin=sum((v.co for v in root),Vector())/len(root)
    moving={v:bm.verts.new(v.co+offset) for v in sorted(root,key=lambda v:v.index)}
    for source in removed:bm.faces.remove(source)
    used={int(f[layer]) for f in bm.faces}
    def identity(attrs):
        nonlocal next_order
        old=next(value for key,value in attrs[0] if key.name==a.L_ORDER)
        if old>0 and old not in used:value=old
        else:value=next_order;next_order+=1
        used.add(value);return value
    for ring,attrs,slots in pieces:
        if len(set(ring))>=3:_face(bm,ring,attrs,identity(attrs),slots)
    for one,two,attrs,width in walls:
        new=_face(bm,(one,two,moving[two],moving[one]),attrs,identity(attrs),(frozenset((one,two)),frozenset((moving[one],moving[two]))))
        a._set_zone(new,bm,'width',max(1,min(32,width)))
        records[0][one,moving[one]]=0.;records[0][two,moving[two]]=0.
    for edge in old_edges:
        if edge.is_valid and not edge.link_faces:bm.edges.remove(edge)
    for vertex in list(bm.verts):
        if not vertex.link_edges:bm.verts.remove(vertex)
    return _finish(bm,offset,list(moving.values()),origin,normal,'VOLUME',len(walls),records)


def extrude(bm,result,offset):
    a._prepare_bmesh(bm,result);bm.verts.index_update();bm.faces.index_update();bm.normal_update()
    if 'FACE' in bm.select_mode:
        faces=[f for f in bm.faces if f.select]
        if faces:return _volume(bm,result,offset,faces=faces)
    selected={v for v in bm.verts if v.select};targets=[]
    for face in bm.faces:
        caps=a._read_caps(face,a._boundary_layer(bm))
        if not caps or any(c is None or c[0] not in ('edge','point') for c in caps):continue
        slots=e.boundary_vertices(face,bm);chosen=[]
        for i,cap in enumerate(slots):
            if not cap or not cap<=selected:continue
            if len(cap)==1:
                if len(next(iter(cap)).link_faces)!=1:continue
            else:
                edge=bm.edges.get(tuple(cap))
                if edge is None or len(edge.link_faces)!=1:continue
                if 'EDGE' in bm.select_mode and not edge.select:continue
            chosen.append(i)
        if chosen:targets.append((face,chosen))
    if targets:return _extend(bm,targets,offset)
    # Two opposite rail edges delimit a band; its old surface is removed.
    bands=[]
    for face in bm.faces:
        plan=result.plans_by_loop.get(tuple(v.index for v in face.verts))
        if plan is None:continue
        selected_edges={a.core.edge_key(*[v.index for v in edge.verts]) for edge in face.edges if edge.select}
        left=[i for i,key in enumerate(a.core.physical_edges(plan.left)) if key in selected_edges]
        right=[i for i,key in enumerate(a.core.physical_edges(plan.right)) if key in selected_edges]
        if len(left)==len(right)==1:bands.append((face,plan,left[0],right[0]))
    if len(bands)==1:return _volume(bm,result,offset,band=bands[0])
    if len(bands)>1:raise ValueError('Для E-Face выбери одну пару противоположных рёбер или используй режим граней')
    return e.single_extrude_stage(bm,result,offset)
