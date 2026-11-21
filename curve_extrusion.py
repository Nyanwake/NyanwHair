"""Curve an extrusion from its parent rails; keep one primary bend row."""
import bmesh
from mathutils import Vector
from . import addon as a,editing,extrusion,wirecurve


def configuration(bm,data,matrix):
    bm.verts.ensure_lookup_table()
    rails=data.get('curve_rails')
    if rails is None:
        source=bm.verts[data['wire_start']];end=bm.verts[data['indices'][0]];direction=end.co-source.co
        candidates=[edge.other_vert(source) for edge in source.link_edges if edge.other_vert(source) is not end]
        preferred=set(data.get('guide_parents',()))
        candidates=[v for v in candidates if v.index in preferred] or candidates
        parent=max(candidates,key=lambda v:(source.co-v.co).normalized().dot(direction.normalized()),default=None)
        rails=((parent.index,end.index,source.index),) if parent is not None else ((source.index,end.index,None),)
    starts=[matrix@bm.verts[start].co for start,end,middle in rails];ends=[matrix@bm.verts[end].co for start,end,middle in rails]
    start=sum(starts,Vector())/len(starts);end=sum(ends,Vector())/len(ends);chord=end-start
    middles=[matrix@bm.verts[middle].co for _,_,middle in rails if middle is not None]
    control=(start+end)*.5
    if middles:
        tangent=sum(middles,Vector())/len(middles)-start
        if tangent.length>1e-8:control=start+tangent.normalized()*chord.length*.5
    peak=max(.05,min(.95,(control-start).dot(chord)/max(1e-20,chord.length_squared)))
    bend=(control-start-chord*peak)*.5
    return dict(rails=rails,start=start,end=end,start_offsets=[p-start for p in starts],end_offsets=[p-end for p in ends],peak=peak,bend=bend,kind='TIP' if data.get('tip_curve') else 'EDGE' if data.get('edge_curve') else 'WIRE')


def coordinates(frame,bend,count,peak):
    centers,parameters,control=wirecurve.sample(frame['start'],frame['end'],bend,count,peak)
    first=control-frame['start'];last=frame['end']-control
    if first.length<1e-8:first=frame['end']-frame['start']
    if last.length<1e-8:last=first
    if first.length<1e-8:first=last=Vector((0.,0.,1.))
    rails=[[] for _ in frame['rails']]
    for center,t in zip(centers,parameters):
        tangent=(control-frame['start'])*(1.-t)+(frame['end']-control)*t
        if tangent.length<1e-8:tangent=first
        one=first.rotation_difference(tangent);two=last.rotation_difference(tangent)
        for row,offset,end_offset in zip(rails,frame['start_offsets'],frame['end_offsets']):
            row.append(center+(one@offset)*(1.-t)+(two@end_offset)*t)
    return rails,parameters


def _split_records(records,one,two,middle):
    directions,density,layout=records
    for u,v in ((one,two),(two,one)):
        value=directions.pop((u,v),None)
        if value is not None:directions[u,middle]=value
    for values,is_layout in ((density,False),(layout,True)):
        key=(one,two) if (one,two) in values else (two,one)
        value=values.pop(key,None)
        if value is None:continue
        total=(one.co-middle.co).length+(two.co-middle.co).length
        for vertex in (one,two):values[vertex,middle]=value*((vertex.co-middle.co).length/max(1e-20,total) if is_layout else 1.)


def insert(bm,frame,rails,parameters):
    bm.verts.ensure_lookup_table();bm.faces.index_update()
    records=list(extrusion._records(bm));saved=[(int(f[bm.faces.layers.int[a.L_ORDER]]),editing.boundary_vertices(f,bm)) for f in bm.faces]
    paths=[(bm.verts[start],bm.verts[end],bm.verts[middle] if middle is not None else None) for start,end,middle in frame['rails']]
    if frame['kind']=='TIP':
        middle_set={middle for start,end,middle in paths}
        records[0]={key:value for key,value in records[0].items() if not middle_set.intersection(key)}
    central=parameters.index(.5);made=[];seen=set()
    for ri,((start,end,middle),positions) in enumerate(zip(paths,rails)):
        if (start,end,middle) in seen:continue
        seen.add((start,end,middle));end.co=positions[-1]
        def supports(one,two,indices):
            previous=one
            for i in indices:
                edge=bm.edges.get((previous,two));_,vertex=bmesh.utils.edge_split(edge,previous,.5)
                vertex.co=positions[i];vertex[bm.verts.layers.int[a.L_VERTEX_GROUP]]=start[bm.verts.layers.int[a.L_VERTEX_GROUP]]
                vertex[bm.verts.layers.float[a.L_POINT_DENSITY]]=0.;vertex[bm.verts.layers.float[a.L_POINT_RADIUS]]=0.
                _split_records(records,previous,two,vertex);made.append((vertex,ri,i));previous=vertex
        if middle is None:supports(start,end,range(1,len(positions)-1))
        else:
            middle.co=positions[central];made.append((middle,ri,central))
            supports(start,middle,range(1,central));supports(middle,end,range(central+1,len(positions)-1))
    for identity,slots in saved:
        face=next(f for f in bm.faces if int(f[bm.faces.layers.int[a.L_ORDER]])==identity)
        editing.assign_vertices(face,bm,slots)
    a._restore_directions(bm,records[0]);a._restore_density(bm,records[1]);a._restore_layout(bm,records[2])
    bm.verts.index_update();editing._select(bm,list(dict.fromkeys(end for start,end,middle in paths)))
    return tuple((vertex.index,ri,i) for vertex,ri,i in made)


def move(bm,mapping,rails):
    bm.verts.ensure_lookup_table()
    for index,ri,i in mapping:bm.verts[index].co=rails[ri][i]


def preview(frame,mapping,result,matrix):
    evaluate=getattr(result,'edge_evaluator',None)
    if evaluate is None or frame['kind']=='WIRE':return None
    lines=[];steps=max(2,min(16,48//max(1,len(mapping)//len(frame['rails'])+1)))
    for ri,(start,end,middle) in enumerate(frame['rails']):
        path=[start]+[index for index,rail,i in sorted(mapping,key=lambda entry:entry[2]) if rail==ri]+[end];line=[]
        for one,two in zip(path,path[1:]):
            if (one,two) not in evaluate.coefficients and (two,one) not in evaluate.coefficients:return None
            if not line:line.append(tuple(matrix@Vector(evaluate(one,two,0.))))
            line.extend(tuple(matrix@Vector(evaluate(one,two,j/steps))) for j in range(1,steps+1))
        lines.append(tuple(line))
    return tuple(lines)
