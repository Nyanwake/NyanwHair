"""NyanwHair 0.35.20: independent body widths and shared boundary contracts.

Pure Python, no Blender IDs or tags on vertices/edges. Blender face corners may
store the user's chosen end caps; this module only consumes that choice.

Invariants:
- Source face defines ownership; two rail paths define continuous shape.
- Shared physical rail edges use a single evaluator and a single row schedule.
- Individual guide control vertices are not forced to create output mesh rows.
- Incompatible station counts produce local triangle transitions, not missing zones.
- Geometry sampling never changes logical rail paths or source topology.
"""
from __future__ import annotations
import bisect
import math
from dataclasses import dataclass, field, replace
from collections import defaultdict

EPS = 1e-10
CURVE_SUBDIVISIONS = 48

def add(a,b): return (a[0]+b[0],a[1]+b[1],a[2]+b[2])
def sub(a,b): return (a[0]-b[0],a[1]-b[1],a[2]-b[2])
def mul(a,s): return (a[0]*s,a[1]*s,a[2]*s)
def dot(a,b): return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]
def cross(a,b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def length(a): return math.sqrt(dot(a,a))
def norm(a):
    v=length(a)
    return mul(a,1.0/v) if v>EPS else (0.0,0.0,0.0)
def mix(a,b,t): return add(mul(a,1.0-t),mul(b,t))
def edge_key(a,b): return (a,b) if a<b else (b,a)

@dataclass(frozen=True)
class ZoneSettings:
    width: int=2
    bulge: float=0.08
    bias: float=0.0
    flip: bool=False
    order: int=0

@dataclass(frozen=True)
class FacePlan:
    index: int
    boundary: tuple[int,...]
    left: tuple[int,...]
    right: tuple[int,...]
    start: tuple[str,int]
    end: tuple[str,int]
    normal: tuple[float,float,float]
    settings: ZoneSettings

@dataclass
class MeshResult:
    vertices: list=field(default_factory=list)
    faces: list=field(default_factory=list)
    owners: list=field(default_factory=list)
    warnings: dict=field(default_factory=dict)
    face_paths: dict=field(default_factory=dict)
    guide_curves: dict=field(default_factory=dict)
    # Longitudinal lines of the ACTUAL generated mesh, indexed by face and
    # normalized width.  Hair Loop only picks these lines, never arbitrary
    # screen positions or the transverse edges of a quad.
    cut_lines: dict=field(default_factory=dict)
    effective_widths: dict=field(default_factory=dict)
    cap_widths: dict=field(default_factory=dict)
    width_transitions: dict=field(default_factory=dict)
    samples_per_edge: dict=field(default_factory=dict)
    plans_by_loop: dict=field(default_factory=dict)
    # Lightweight diagnostics for explicit transitions.
    transitions: dict=field(default_factory=dict)
    orientation_by_face: dict=field(default_factory=dict)
    base_rows: int=0
    reference_length: float=0.
    target_spacing: float=0.
    surface_grids: dict=field(default_factory=dict)
    surface_sections: dict=field(default_factory=dict)
    rail_samples: dict=field(default_factory=dict)
    vertex_keys: list=field(default_factory=list)
    directional_smoothing: dict=field(default_factory=dict)
    local_density: dict=field(default_factory=dict)
    point_density: dict=field(default_factory=dict)
    layout_lengths: dict=field(default_factory=dict)
    surface_columns: dict=field(default_factory=dict)
    sampling_cache: dict=field(default_factory=dict,repr=False)
    correspondence_cache: dict=field(default_factory=dict,repr=False)
    patch_cache: dict=field(default_factory=dict,repr=False)
    reused_patches: int=0


def _plan_priority(plan):
    # Positive creation IDs survive save/load, face reordering and Undo.
    return (0,plan.settings.order,plan.index) if plan.settings.order>0 else (1,plan.index,0)


def _boundary_key(boundary):
    """Face identity for deterministic decisions, independent of face order."""
    loop=tuple(boundary)
    return min(seq[i:]+seq[:i] for seq in (loop,loop[::-1]) for i in range(len(seq)))


def _cap_profiles(points,plans,widths,evaluate,cap_widths=None):
    """One geometric definition per cross-cap, shared by ALL its owners.

    Equal-weight mean signed bulge/bias is symmetric. The local reference
    normals come from the source curves, independent of output row density.
    """
    groups=defaultdict(list)
    for p in plans:
        for end,cap in enumerate((p.start,p.end)):
            if cap[0]!='edge': continue
            i=cap[1]
            key=edge_key(p.boundary[i],p.boundary[(i+1)%len(p.boundary)])
            tangents=[]
            for rail in (p.left,p.right):
                if end:
                    tangents.append(sub(points[rail[-1]],evaluate(rail[-2],rail[-1],.999)))
                else:
                    tangents.append(sub(evaluate(rail[0],rail[1],.001),points[rail[0]]))
            across=sub(points[p.right[-1 if end else 0]],points[p.left[-1 if end else 0]])
            normal=norm(cross(across,add(*tangents)))
            if length(normal)<EPS: normal=p.normal
            if dot(normal,p.normal)<0: normal=mul(normal,-1.)
            groups[key].append((p,normal))
    profiles={}
    for (lo,hi),group in groups.items():
        group.sort(key=lambda item:_boundary_key(item[0].boundary))
        n=norm(tuple(math.fsum(item[1][axis] for item in group) for axis in range(3)))
        if length(n)<EPS: n=group[0][1]
        bias=math.fsum(p.settings.bias for p,_ in group)/len(group)
        bulge=math.fsum(p.settings.bulge*(-1 if p.settings.flip else 1) for p,_ in group)/len(group)
        w=(cap_widths or {}).get((lo,hi),widths[group[0][0].index])
        size=length(sub(points[hi],points[lo]))
        positions=[]
        for j in range(w+1):
            v=bias_cross_position(j/w,bias)
            positions.append(add(mix(points[lo],points[hi],v),mul(n,bulge*size*math.sin(math.pi*v))))
        positions[0]=points[lo]; positions[-1]=points[hi]
        profiles[(lo,hi)]=positions
    return profiles


def newell(points,loop):
    n=(0.,0.,0.)
    for i,a in enumerate(loop):
        p,q=points[a],points[loop[(i+1)%len(loop)]]
        n=add(n, ((p[1]-q[1])*(p[2]+q[2]),
                  (p[2]-q[2])*(p[0]+q[0]),
                  (p[0]-q[0])*(p[1]+q[1])))
    return norm(n)


def cap_paths(loop,c0,c1):
    """Find two edge-disjoint rails between two logical end caps.

    Caps reference face CORNERS, not persistent BMVert/BMEdge identifiers.
    """
    n=len(loop)
    if any(c is None or c[0] not in ('edge','point') or not 0<=c[1]<n for c in (c0,c1)):return None
    blocked={c[1] for c in (c0,c1) if c[0]=='edge'}
    ends=lambda c: (c[1],) if c[0]=='point' else (c[1],(c[1]+1)%n)
    possibilities=[]
    for start in ends(c0):
        for finish in ends(c1):
            for step in (1,-1):
                p=[start]; used=set(); k=start
                while k!=finish and len(p)<=n:
                    nxt=(k+step)%n
                    edge_i=k if step==1 else nxt
                    if edge_i in blocked: break
                    used.add(edge_i); p.append(nxt); k=nxt
                if k==finish and len(p)>=2:
                    possibilities.append((tuple(loop[i] for i in p),used))
    required=set(range(n))-blocked
    for i,(a,ea) in enumerate(possibilities):
        for b,eb in possibilities[i+1:]:
            if ea.isdisjoint(eb) and ea|eb==required:
                if a[0]==b[0] and c0[0]=='edge': continue
                if a[-1]==b[-1] and c1[0]=='edge': continue
                if a!=b and len(a)>=2 and len(b)>=2:
                    return (a,b) if a<b else (b,a)
    return None


def major_axis(points,loop):
    center=mul(tuple(map(sum,zip(*(points[k] for k in loop)))),1/len(loop))
    xs=[sub(points[k],center) for k in loop]
    best=max(((dot(sub(points[a],points[b]),sub(points[a],points[b])),a,b)
              for i,a in enumerate(loop) for b in loop[i+1:]),default=None)
    if not best or best[0]<EPS: raise ValueError('Все точки зоны совпадают')
    direction=norm(sub(points[best[1]],points[best[2]]))
    for _ in range(12):
        v=(0.,0.,0.)
        for x in xs: v=add(v,mul(x,dot(x,direction)))
        if length(v)<EPS: break
        direction=norm(v)
    return direction


def auto_caps(points,loop):
    """One-time fallback. Cache or persist this choice, never rerun during drag."""
    n=len(loop)
    if n==3:
        edge=min(range(3),key=lambda i:length(sub(points[loop[i]],points[loop[(i+1)%3]])))
        return ('edge',edge),('point',(edge+2)%3)
    axis=major_axis(points,loop)
    ts=[dot(points[k],axis) for k in loop]
    lo,hi=min(ts),max(ts)
    extent=hi-lo
    if extent<EPS: raise ValueError('Не удалось определить ось локона')
    options=[]
    for end in (lo,hi):
        choices=[]
        for i in range(n):
            j=(i+1)%n
            d=abs((ts[i]+ts[j])*0.5-end)/extent
            if d<0.30 and abs(ts[i]-ts[j])/extent<0.24:
                choices.append((('edge',i),d*2.3+0.8*length(sub(points[loop[i]],points[loop[j]]))/extent))
            d=abs(ts[i]-end)/extent
            if d<0.24:
                prev=norm(sub(points[loop[(i-1)%n]],points[loop[i]]))
                nxt=norm(sub(points[loop[j]],points[loop[i]]))
                angle=math.acos(max(-1.,min(1.,dot(prev,nxt))))
                choices.append((('point',i),d*2.3+0.34+max(0,min(1,(angle-.5)/1.7))*.85))
        options.append(choices)
    best=None
    for a,sa in options[0]:
        for b,sb in options[1]:
            paths=cap_paths(loop,a,b)
            if not paths: continue
            left,right=paths
            len_l=sum(length(sub(points[x],points[y])) for x,y in zip(left,left[1:]))
            len_r=sum(length(sub(points[x],points[y])) for x,y in zip(right,right[1:]))
            if min(len_l,len_r)<EPS: continue
            score=sa+sb+.2*abs(len_l-len_r)/max(len_l,len_r)
            if best is None or score<best[0]: best=(score,a,b)
    if best is None: raise ValueError('Не найдены две рабочие кривые; укажи торцы зоны')
    return best[1],best[2]


def neutral_caps(boundary,caps):
    """Choose an internal parameter direction independent of UI slot order."""
    if caps is None or len(caps)!=2 or any(c is None or c[0] not in ('point','edge') for c in caps):return caps
    def key(cap):
        i=cap[1]
        vertices=(boundary[i],) if cap[0]=='point' else (boundary[i],boundary[(i+1)%len(boundary)])
        return tuple(sorted(vertices))
    return tuple(sorted(caps,key=key))


def plan_face(points,boundary,index,settings,hint=None,caps=None):
    loop=tuple(boundary)
    if len(loop)<3 or len(set(loop))!=len(loop):
        raise ValueError('Некорректный контур управляющей грани')
    normal=newell(points,loop)
    if length(normal)<EPS: raise ValueError('Площадь управляющей грани равна нулю')
    if caps is not None:
        if len(caps)!=2 or any(c is None for c in caps):raise ValueError('Укажи недостающий край сегмента')
        if any(c[0] not in ('point','edge') for c in caps):raise ValueError('Край сегмента требует уточнения после изменения каркаса')
        c0,c1=caps
        paths=cap_paths(loop,c0,c1)
        if not paths: raise ValueError('Сохранённые торцы больше не образуют две кривые')
    elif hint is not None and hint.boundary==loop:
        c0,c1=hint.start,hint.end
        paths=cap_paths(loop,c0,c1)
        if not paths: raise ValueError('Нарушено соединение двух кривых')
    else:
        c0,c1=auto_caps(points,loop)
        paths=cap_paths(loop,c0,c1)
    if not paths: raise ValueError('У управляющей грани нет двух непрерывных сторон')
    return FacePlan(index,loop,*paths,c0,c1,normal,settings)


def cap_location(points, loop, cap):
    """Position of a face cap, derived without any vertex/edge metadata."""
    kind, i = cap
    if kind == 'point':
        return points[loop[i]]
    return mix(points[loop[i]], points[loop[(i+1) % len(loop)]], .5)


def recover_caps(previous, old_points, points, boundary):
    """Preserve the two end caps across a small native Blender edit.

    An inserted guide control vertex is not an instruction to rotate a face's
    working rails.  Match the *old cap positions* against the *new face
    corners*, allowing an edge cap to become a point cap after Merge At Center.
    This is a fallback when BMLoop markers were lost, not an ownership tag on
    control vertices.  It never changes Blender geometry or its custom data.
    """
    loop = tuple(boundary)
    if len(loop) < 3 or previous is None:
        return None
    old_loop = previous.boundary
    try:
        targets = [cap_location(old_points, old_loop, c)
                   for c in (previous.start, previous.end)]
    except (IndexError, TypeError):
        return None
    sizes = [length(sub(points[loop[i]], points[loop[(i+1) % len(loop)]]))
             for i in range(len(loop))]
    # Prefer a surviving old cap edge, but only weakly: after merging the two
    # cap endpoints a single point is exactly the correct new cap.
    candidates = ([('point', i) for i in range(len(loop))] +
                  [('edge', i) for i in range(len(loop)) if sizes[i] > EPS])
    old_scale = max(EPS, max(length(sub(old_points[u], old_points[v]))
                             for u in old_loop for v in old_loop))
    choices = []
    for target, old_cap in zip(targets, (previous.start, previous.end)):
        ranking = []
        for cap in candidates:
            distance = length(sub(cap_location(points, loop, cap), target))
            penalty = .012 * old_scale if cap[0] != old_cap[0] else 0.
            ranking.append((distance + penalty, cap))
        choices.append(sorted(ranking, key=lambda x: (x[0], x[1])))
    best = None
    for sa, a in choices[0]:
        for sb, b in choices[1]:
            if a == b or cap_paths(loop, a, b) is None:
                continue
            score = sa + sb
            if best is None or score < best[0]:
                best = score, (a, b)
    return best[1] if best else None


def physical_edges(path): return [edge_key(a,b) for a,b in zip(path,path[1:])]


def make_edge_evaluator(points,plans,directional_smoothing=None):
    from .curves import CurveNetwork
    return CurveNetwork(points,plans,directional_smoothing)


def sample_smooth(points,path,count,evaluate,ident):
    """Uniform arc-length sampling of a complete guide, not of source edges."""
    count=int(count)
    samples=[(0.,points[path[0]],path[0],path[1],0.)]
    distance=0.
    prev=points[path[0]]
    for a,b in zip(path,path[1:]):
        for k in range(1,13):
            t=k/12.
            p=evaluate(a,b,t)
            distance+=length(sub(p,prev))
            samples.append((distance,p,a,b,t))
            prev=p
    if distance<EPS: raise ValueError('Нулевая длина рабочей кривой')
    distances=[p[0] for p in samples]
    output=[]
    for i in range(count+1):
        if i==0: output.append((('v',path[0]),points[path[0]])); continue
        if i==count: output.append((('v',path[-1]),points[path[-1]])); continue
        target=distance*i/count
        k=min(max(1,bisect.bisect_left(distances,target)),len(samples)-1)
        a,b=samples[k-1],samples[k]
        blend=(target-a[0])/max(EPS,b[0]-a[0])
        if a[2:4]==b[2:4]:
            position=evaluate(b[2],b[3],a[4]*(1-blend)+b[4]*blend)
        else: position=mix(a[1],b[1],blend)
        output.append((('free',ident,i),position))
    return output



def bias_cross_position(u,bias):
    """Symmetric width redistribution: positive toward centre, negative outward.

    Endpoints and the midpoint are invariant.  Unlike asymmetric u**power,
    changing bias cannot move the strand's central guide sideways.
    """
    u=max(0.,min(1.,float(u)))
    exponent=2.0**max(-2.,min(2.,float(bias)))
    return .5+math.copysign(.5*(abs(2.*u-1.)**exponent),u-.5) if u!=.5 else .5


def plan_widths(plans,fixed=None,warnings=None):
    """Matching cross-cap width is necessary for manifold stacked zones."""
    parent={p.index:p.index for p in plans}
    def root(x):
        while parent[x]!=x:
            parent[x]=parent[parent[x]]; x=parent[x]
        return x
    def union(a,b): parent[root(a)]=root(b)
    owners=defaultdict(list)
    for p in plans:
        for cap in (p.start,p.end):
            if cap[0]=='edge':
                a=p.boundary[cap[1]]; b=p.boundary[(cap[1]+1)%len(p.boundary)]
                owners[edge_key(a,b)].append(p.index)
    for group in owners.values():
        for x in group[1:]: union(group[0],x)
    widths={}
    for p in plans:
        k=root(p.index)
        widths[k]=max(widths.get(k,1),max(1,min(32,int(p.settings.width))))
    constraints=defaultdict(set)
    for owner,value in (fixed or {}).items():constraints[root(owner)].add(value)
    for key,values in constraints.items():
        if len(values)>1:
            message='Два унаследованных края имеют разную ширину; нужен переход'
            if warnings is None:raise ValueError(message)
            for p in plans:
                if root(p.index)==key:warnings[p.index]=message
        else:widths[key]=next(iter(values))
    return {p.index:widths[root(p.index)] for p in plans}



def generate(points,boundaries,settings=None,rows=12,max_polygons=200000,
             previous=None,caps=None,reference_length=0.,directional_smoothing=None,local_density=None,point_density=None,layout_lengths=None,regular_rows=False):
    points=[tuple(map(float,p)) for p in points]
    settings=list(settings or [ZoneSettings() for _ in boundaries])
    rows=max(2,min(256,int(rows)))
    out=MeshResult()
    plans=[]
    for i,boundary in enumerate(boundaries):
        try:
            hint=previous.plans_by_loop.get(tuple(boundary)) if previous else None
            explicit=caps[i] if caps and i<len(caps) else None
            plan=plan_face(points,boundary,i,settings[i],hint=hint,caps=explicit)
            plans.append(plan)
            out.face_paths[i]=(plan.left,plan.right)
            out.plans_by_loop[plan.boundary]=plan
        except (ValueError,IndexError,TypeError) as exc:
            out.warnings[i]=str(exc)
    if not plans: return out
    face_owners=defaultdict(set)
    for p in plans:
        for i,u in enumerate(p.boundary):
            face_owners[edge_key(u,p.boundary[(i+1)%len(p.boundary)])].add(p.index)
    overloaded={x for e,own in face_owners.items() if len(own)>2 for x in own}
    for i in overloaded: out.warnings[i]='Одно ребро принадлежит трём и более зонам'
    plans=[p for p in plans if p.index not in overloaded]
    if not plans: return out
    # Orient by shared EDGE traversal, never by the angle between face normals.
    # Geometry can fold past 90 degrees without changing winding parity.
    by_id={p.index:p for p in plans}
    directions={}
    for p in plans:
        for a,b in zip(p.boundary,p.boundary[1:]+p.boundary[:1]):
            directions[(p.index,edge_key(a,b))]=(a,b)
    neighbors=defaultdict(list)
    for edge,owners in face_owners.items():
        owners=sorted(i for i in owners if i in by_id)
        if len(owners)==2:
            u,v=owners
            parity=-1 if directions[(u,edge)]==directions[(v,edge)] else 1
            neighbors[u].append((v,parity)); neighbors[v].append((u,parity))
    signs={}
    for first in sorted(by_id,key=lambda i:_plan_priority(by_id[i])):
        if first in signs: continue
        signs[first]=1
        queue=[first]
        for i in queue:
            for j,parity in sorted(neighbors[i],key=lambda it:_boundary_key(by_id[it[0]].boundary)):
                desired=signs[i]*parity
                if j in signs:
                    if signs[j]!=desired:
                        out.warnings[j]='Неориентируемое соединение зон'
                    continue
                signs[j]=desired
                queue.append(j)
    plans=[replace(p,normal=mul(p.normal,signs[p.index])) for p in plans]
    from .topology import RailRegistry
    widths={p.index:max(1,min(32,int(p.settings.width))) for p in plans}
    out.effective_widths=widths
    out.base_rows=rows
    out.directional_smoothing=dict(directional_smoothing or {})
    evaluate=make_edge_evaluator(points,plans,out.directional_smoothing)
    out.local_density=dict(local_density or {})
    out.point_density=dict(point_density or {})
    registry=RailRegistry(points,evaluate,rows,plans,reference_length,max_polygons,out.local_density,out.point_density,previous,layout_lengths)
    rail_edges={edge for p in plans for rail in (p.left,p.right) for edge in physical_edges(rail)}
    interfaces={};cap_widths={}
    for p in plans:
        for kind,i in (p.start,p.end):
            if kind!='edge':continue
            edge=edge_key(p.boundary[i],p.boundary[(i+1)%len(p.boundary)])
            cap_widths[edge]=max(cap_widths.get(edge,1),widths[p.index])
            if edge in rail_edges:interfaces[edge]=registry.boundary(registry.curve(edge))
    for edge,samples in interfaces.items():cap_widths[edge]=len(samples)-1
    out.cap_widths=cap_widths
    cap_profiles=_cap_profiles(points,plans,widths,evaluate,cap_widths)
    registry.cap_keys={}
    for edge,w in cap_widths.items():
        if edge in interfaces:
            registry.cap_keys[edge]=[key for key,pos in interfaces[edge]]
            cap_profiles[edge]=[pos for key,pos in interfaces[edge]]
        else:registry.cap_keys[edge]=[('v',edge[0])]+[('cap',*edge,x,w) for x in range(1,w)]+[('v',edge[1])]
    for p in plans:
        ends=[cap_widths[edge_key(p.boundary[i],p.boundary[(i+1)%len(p.boundary)])] if kind=='edge' else widths[p.index] for kind,i in (p.start,p.end)]
        out.width_transitions[p.index]=(ends[0],widths[p.index],ends[1])
    out.layout_lengths=dict(registry.layout_lengths)
    out.reference_length=registry.reference_length
    out.target_spacing=registry.spacing
    vertex_index={}
    produced=set()
    def put(key,pos):
        if key not in vertex_index:
            vertex_index[key]=len(out.vertices)
            out.vertices.append(pos)
        return vertex_index[key]
    for p in sorted(plans,key=_plan_priority):
        try:
            if regular_rows:
                ca=registry.curve(p.left);cb=registry.curve(p.right)
                left=registry.boundary(ca);right=registry.boundary(cb)
            else:ca,cb,left,right,body_left,body_right,fractions=registry.pair(p.left,p.right,False)
            actual_a=[put(key,pos) for key,pos in left]
            actual_b=[put(key,pos) for key,pos in right]
            out.guide_curves[p.index]=(tuple(pos for key,pos in left),tuple(pos for key,pos in right))
            out.transitions[p.index]=(len(left)-1,len(right)-1)
            w=widths[p.index]
            first=p.boundary.index(p.left[0])
            reverse=(p.boundary[(first+1)%len(p.boundary)]==p.left[1])
            if signs[p.index]<0:reverse=not reverse
            out.orientation_by_face[p.index]=reverse
            local=[];local_keys=set()
            def emit(face):
                face=[v for j,v in enumerate(face) if v!=face[j-1]]
                if len(face)>1 and face[0]==face[-1]:face.pop()
                if len(set(face))<3 or length(newell(out.vertices,face))<EPS:return
                if reverse:face.reverse()
                signature=tuple(sorted(face))
                if signature in produced or signature in local_keys:return
                local_keys.add(signature);local.append(tuple(face))
            mapping=registry.maps.get((ca.path,cb.path))
            density_here=any(registry.density.get(edge,1.)>1. for curve in (ca,cb) for edge in curve.edges)
            start_width,body_width,end_width=out.width_transitions[p.index]
            if start_width!=w or end_width!=w:
                from .width import build_transition
                sections,columns,grid,reused=build_transition(p,w,registry,ca,cb,left,right,cap_profiles,put,emit,max_polygons,
                    out.patch_cache,getattr(previous,'patch_cache',{}),mapping)
                out.reused_patches+=reused
            elif not regular_rows and (mapping.strong_turn or density_here or registry.point_field.affected(ca) or registry.point_field.affected(cb)):
                from .surface import build_patch
                sections,columns,reused=build_patch(p,w,registry,ca,cb,left,right,cap_profiles,put,emit,max_polygons,
                    out.patch_cache,getattr(previous,'patch_cache',{}))
                out.reused_patches+=reused
                grid=[]
            else:
                from .strip import build_strip
                sections,columns,grid,reused=build_strip(p,w,registry,ca,cb,left,right,cap_profiles,put,emit,max_polygons,
                    out.patch_cache,getattr(previous,'patch_cache',{}))
                out.reused_patches+=reused
            out.surface_columns[p.index]=tuple(tuple(column) for column in columns)
            if len(local)+len(out.faces)>max_polygons:raise ValueError('Превышен лимит полигонов')
            produced.update(local_keys)
            out.faces.extend(local);out.owners.extend([p.index]*len(local))
            out.surface_grids[p.index]=tuple(tuple(row) for row in grid)
            out.surface_sections[p.index]=sections if getattr(sections,'lazy',False) else tuple(sections)
            out.rail_samples[p.index]=(tuple(left),tuple(right))
            registry.register(ca,left);registry.register(cb,right)
        except (ValueError,IndexError,ZeroDivisionError) as exc:
            out.warnings[p.index]=str(exc)
    out.sampling_cache=registry.geometry_cache
    out.correspondence_cache=registry.map_cache
    out.faces,out.owners=registry.stitch(out.vertices,out.faces,out.owners,vertex_index)
    if len(out.faces)>max_polygons:raise ValueError('Превышен лимит полигонов после сшивки')
    # Keep baked meshes free of unused reference/degenerate positions.
    used=sorted({v for face in out.faces for v in face})
    remap={old:new for new,old in enumerate(used)}
    keys={vid:key for key,vid in vertex_index.items()}
    out.vertex_keys=[keys[v] for v in used]
    out.vertices=[out.vertices[v] for v in used]
    out.faces=[tuple(remap[v] for v in face) for face in out.faces]
    out.surface_grids={zone:tuple(tuple(remap.get(v) if v is not None else None for v in row) for row in grid)
                       for zone,grid in out.surface_grids.items()}
    out.surface_columns={zone:tuple(tuple(remap[v] for v in column if v in remap) for column in columns)
                         for zone,columns in out.surface_columns.items()}
    owner_edges=defaultdict(set)
    for face,owner in zip(out.faces,out.owners):
        owner_edges[owner].update(edge_key(a,b) for a,b in zip(face,face[1:]+face[:1]))
    for zone,columns in out.surface_columns.items():
        w=len(columns)-1
        out.cut_lines[zone]={}
        for x in range(1,w):
            line=columns[x]
            if len(line)>1 and all(a==b or edge_key(a,b) in owner_edges[zone] for a,b in zip(line,line[1:])):
                out.cut_lines[zone][x/w]=tuple(out.vertices[v] for v in line)
    out.samples_per_edge={edge:max(0,len(stations)-1) for edge,stations in registry.stations.items()}
    return out



def width_budget_split(width, t):
    """Divide one strand's WIDTH budget, not its longitudinal row count.

    Both sides need >= 1 column. A one-column strand therefore becomes two
    one-column strands when split, which is the smallest valid subdivision.
    """
    width=max(1,int(width))
    t=max(0.,min(1.,float(t)))
    if width==1: return 1,1
    left=max(1,min(width-1,int(math.floor(width*t+.5))))
    return left,width-left


def sample_cut_guide(result, zone, t, count=20):
    """Resample the ACTUAL generated surface profile, including shared caps.

    The edit tool and the displayed mesh must never use different surface
    formulas. This also preserves the zipper correspondence on uneven rails.
    """
    sections=result.surface_sections.get(zone)
    if not sections:raise ValueError('У зоны нет рассчитанной поверхности')
    width=len(sections[0])-1
    x=max(0.,min(float(width),float(t)*width))
    column=min(width-1,int(x));blend=x-column
    line=next((list(line) for fraction,line in result.cut_lines.get(zone,{}).items() if abs(fraction-t)<1e-8),None)
    if line is None:line=[mix(row[column],row[column+1],blend) for row in sections]
    distances=[0.]
    for a,b in zip(line,line[1:]): distances.append(distances[-1]+length(sub(b,a)))
    total=distances[-1]
    if total<EPS: raise ValueError('Нулевая длина линии разреза')
    output=[]
    for i in range(count+1):
        target=total*i/max(1,count)
        k=min(len(line)-1,max(1,bisect.bisect_left(distances,target)))
        u=(target-distances[k-1])/max(EPS,distances[k]-distances[k-1])
        output.append(mix(line[k-1],line[k],u))
    output[0]=line[0];output[-1]=line[-1]
    return output
