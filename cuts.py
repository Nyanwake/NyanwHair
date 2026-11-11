"""A single shared-cap cut schedule, committed through the existing transaction."""
from .i18n import tr
import math
import bmesh
from mathutils import Vector
from . import core


def network(result,zone,t):
    cache=getattr(result,'cap_cut_cache',None)
    if cache is None:cache={};result.cap_cut_cache=cache
    key=(zone,round(t,9))
    if key in cache:return cache[key]
    plans={p.index:p for p in result.plans_by_loop.values()};owners={}
    for p in plans.values():
        for end,(kind,i) in enumerate((p.start,p.end)):
            if kind=='edge':
                left=p.left[-1 if end else 0];right=p.right[-1 if end else 0]
                owners.setdefault(core.edge_key(left,right),[]).append((p.index,left,right))
    targets={zone:t};queue=[zone]
    for index in queue:
        p=plans[index];value=targets[index]
        for end,(kind,i) in enumerate((p.start,p.end)):
            if kind!='edge':continue
            left=p.left[-1 if end else 0];right=p.right[-1 if end else 0];fraction=value if left<right else 1.-value
            for other,a,b in owners[core.edge_key(left,right)]:
                wanted=fraction if a<b else 1.-fraction
                if other in targets:
                    if abs(targets[other]-wanted)>1e-7:raise ValueError(tr('A closed loop requires incompatible cut positions'))
                else:targets[other]=wanted;queue.append(other)
    cache[key]=targets
    return targets


def preview(result,zone,t):
    plans={p.index:p for p in result.plans_by_loop.values()};positions={};lines={}
    for index,value in network(result,zone,t).items():
        plan=plans[index];line=core.sample_cut_guide(result,index,value,32)
        for end,(kind,i) in enumerate((plan.start,plan.end)):
            if kind!='edge':continue
            edge=core.edge_key(plan.left[-1 if end else 0],plan.right[-1 if end else 0]);k=-1 if end else 0
            line[k]=positions.setdefault(edge,line[k])
        lines[index]=line
    return lines


