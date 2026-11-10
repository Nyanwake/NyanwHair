"""Shared-vertex cap extension and open region extrusion on a staged BMesh."""
import bpy
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


def _edge_curve(result,one,two):
    p,q=tuple(one.co),tuple(two.co)
    coefficients=getattr(result.edge_evaluator,'coefficients',{})
    data=coefficients.get((one.index,two.index))
    if data is not None:return tuple(tuple(v) for v in data)
    data=coefficients.get((two.index,one.index))
    if data is not None:
        q,p,mb,ma=data
        return tuple(p),tuple(q),tuple(-x for x in ma),tuple(-x for x in mb)
    chord=tuple(two.co-one.co)
    return p,q,chord,chord


def update_opening(bm,opening,offset):
    """Widen from the saved curves, never from a previous mouse event's positions."""
    bm.verts.ensure_lookup_table()
    for origin,span,carrier,rows in opening:
        origin=Vector(origin);distance=offset.length
        retreat=.6*span*max(distance,span*1e-5)/(max(distance,span*1e-5)+.5*span)
        positions=[]
        for base,end,curve in rows:
            p,q,ma,mb=map(Vector,curve);length=(q-p).length
            t=min(.6,retreat/max(length,1e-8));t2=t*t;t3=t2*t
            point=(2*t3-3*t2+1)*p+(-2*t3+3*t2)*q+(t3-2*t2+t)*ma+(t3-t2)*mb
            bm.verts[base].co=point;positions.append(point)
        center=positions[carrier];direction=offset.normalized() if distance>1e-8 else (origin-center).normalized()
        for (base,end,curve),point in zip(rows,positions):
            across=point-center;across-=direction*across.dot(direction)
            bm.verts[end].co=origin+offset+across


def _extend(bm,targets,offset,result):
    records=list(_records(bm));moving={};origins=[];directions=[];jobs=[];fans={};aliases={}
    order=bm.faces.layers.int[a.L_ORDER]
    # Snapshot all corners before changing any shared vertex or replacing a face.
    for face,chosen in targets:
        slots=list(e.boundary_vertices(face,bm));ring=list(face.verts)
        jobs.append((face,chosen,_attributes(face,bm),int(face[order]),slots,ring,list(face.edges)))
        for index in chosen:
            if len(slots[index])!=1:continue
            tip=next(iter(slots[index]));i=ring.index(tip)
            fans.setdefault(tip,set()).update((ring[i-1],ring[(i+1)%len(ring)]))
    descriptions=[]
    for tip,neighbors in fans.items():
        neighbors=sorted(neighbors,key=lambda v:v.index);origin=tip.co.copy()
        carrier=max(range(len(neighbors)),key=lambda i:(len(bm.edges.get((tip,neighbors[i])).link_faces),-neighbors[i].index))
        rows=[];span=sum((v.co-origin).length for v in neighbors)/len(neighbors)
        if span<1e-8:raise ValueError('Не удалось раскрыть точечный кончик')
        curves=[_edge_curve(result,tip,neighbor) for neighbor in neighbors]
        for i,(neighbor,curve) in enumerate(zip(neighbors,curves)):
            base=tip if i==carrier else bm.verts.new(origin)
            if base is not tip:
                for name in (a.L_POINT_DENSITY,a.L_POINT_RADIUS):base[bm.verts.layers.float[name]]=tip[bm.verts.layers.float[name]]
                base[bm.verts.layers.int[a.L_VERTEX_GROUP]]=tip[bm.verts.layers.int[a.L_VERTEX_GROUP]]
            aliases[tip,neighbor]=base;end=bm.verts.new(origin+offset);moving[base]=end;rows.append((base,end,curve))
        descriptions.append((tuple(origin),span,carrier,rows))
    if aliases:
        records=[{(aliases.get((u,v),u),aliases.get((v,u),v)):value for (u,v),value in values.items()} for values in records]
    for face,chosen,attributes,identity,slots,ring,old_edges in jobs:
        replacements={}
        for index in chosen:
            cap=slots[index];vertices=list(cap);center=sum((v.co for v in vertices),Vector())/len(vertices);origins.append(center)
            if len(cap)==1:
                tip=vertices[0];i=ring.index(tip);previous,following=ring[i-1],ring[(i+1)%len(ring)]
                directions.append(center-(previous.co+following.co)*.5)
                first,second=aliases[tip,previous],aliases[tip,following]
                replacements[tip]=[first,moving[first],moving[second],second]
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
    bm.verts.index_update()
    opening=tuple((origin,span,carrier,tuple((base.index,end.index,curve) for base,end,curve in rows)) for origin,span,carrier,rows in descriptions)
    update_opening(bm,opening,offset)
    values=list(dict.fromkeys(moving.values()))
    # Keep the central endpoint active at the cursor when opening a shared tip.
    if descriptions:
        active=descriptions[-1][3][descriptions[-1][2]][1];values.remove(active);values.append(active)
    origin=sum(origins,Vector())/len(origins);direction=sum(directions,Vector())
    data=_finish(bm,offset,values,origin,direction,'EXTEND',sum(len(indices) for face,indices in targets),records)
    data['opening']=opening
    return data


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
    candidates={v for source in removed for v in source.verts}
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
    for vertex in candidates:
        if vertex.is_valid and not vertex.link_edges:bm.verts.remove(vertex)
    return _finish(bm,offset,list(moving.values()),origin,normal,'VOLUME',len(walls),records)


def _wire(bm,source,offset):
    from . import groups
    origin=source.co.copy();direction=sum((source.co-edge.other_vert(source).co for edge in source.link_edges),Vector())
    vertex=bm.verts.new(origin+offset)
    vertex[bm.verts.layers.int[a.L_VERTEX_GROUP]]=source[bm.verts.layers.int[a.L_VERTEX_GROUP]] or groups.active_id(bpy.context.edit_object)
    bm.edges.new((source,vertex));e._select(bm,[vertex]);bm.verts.index_update()
    return dict(mode='WIRE',wire_start=source.index,indices=(vertex.index,),bases=(tuple(origin),),anchor=tuple(origin),
        direction=tuple(direction.normalized() if direction.length else Vector((0,0,1))))


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
                tip=next(iter(cap))
                if any(frozenset((tip,)) not in e.boundary_vertices(neighbor,bm) for neighbor in tip.link_faces):continue
            else:
                edge=bm.edges.get(tuple(cap))
                if edge is None or len(edge.link_faces)!=1:continue
                if 'EDGE' in bm.select_mode and not edge.select:continue
            chosen.append(i)
        if chosen:targets.append((face,chosen))
    if targets:return _extend(bm,targets,offset,result)
    if len(selected)==1:return _wire(bm,next(iter(selected)),offset)
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
