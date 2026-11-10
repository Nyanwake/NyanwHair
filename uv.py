"""Flow-aligned UV islands, built only for an explicitly baked mesh.

Charts retain start-to-end V orientation. Smooth compatible charts may join
by translation and uniform scale; packing never rotates their flow direction.
"""
from .i18n import tr
import math,heapq
from collections import defaultdict
import numpy as np
from mathutils import Vector
from mathutils.kdtree import KDTree
from mathutils.geometry import box_pack_2d
from . import core


def _arc(points):
    lengths=[0.]
    for a,b in zip(points,points[1:]):lengths.append(lengths[-1]+core.length(core.sub(a,b)))
    return [v/max(lengths[-1],1e-12) for v in lengths]


def _coordinates(mesh,owner,lookup_data):
    grid=mesh.surface_grids.get(owner,());columns=mesh.surface_columns[owner];samples=defaultdict(list)
    if grid:
        sections=mesh.surface_sections[owner]
        centers=[core.mul(core.add(row[0],row[-1]),.5) for row in sections]
        flows=_arc(centers) if len(centers)==len(grid) else [i/max(1,len(grid)-1) for i in range(len(grid))]
        for j,row in enumerate(grid):
            for x,index in enumerate(row):
                if index is not None:samples[index].append((x/max(1,len(row)-1),flows[j]))
    for x,column in enumerate(columns):
        if not column:continue
        flows=_arc([mesh.vertices[i] for i in column])
        for index,t in zip(column,flows):
            if x in (0,len(columns)-1):samples[index]=[(x/max(1,len(columns)-1),t)]
            elif not grid:samples[index].append((x/max(1,len(columns)-1),t))
    uv={i:(sum(p[0] for p in values)/len(values),sum(p[1] for p in values)/len(values)) for i,values in samples.items()}
    vertex_index,cap_index,plans=lookup_data;plan=plans[owner]
    for end,cap in enumerate((plan.start,plan.end)):
        if cap[0]=='point':
            vertex=vertex_index['v',plan.boundary[cap[1]]];uv[vertex]=(.5,float(end))
        else:
            edge=core.edge_key(plan.boundary[cap[1]],plan.boundary[(cap[1]+1)%len(plan.boundary)])
            first=plan.left[-1 if end else 0]
            for i,key in cap_index.get(edge,()):
                u=key[3]/key[4];uv[i]=(u if first==edge[0] else 1-u,float(end))
    # UV dictionaries are per owner: welded geometry may have different UVs
    # on either side of a deliberate seam.
    return {mesh.vertex_keys[i]:(u,1-v) for i,(u,v) in uv.items()}


def _maps(mesh):
    caps=defaultdict(list)
    for i,key in enumerate(mesh.vertex_keys):
        if key[0]=='cap':caps[key[1:3]].append((i,key))
    index=({key:i for i,key in enumerate(mesh.vertex_keys)},caps,{p.index:p for p in mesh.plans_by_loop.values()})
    return {owner:_coordinates(mesh,owner,index) for owner in set(mesh.owners)}


def _unmirror(key):
    while key[0]=='mirror':key=key[2]
    return key


def _key(key):
    mask=0
    while key[0] in ('mirror','back','shell'):
        if key[0]=='mirror':mask|=key[1];key=key[2]
        else:key=key[1]
    return key,mask


def _area(points):
    return .5*sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points,points[1:]+points[:1]))


def _triangles_overlap(a,b,tolerance):
    # Strict separating-axis test: touching seams and endpoints are allowed.
    for triangle in (a,b):
        for p,q in zip(triangle,np.roll(triangle,-1,axis=0)):
            edge=q-p;axis=np.array((-edge[1],edge[0]));size=np.linalg.norm(axis)
            if size<1e-14:continue
            aa=a@axis;bb=b@axis
            if min(aa.max(),bb.max())-max(aa.min(),bb.min())<=tolerance*size:return False
    return True