def split(bm,zone,t,result,select_result=False,provenance=None):
    from . import addon as a,editing
    a._prepare_bmesh(bm,result)
    for seq in (bm.verts,bm.edges,bm.faces):seq.ensure_lookup_table();seq.index_update()
    schedule=network(result,zone,t);plans={p.index:p for p in result.plans_by_loop.values()}
    if any(not .001<value<.999 for value in schedule.values()):raise ValueError(tr('Cut is too close to the boundary'))
    original=set(bm.verts);order=bm.faces.layers.int[a.L_ORDER];vertices={v.index:v for v in bm.verts}
    directions=a._directional_smoothing(bm,True);density=a._edge_density(bm,True);layout=a._layout_lengths(bm,True)
    jobs=[];cap_jobs={};affected=set();source_faces=set()
    for index,value in schedule.items():
        face=bm.faces[index];source_faces.add(face);p=plans[index];settings=a._get_zone(face,bm)
        raw=a._read_caps(face,a._boundary_layer(bm));slots=[raw.index(cap) for cap in (p.start,p.end)]
        profile=core.sample_cut_guide(result,index,value,max(2,min(12,max(len(p.left),len(p.right))-1)))
        if any(not all(math.isfinite(v) for v in point) for point in profile):raise ValueError(tr('Invalid cut geometry'))
        ends=[]
        for end,(kind,i) in enumerate((p.start,p.end)):
            left=vertices[p.left[-1 if end else 0]];right=vertices[p.right[-1 if end else 0]]
            edge=core.edge_key(left.index,right.index) if kind=='edge' else None
            ends.append((kind,left,right,edge))
            if edge is not None:
                physical=value if left.index<right.index else 1.-value
                if edge in cap_jobs and abs(cap_jobs[edge][0]-physical)>1e-7:raise ValueError(tr('The shared boundary requires two different cuts'))
                cap_jobs.setdefault(edge,(physical,profile[-1 if end else 0]))
                affected.update(bm.edges.get((left,right)).link_faces)
        # Match the original fitting used by the single-zone split.
        grid=result.surface_sections[index];x=value*(len(grid[0])-1);col=min(len(grid[0])-2,int(x));blend=x-col
        v=core.bias_cross_position(value,settings.bias);factors=[[],[]]
        for row in grid:
            cut=core.mix(row[col],row[col+1],blend);size=core.length(core.sub(row[-1],row[0]))
            for side,distance,fraction in ((0,core.length(core.sub(cut,row[0])),v),(1,core.length(core.sub(row[-1],cut)),1.-v)):
                if distance>core.EPS:factors[side].append(size*fraction*fraction/distance)
        gains=[sorted(values)[len(values)//2] if values else .5 for values in factors]
        side=next((vertices[i] for i in p.left if i not in (p.right[0],p.right[-1])),None)
        if side is None:raise ValueError(tr('Could not determine the cut sides'))
        jobs.append(dict(index=index,identity=int(face[order]),settings=settings,ends=ends,slots=slots,profile=profile,side=side,value=value,gains=gains))
    affected.difference_update(source_faces);neighbor_caps=a._cap_records(bm,affected,result)
    if len(neighbor_caps)!=len(affected):raise ValueError(tr('Could not preserve neighbouring boundaries'))
    created={};split_records=[]
    for edge,(fraction,position) in cap_jobs.items():
        left,right=(vertices[i] for i in edge);source=bm.edges.get((left,right))
        _,point=bmesh.utils.edge_split(source,left,fraction);point.co=Vector(position);created[edge]=point;split_records.append((left,right,point))
    next_order=max(int(f[order]) for f in bm.faces)+1;children_all=[];chosen=None
    for job in jobs:
        ends=[left if kind=='point' else created[edge] for kind,left,right,edge in job['ends']]
        start,finish=ends
        candidates=[f for f in bm.faces if int(f[order])==job['identity'] and start in f.verts and finish in f.verts]
        if len(candidates)!=1:raise ValueError(tr('Could not find the source segment'))
        profile=list(job['profile']);profile[0]=tuple(start.co);profile[-1]=tuple(finish.co)
        if bmesh.utils.face_split(candidates[0],start,finish,coords=profile[1:-1]) is None:raise ValueError(tr('Blender could not split the segment'))
        children=[f for f in bm.faces if int(f[order])==job['identity']]
        if len(children)!=2:raise ValueError(tr('The cut must create two segments'))
        left_child=next((f for f in children if job['side'] in f.verts),None)
        if left_child is None:raise ValueError(tr('Could not determine the cut sides'))
        widths=core.width_budget_split(job['settings'].width,job['value'])
        for face in children:
            side=0 if face is left_child else 1;face[order]=job['identity'] if side==0 else next_order
            if side:next_order+=1
            caps=[None,None]
            for k,(kind,left,right,edge) in enumerate(job['ends']):caps[job['slots'][k]]=frozenset((ends[k],)) if kind=='point' else frozenset((ends[k],left if side==0 else right))
            editing.assign_vertices(face,bm,caps)
            a._set_zone(face,bm,'width',widths[side]);a._set_zone(face,bm,'bulge',job['settings'].bulge*job['gains'][side])
            a._set_zone(face,bm,'bias',job['settings'].bias);a._set_zone(face,bm,'flip',job['settings'].flip)
            if provenance is not None:provenance[int(face[order])]=(job['identity'],job['gains'][side])
        children_all.extend(children)
        if job['index']==zone:chosen=left_child;answer=(job['settings'].width,*widths)
    a._restore_cap_records(bm,neighbor_caps,exclude=children_all)
    a._restore_directions(bm,directions,split_records);a._restore_density(bm,density,split_records);a._restore_layout(bm,layout,split_records)
    for vertex in bm.verts:
        if vertex not in original:
            vertex[bm.verts.layers.float[a.L_POINT_DENSITY]]=0.;vertex[bm.verts.layers.float[a.L_POINT_RADIUS]]=0.
    bm.verts.index_update();bm.faces.index_update();points=[tuple(v.co) for v in bm.verts]
    for child in children_all:core.plan_face(points,tuple(v.index for v in child.verts),child.index,a._get_zone(child,bm),caps=a._read_caps(child,a._boundary_layer(bm)))
    if select_result:
        bm.select_history.clear()
        for face in bm.faces:face.select_set(False)
        chosen.select_set(True);bm.faces.active=chosen;bm.select_history.add(chosen)
    return answer