def _overlap(one,two,coords,triangles):
    aa=np.array([[coords[v] for v in tri] for f in one for tri in triangles[f]])
    bb=np.array([[coords[v] for v in tri] for f in two for tri in triangles[f]])
    if not len(aa) or not len(bb):return False
    lo_a=aa.min(axis=(0,1));hi_a=aa.max(axis=(0,1));lo_b=bb.min(axis=(0,1));hi_b=bb.max(axis=(0,1))
    extent=max(1e-5,float(np.max(hi_a-lo_a)),float(np.max(hi_b-lo_b)));epsilon=extent*1e-8
    if np.any(np.minimum(hi_a,hi_b)-np.maximum(lo_a,lo_b)<=epsilon):return False
    # A 3D triangle BVH does not reliably report coplanar containment. Use
    # centroid bounds for broad phase and a strict 2D SAT for the actual test.
    radii_a=np.linalg.norm(aa.max(axis=1)-aa.min(axis=1),axis=1)*.5
    radii_b=np.linalg.norm(bb.max(axis=1)-bb.min(axis=1),axis=1)*.5
    if radii_a.max()<radii_b.max():aa,bb=bb,aa;radii_a,radii_b=radii_b,radii_a
    lows=bb.min(axis=1);highs=bb.max(axis=1);centers=(lows+highs)*.5
    tree=KDTree(len(bb))
    for i,p in enumerate(centers):tree.insert((float(p[0]),float(p[1]),0.),i)
    tree.balance();radius=float(radii_b.max())
    for triangle,r in zip(aa,radii_a):
        lo=triangle.min(axis=0);hi=triangle.max(axis=0);center=(lo+hi)*.5
        for _,j,_ in tree.find_range((float(center[0]),float(center[1]),0.),float(r)+radius+epsilon):
            if np.any(np.minimum(hi,highs[j])-np.maximum(lo,lows[j])<=epsilon):continue
            if _triangles_overlap(triangle,bb[j],epsilon):return True
    return False


def _shape_score(uv,tri,world):
    p,q,r=(np.asarray(world[v],dtype=float) for v in tri);e=q-p;f=r-p;size=np.linalg.norm(e)
    if size<1e-12:return 1.
    x=np.dot(e,f)/size;y=math.sqrt(max(0.,float(np.dot(f,f)-x*x)))
    if y<1e-12:return 1.
    matrix=np.column_stack((uv[tri[1]]-uv[tri[0]],uv[tri[2]]-uv[tri[0]]))@np.array(((1/size,-x/(size*y)),(0.,1/y)))
    values=np.linalg.svd(matrix,compute_uv=False)
    return float(values[0]/max(1e-12,values[-1]))


def _valid_chart(coords,component,triangles):
    return all(_area([tuple(coords[v]) for v in tri])>1e-14 for f in component for tri in triangles[f])


def _relax_chart(coords,component,triangles):
    """Repair only folded charts with a positive-weight disk parameterization."""
    incidence=defaultdict(int);graph=defaultdict(set)
    for f in component:
        for tri in triangles[f]:
            for a,b in zip(tri,tri[1:]+tri[:1]):
                incidence[core.edge_key(a,b)]+=1;graph[a].add(b);graph[b].add(a)
    border={v for edge,count in incidence.items() if count==1 for v in edge}
    if len(border)<3:return None
    interior=sorted(set(graph)-border);ids={v:i for i,v in enumerate(interior)}
    rows=[];columns=[];fixed=[]
    for a in interior:
        for b in graph[a]:
            if b in ids:rows.append(ids[a]);columns.append(ids[b])
            else:fixed.append((ids[a],b))
    rows=np.asarray(rows,dtype=np.intp);columns=np.asarray(columns,dtype=np.intp)
    diagonal=np.asarray([len(graph[v]) for v in interior],dtype=float)
    def solve(boundary):
        out={v:p.copy() for v,p in coords.items()};out.update(boundary)
        if not interior:return out
        rhs=np.zeros((len(interior),2))
        for i,v in fixed:rhs[i]+=boundary[v]
        def product(x):
            return diagonal[:,None]*x-np.stack([np.bincount(rows,weights=x[columns,axis],minlength=len(interior)) for axis in range(2)],axis=1)
        x=np.asarray([out[v] for v in interior]);r=rhs-product(x);z=r/diagonal[:,None];direction=z.copy();energy=np.sum(r*z,axis=0)
        for _ in range(min(2000,max(32,4*len(interior)))):
            if np.max(np.abs(r))<1e-12:break
            applied=product(direction);denom=np.sum(direction*applied,axis=0)
            alpha=np.divide(energy,denom,out=np.zeros(2),where=np.abs(denom)>1e-30)
            x+=direction*alpha;r-=applied*alpha;z=r/diagonal[:,None];new_energy=np.sum(r*z,axis=0)
            beta=np.divide(new_energy,energy,out=np.zeros(2),where=np.abs(energy)>1e-30)
            direction=z+direction*beta;energy=new_energy
        out.update((v,x[i]) for v,i in ids.items())
        return out
    boundary={v:coords[v].copy() for v in border}
    trial=solve(boundary)
    if _valid_chart(trial,component,triangles):return trial
    # Collinear boundary-only triangles need a slightly rounded outline.
    # Start with a tiny adjustment; a convex ellipse is the final disk fallback.
    points=np.asarray(list(boundary.values()));center=points.mean(axis=0);radii=np.maximum(1e-8,np.ptp(points,axis=0)*.5)
    delta=(points-center)/radii;lengths=np.linalg.norm(delta,axis=1)
    if np.any(lengths<1e-12):return None
    rounded=center+delta/lengths[:,None]*radii
    for strength in (.01,.1,1.):
        trial=solve({v:(1.-strength)*p+strength*q for (v,p),q in zip(boundary.items(),rounded)})
        if _valid_chart(trial,component,triangles):return trial
    return None


def generate(surfaces,matrix,angle=math.radians(60.),margin=.008,mirror_uv=True):
    """Return face-corner UVs and face-island IDs, in bake traversal order."""
    faces=[];world=[];charts=[];initial=[];kinds=[]
    full_faces=[];full_keys=[];active_faces=[];replicas={}
    for gid,surface in surfaces:
        offset=len(world);world.extend(tuple(matrix@Vector(p)) for p in surface.vertices)
        front=getattr(surface,'front_result',surface);back=getattr(surface,'back_result',None)
        maps={('front',owner):values for owner,values in _maps(front).items()}
        if back is not None:maps.update((('back',owner),values) for owner,values in _maps(back).items())
        keys=[_key(k) for k in surface.vertex_keys];side_labels=getattr(surface,'surface_sides',None)
        originals={};replica_faces=getattr(surface,'replica_faces',set());face_offset=len(full_faces)
        def signature(face,owner,side):return owner,side,tuple(sorted(_unmirror(surface.vertex_keys[v]) for v in face))
        if mirror_uv:
            for local,(face,owner) in enumerate(zip(surface.faces,surface.owners)):
                if local not in replica_faces:originals[signature(face,owner,side_labels[local] if side_labels else 'front')]=face_offset+local
        for local,(face,owner) in enumerate(zip(surface.faces,surface.owners)):
            side=side_labels[local] if side_labels is not None else 'front';mask=0
            full=len(full_faces);full_faces.append(tuple(offset+v for v in face));full_keys.append([_unmirror(surface.vertex_keys[v]) for v in face])
            if mirror_uv and local in replica_faces:
                replicas[full]=originals[signature(face,owner,side)];continue
            for v in face:mask|=keys[v][1]
            coords={};lookup=maps.get((side,owner),{})
            if side=='rim':
                plan=next(p for p in front.plans_by_loop.values() if p.index==owner)
                cap_edge=next((keys[v][0][1:3] for v in face if keys[v][0][0]=='cap'),None)
                if cap_edge is None:
                    cap_edge=next((core.edge_key(plan.boundary[i],plan.boundary[(i+1)%len(plan.boundary)]) for kind,i in (plan.start,plan.end) if kind=='edge' and all(keys[v][0][0]=='v' and keys[v][0][1] in (plan.boundary[i],plan.boundary[(i+1)%len(plan.boundary)]) for v in face)),None)
                if cap_edge is None:raise ValueError(tr('UV: reverse-side boundary not found'))
                start_cap=plan.start[0]=='edge' and core.edge_key(plan.boundary[plan.start[1]],plan.boundary[(plan.start[1]+1)%len(plan.boundary)])==cap_edge
                first=plan.left[0 if start_cap else -1]
                # Welded corners lie halfway across the rim's lens-shaped UV.
                for v in face:
                    raw,_=keys[v]
                    u=raw[3]/raw[4] if raw[0]=='cap' else float(raw[1]==cap_edge[1])
                    if first!=cap_edge[0]:u=1-u
                    original=surface.vertex_keys[v]
                    while original[0]=='mirror':original=original[2]
                    depth=1. if original[0]=='back' else .5 if raw[0]=='v' else 0.
                    coords[offset+v]=(u,depth)
            else:
                for v in face:
                    raw,_=keys[v]
                    if raw not in lookup:raise ValueError(tr('UV: missing guide coordinate for segment %d')%(owner+1))
                    coords[offset+v]=lookup[raw]
            faces.append(tuple(offset+v for v in face));initial.append(coords);kinds.append((gid,owner,side,mask))
            active_faces.append(full)

    from .topology import _triangulate
    triangles=[tuple(_triangulate(world,face)) for face in faces]
    normals=[];flows=[];metrics=[]
    for fi,face in enumerate(faces):
        normals.append(core.norm(core.newell(world,face)));gradient=(0.,0.,0.);total=0.;height_sum=0.
        for a,b,c in triangles[fi]:
            uv=initial[fi];du1=uv[b][0]-uv[a][0];dv1=uv[b][1]-uv[a][1];du2=uv[c][0]-uv[a][0];dv2=uv[c][1]-uv[a][1];det=du1*dv2-du2*dv1
            if abs(det)>1e-12:
                dv=core.mul(core.sub(core.mul(core.sub(world[c],world[a]),du1),core.mul(core.sub(world[b],world[a]),du2)),1/det)
                weight=abs(det)*.5;gradient=core.add(gradient,core.mul(dv,weight));total+=weight;height_sum+=core.length(dv)*weight
        flows.append(core.norm(gradient));metrics.append((height_sum,total))
    edges=defaultdict(list)
    for fi,face in enumerate(faces):
        for a,b in zip(face,face[1:]+face[:1]):edges[core.edge_key(a,b)].append(fi)
    neighbors=defaultdict(set)
    for linked in edges.values():
        if len(linked)==2 and kinds[linked[0]]==kinds[linked[1]]:
            a,b=linked;neighbors[a].add(b);neighbors[b].add(a)
    pending=set(range(len(faces)));face_chart={}
    while pending:
        seed=min(pending);pending.remove(seed);component={seed};queue=[seed]
        for f in queue:
            for other in neighbors[f]&pending:pending.remove(other);component.add(other);queue.append(other)
        coords={v:np.asarray(p,dtype=float) for f in component for v,p in initial[f].items()}
        area=sum(_area([tuple(coords[v]) for v in faces[f]]) for f in component)
        if area<0:
            for p in coords.values():p[0]=1-p[0]
        if not _valid_chart(coords,component,triangles):
            repaired=_relax_chart(coords,component,triangles)
            if repaired is None:raise ValueError(tr('UV: the source segment contains a flipped face'))
            coords=repaired
        # Match physical area and longitudinal arc scale before joining.
        physical_area=sum(core.length(core.cross(core.sub(world[b],world[a]),core.sub(world[c],world[a])))*.5 for f in component for a,b,c in triangles[f])
        uv_area=max(1e-12,sum(abs(_area([tuple(coords[v]) for v in faces[f]])) for f in component))
        height_sum=sum(metrics[f][0] for f in component);weights=sum(metrics[f][1] for f in component)
        height=height_sum/weights if weights>1e-12 else math.sqrt(max(1e-12,physical_area))
        height=max(height,1e-8)
        width=max(1e-8,physical_area/max(1e-12,uv_area*height))
        for p in coords.values():p*=np.array((width,height))
        cid=len(charts);charts.append({'faces':component,'uv':coords,'scales':(1.,1.),'kind':kinds[seed],'members':{cid}})
        for f in component:face_chart[f]=cid

    candidates=defaultdict(list);cosine=math.cos(angle)
    for edge,linked in edges.items():
        if len(linked)!=2:continue
        a,b=linked;ca,cb=face_chart[a],face_chart[b]
        if ca==cb:continue
        ka,kb=charts[ca]['kind'],charts[cb]['kind']
        if ka[0]!=kb[0] or ka[2:]!=kb[2:] or ka[2]=='rim':continue
        score=min(core.dot(normals[a],normals[b]),core.dot(flows[a],flows[b]))
        candidates[core.edge_key(ca,cb)].append(score)
    # Whole shared runs must be smooth, not just one favorable sampled edge.
    ordered=sorted(((min(scores),pair) for pair,scores in candidates.items() if min(scores)>=cosine),reverse=True)
    blocked=defaultdict(set)
    for (a,b),scores in candidates.items():
        if min(scores)<cosine:blocked[a].add(b);blocked[b].add(a)
    parent=list(range(len(charts)))
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    merged=0;rejected=defaultdict(int)
    queue=[(-round(score,5),len(charts[a]['faces'])+len(charts[b]['faces']),a,b) for score,(a,b) in ordered];heapq.heapify(queue)
    while queue:
        priority,cost,old_a,old_b=heapq.heappop(queue);a,b=find(old_a),find(old_b)
        if a==b:continue
        actual_cost=len(charts[a]['faces'])+len(charts[b]['faces'])
        if actual_cost!=cost:
            heapq.heappush(queue,(priority,actual_cost,a,b));continue
        one,two=charts[a],charts[b];shared=sorted(one['uv'].keys()&two['uv'].keys())
        if any(blocked[i]&two['members'] for i in one['members']):rejected['sharp_cycle']+=1;continue
        if len(shared)<2:continue
        pa=np.array([one['uv'][v] for v in shared]);pb=np.array([two['uv'][v] for v in shared]);ma=pa.mean(axis=0);mb=pb.mean(axis=0)
        centered=pb-mb;denom=float(np.sum(centered*centered))
        if denom<1e-14:continue
        scale=float(np.sum((pa-ma)*centered)/denom)
        if not .8<=scale<=1.25:rejected['scale']+=1;continue
        lo=min(one['scales'][0],two['scales'][0]*scale);hi=max(one['scales'][1],two['scales'][1]*scale)
        if hi/max(lo,1e-12)>1.35:rejected['density']+=1;continue
        delta=ma-scale*mb;error=float(np.max(np.linalg.norm(pa-(pb*scale+delta),axis=1)));span=max(1e-8,float(np.max(np.linalg.norm(pa-ma,axis=1))))
        if error>max(1e-8,span*.005):rejected['alignment']+=1;continue
        trial={**one['uv'],**{v:p*scale+delta for v,p in two['uv'].items()}}
        for v in shared:trial[v]=(one['uv'][v]+two['uv'][v]*scale+delta)*.5
        valid=True
        for chart in (one,two):
            for f in chart['faces']:
                for tri in triangles[f]:
                    if _area([tuple(trial[v]) for v in tri])<=1e-14:valid=False;break
                    if any(v in shared for v in tri) and _shape_score(trial,tri,world)>1.25*_shape_score(chart['uv'],tri,world):valid=False;break
                if not valid:break
            if not valid:break
        if not valid:rejected['distortion']+=1;continue
        if _overlap(one['faces'],two['faces'],trial,triangles):rejected['overlap']+=1;continue
        one['uv']=trial;one['faces']|=two['faces'];one['scales']=(lo,hi);one['members']|=two['members'];parent[b]=a;merged+=1

    islands=[chart for i,chart in enumerate(charts) if find(i)==i];bounds=[]
    for chart in islands:
        points=np.array(list(chart['uv'].values()));bounds.append((points.min(axis=0),points.max(axis=0)))
    gap=0.;boxes=[];extent=1.
    for _ in range(4):
        boxes=[[0.,0.,float(hi[0]-lo[0])+2*gap,float(hi[1]-lo[1])+2*gap] for lo,hi in bounds]
        w,h=box_pack_2d(boxes);extent=max(w,h,1e-8);gap=extent*margin
    uv_faces=[None]*len(faces);face_islands=[None]*len(faces)
    for island,(chart,(lo,hi),box) in enumerate(zip(islands,bounds,boxes)):
        # Use the actual packed padding, not the next iteration's estimate.
        offset=np.array((box[0],box[1]))+(box[2]-(hi[0]-lo[0]))*.5
        for f in chart['faces']:
            uv_faces[f]=[tuple((chart['uv'][v]-lo+offset)/extent) for v in faces[f]];face_islands[f]=island
    if any(p is None for p in uv_faces):raise ValueError(tr('UV: incomplete unwrap'))
    for face in uv_faces:
        if not all(math.isfinite(t) and -1e-6<=t<=1.+1e-6 for p in face for t in p):raise ValueError(tr('UV: coordinates outside the atlas'))
        if _area(face)<=1e-14:raise ValueError(tr('UV: collapsed or flipped face'))
    complete=[None]*len(full_faces);complete_islands=[None]*len(full_faces)
    for active,full in enumerate(active_faces):complete[full]=uv_faces[active];complete_islands[full]=face_islands[active]
    for copied,original in replicas.items():
        lookup=dict(zip(full_keys[original],complete[original]))
        complete[copied]=[lookup[key] for key in full_keys[copied]];complete_islands[copied]=complete_islands[original]
        if abs(_area(complete[copied]))<=1e-14:raise ValueError(tr('UV: collapsed mirrored face'))
    return complete,complete_islands,{'charts':len(charts),'islands':len(islands),'merged':merged,'mirror_faces':len(replicas),'candidates':len(candidates),'smooth_candidates':len(ordered),'rejected':dict(rejected)}


def apply(mesh,uv_faces,face_islands):
    if len(uv_faces)!=len(mesh.polygons) or len(face_islands)!=len(mesh.polygons) or any(len(points)!=polygon.loop_total for polygon,points in zip(mesh.polygons,uv_faces)):
        raise ValueError(tr('UV coordinates do not match Mesh polygons'))
    layer=mesh.uv_layers.new(name='UVMap')
    for face,points in zip(mesh.polygons,uv_faces):
        for loop,point in zip(face.loop_indices,points):layer.data[loop].uv=point
    linked=defaultdict(list)
    for face,island in zip(mesh.polygons,face_islands):
        for loop in face.loop_indices:linked[mesh.loops[loop].edge_index].append(island)
    for edge in mesh.edges:
        owners=linked[edge.index];edge.use_seam=len(owners)!=2 or owners[0]!=owners[1]
