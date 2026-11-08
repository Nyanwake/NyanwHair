"""NyanwHair 0.35.9: face-defined zones and canonical shared boundary profiles.

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
    samples_per_edge: dict=field(default_factory=dict)
    plans_by_loop: dict=field(default_factory=dict)
    # Lightweight diagnostics for explicit transitions.
    transitions: dict=field(default_factory=dict)
    orientation_by_face: dict=field(default_factory=dict)
    base_rows: int=0
    surface_grids: dict=field(default_factory=dict)


def _boundary_key(boundary):
    """Face identity for deterministic decisions, independent of face order."""
    loop=tuple(boundary)
    return min(seq[i:]+seq[:i] for seq in (loop,loop[::-1]) for i in range(len(seq)))


def _cap_profiles(points,plans,widths,evaluate):
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
        w=widths[group[0][0].index]
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


def plan_face(points,boundary,index,settings,hint=None,caps=None):
    loop=tuple(boundary)
    if len(loop)<3 or len(set(loop))!=len(loop):
        raise ValueError('Некорректный контур управляющей грани')
    normal=newell(points,loop)
    if length(normal)<EPS: raise ValueError('Площадь управляющей грани равна нулю')
    if caps is not None:
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


def make_edge_evaluator(points,plans):
    """Piecewise Catmull-Rom following the two *working rails* per face.

    A curve's intended interior continuation is read from its own ordered
    rail, not guessed from arbitrary adjacent graph edges. A shared edge
    always has a single identical evaluator for all zones. If shared zones
    disagree on a continuation, use a safe one-sided tangent at that end.
    """
    through=defaultdict(set)
    for p in plans:
        for rail in (p.left,p.right):
            for prev,a,nxt in zip(rail,rail[1:],rail[2:]):
                through[(a,nxt)].add(prev)
                through[(a,prev)].add(nxt)
    cache={}
    def tangent(a,b):
        span=sub(points[b],points[a]); spanlen=length(span)
        options=through.get((a,b),set())
        if len(options)==1:
            other=next(iter(options))
        elif len(options)>1:
            other=None
        else:
            # A cap is NOT a through-rail. Taking the other adjacent edge
            # here caused the rectangular starter to bulge into waves, since
            # its top/bottom cross-edge was mistaken for a curve continuation.
            other=None
        if other is not None:
            # Angle-bisector Hermite tangent with centripetal-length limiter.
            # It follows the intended through-rail, cannot overshoot a short
            # neighbouring control edge, and is the same for shared zones.
            incoming=sub(points[a],points[other]); prevlen=length(incoming)
            if prevlen>EPS:
                direction=norm(add(norm(incoming),norm(span)))
                if dot(direction,span)>EPS:
                    # A modestly stronger centripetal tangent rounds the
                    # transition through supports while remaining bounded by
                    # the two adjoining spans; it cannot flare arbitrarily.
                    magnitude=min(1.20*spanlen,1.20*prevlen,
                                  .69*(spanlen+prevlen))
                    return mul(direction,magnitude)
        return span
    def evaluate(a,b,t):
        u,v=edge_key(a,b)
        pair=(u,v)
        if pair not in cache:
            cache[pair]=(points[u],points[v],tangent(u,v),mul(tangent(v,u),-1.0))
        p0,p1,m0,m1=cache[pair]
        t=t if a==u else 1-t
        tt=t*t; ttt=tt*t
        return add(add(mul(p0,2*ttt-3*tt+1),mul(p1,-2*ttt+3*tt)),
                   add(mul(m0,ttt-2*tt+t),mul(m1,ttt-tt)))
    return evaluate


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



def _shared_runs(plans):
    """Maximal shared guide chains, NOT one station run per control edge.

    A BMVert of degree two inside the same two-zone seam is purely a spline
    control and must not impose an output polygon row.  Only changes in seam
    ownership (true junctions), or the end of a zone, stop a shared run.
    """
    owners=defaultdict(set)
    for plan in plans:
        for path in (plan.left,plan.right):
            for key in physical_edges(path):
                owners[key].add(plan.index)
    common={edge:tuple(sorted(group)) for edge,group in owners.items() if len(group)>=2}
    by_pair=defaultdict(set)
    for edge,pair in common.items():
        by_pair[pair].add(edge)
    chains={}
    edge_to_chain={}
    for pair,edges in sorted(by_pair.items()):
        adjacency=defaultdict(list)
        for a,b in sorted(edges):
            adjacency[a].append(b)
            adjacency[b].append(a)
        pending=set(edges)
        # The endpoints of an open shared chain must have non-2 valence in
        # the subgraph owned by exactly this set of zones.
        def walk(first,second):
            seq=[first,second]
            pending.remove(edge_key(first,second))
            prev,cur=first,second
            while len(adjacency[cur])==2:
                candidates=[n for n in adjacency[cur] if n!=prev]
                if not candidates: break
                nxt=candidates[0]
                key=edge_key(cur,nxt)
                if key not in pending: break
                pending.remove(key)
                seq.append(nxt)
                prev,cur=cur,nxt
            return tuple(seq)
        for first in sorted(adjacency):
            if len(adjacency[first])==2: continue
            for second in sorted(adjacency[first]):
                if edge_key(first,second) not in pending: continue
                sequence=walk(first,second)
                reverse=sequence[::-1]
                chain=min(sequence,reverse)
                chains[chain]=chain
                for u,v in zip(chain,chain[1:]): edge_to_chain[edge_key(u,v)]=chain
        # Closed seams are uncommon for hair ribbons.  Keep their edges
        # independent instead of inventing an arbitrary loop cut station.
        while pending:
            a,b=min(pending)
            pending.remove((a,b))
            chain=(a,b)
            chains[chain]=chain
            edge_to_chain[(a,b)]=chain
    return chains,edge_to_chain


def allocate_stations(points,plans,rows,previous=None):
    """Share one station schedule per continuous seam between true junctions.

    An intermediate control vertex is part of the curve geometry, not a
    required polygon vertex. The schedule is independent of its position
    while dragging, as long as the topology is unchanged.
    """
    chains,edge_to_chain=_shared_runs(plans)
    anchored={p.index:(any(e in edge_to_chain for e in physical_edges(p.left)),
                       any(e in edge_to_chain for e in physical_edges(p.right)))
              for p in plans}
    # A fixed budget per logical run is a function of topology + settings.
    # Frozen geometry-derived counts lived only in RAM and changed on reopen.
    counts={chain:rows for chain in chains}
    return counts,anchored,chains,edge_to_chain


def bias_cross_position(u,bias):
    """Symmetric width redistribution: positive toward centre, negative outward.

    Endpoints and the midpoint are invariant.  Unlike asymmetric u**power,
    changing bias cannot move the strand's central guide sideways.
    """
    u=max(0.,min(1.,float(u)))
    exponent=2.0**max(-2.,min(2.,float(bias)))
    return .5+math.copysign(.5*(abs(2.*u-1.)**exponent),u-.5) if u!=.5 else .5


def _sample_shared_curve(points,chain,count,evaluate):
    return sample_smooth(points,chain,count,evaluate,('shared',chain))


def _sample_rail(points,path,counts,rows,evaluate,edge_to_chain,shared_cache,ident):
    """Emit seam stations identically for both owners and free stations elsewhere.

    Each shared chain is sampled by arc length as a WHOLE, regardless of how
    many intermediate Blender control vertices it contains. Private guide
    sections are also sampled as complete uninterrupted subcurves.
    """
    edges=physical_edges(path)
    if not any(e in edge_to_chain for e in edges): return None
    result=[]
    index=0
    while index<len(edges):
        chain=edge_to_chain.get(edges[index])
        if chain is not None:
            reverse=chain[::-1]
            seq=path[index:index+len(chain)]
            if tuple(seq)==chain or tuple(seq)==reverse:
                if chain not in shared_cache:
                    shared_cache[chain]=_sample_shared_curve(points,chain,counts[chain],evaluate)
                sample=(shared_cache[chain] if tuple(seq)==chain
                        else list(reversed(shared_cache[chain])))
                index+=len(chain)-1
            else:
                # A face that owns only a partial chain indicates an unusual
                # topology. Do not silently generate nonidentical seam keys.
                raise ValueError('Общая направляющая разорвана внутри области')
        else:
            end=index+1
            while end<len(edges) and edges[end] not in edge_to_chain:
                end+=1
            subpath=path[index:end+1]
            budget=rows
            sample=sample_smooth(points,subpath,budget,evaluate,(ident,index,end))
            index=end
        if result: sample=sample[1:]
        result.extend(sample)
    return result


def plan_widths(plans):
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
    return {p.index:widths[root(p.index)] for p in plans}


def zipper_path(na,nb):
    """Fixed monotone correspondence; only count changes can alter it.

    With equal counts, every step is a quad row. With unequal counts, local
    one-sided transitions become triangles nearest the compressed rail.
    """
    if na<1 or nb<1: return []
    a=b=0
    path=[(a,b)]
    while a<na or b<nb:
        if a==na: b+=1
        elif b==nb: a+=1
        else:
            da=(a+1)/na; db=(b+1)/nb
            if abs(da-db)<0.5/max(na,nb): a+=1; b+=1
            elif da<db: a+=1
            else: b+=1
        path.append((a,b))
    return path


def generate(points,boundaries,settings=None,rows=12,max_polygons=200000,
             previous=None,caps=None):
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
    for first in sorted(by_id,key=lambda i:_boundary_key(by_id[i].boundary)):
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
    counts,anchors,shared_chains,edge_to_chain=allocate_stations(points,plans,rows)
    out.samples_per_edge=counts
    widths=plan_widths(plans)
    out.effective_widths=widths
    out.base_rows=rows
    evaluate=make_edge_evaluator(points,plans)
    cap_profiles=_cap_profiles(points,plans,widths,evaluate)
    shared_cache={}
    vertex_index={}
    produced=set()
    def put(key,pos):
        if key not in vertex_index:
            vertex_index[key]=len(out.vertices)
            out.vertices.append(pos)
        return vertex_index[key]
    for p in plans:
        try:
            al,ar=anchors[p.index]
            # Control points on a shared guide no longer introduce rows.
            # Complete shared chains have one common arclength schedule.
            left=_sample_rail(points,p.left,counts,rows,evaluate,edge_to_chain,
                              shared_cache,(p.index,'left')) if al else None
            right=_sample_rail(points,p.right,counts,rows,evaluate,edge_to_chain,
                               shared_cache,(p.index,'right')) if ar else None
            if left is None:
                target=len(right)-1 if right is not None else rows
                left=sample_smooth(points,p.left,max(2,target),evaluate,(p.index,'left'))
            if right is None:
                right=sample_smooth(points,p.right,max(2,len(left)-1),evaluate,(p.index,'right'))
            out.guide_curves[p.index]=(tuple(pos for key,pos in left),
                                       tuple(pos for key,pos in right))
            na=len(left)-1; nb=len(right)-1
            path=zipper_path(na,nb)
            if not path: raise ValueError('Недостаточно точек направляющих')
            w=widths[p.index]
            out.transitions[p.index]=(na,nb)
            center=[mix(a[1],b[1],.5) for a,b in ((left[i],right[j]) for i,j in path)]
            normals=[]
            for k,(i,j) in enumerate(path):
                a=left[i][1]; b=right[j][1]
                wvec=sub(b,a)
                if len(path)>1:
                    t=sub(center[min(len(path)-1,k+1)],center[max(0,k-1)])
                else: t=(0.,0.,1.)
                n=norm(cross(wvec,t))
                if length(n)<EPS:
                    n=normals[-1] if normals else norm(p.normal)
                # Consistent transport from the first row. Individual
                # dot(n, face_normal) tests would make bulge flip mid-strand.
                reference=normals[-1] if normals else p.normal
                if dot(n,reference)<0: n=mul(n,-1.)
                normals.append(n)
            # Winding is a face-level decision.  Reversing individual quads
            # according to near-zero local signed area creates flickering
            # topology during edits and can make adjacent faces disagree.
            first=p.boundary.index(p.left[0])
            reverse=(p.boundary[(first+1)%len(p.boundary)]==p.left[1])
            if signs[p.index]<0: reverse=not reverse
            out.orientation_by_face[p.index]=reverse
            grid=[]
            for k,(i,j) in enumerate(path):
                akey,a=left[i]; bkey,b=right[j]
                # Bias and bulge describe the CROSS SECTION of a strand.
                # They do not vanish at its root/tip: that produced the
                # characteristic 'hill in the middle' in v0.35.0.
                row=[]
                for x in range(w+1):
                    if x==0: key=akey; pos=a
                    elif x==w: key=bkey; pos=b
                    elif akey==bkey: key=akey; pos=a
                    else:
                        v=bias_cross_position(x/w,p.settings.bias)
                        pos=mix(a,b,v)
                        amplitude=p.settings.bulge*length(sub(b,a))*math.sin(math.pi*v)
                        if p.settings.flip: amplitude=-amplitude
                        pos=add(pos,mul(normals[k],amplitude))
                        if k==0 and p.start[0]=='edge' or k==len(path)-1 and p.end[0]=='edge':
                            cap=p.start if k==0 else p.end
                            lo,hi=edge_key(p.boundary[cap[1]],p.boundary[(cap[1]+1)%len(p.boundary)])
                            a_v=p.left[0 if k==0 else -1]
                            aligned=x if a_v==lo else w-x
                            key=('cap',lo,hi,aligned,w)
                            pos=cap_profiles[(lo,hi)][aligned]
                        else: key=('inside',p.index,k,x)
                    row.append(put(key,pos))
                grid.append(row)
            local=[]
            local_keys=set()
            for k in range(len(path)-1):
                for x in range(w):
                    face=[grid[k][x],grid[k][x+1],grid[k+1][x+1],grid[k+1][x]]
                    face=[v for j,v in enumerate(face) if v!=face[j-1]]
                    if len(face)>1 and face[0]==face[-1]: face.pop()
                    if len(set(face))<3: continue
                    a,b,c=(out.vertices[idx] for idx in face[:3])
                    if length(cross(sub(b,a),sub(c,a)))<1e-12: continue
                    if reverse: face.reverse()
                    sig=tuple(sorted(face))
                    if sig in produced or sig in local_keys: continue
                    local_keys.add(sig); local.append(tuple(face))
            if len(local)+len(out.faces)>max_polygons:
                raise ValueError('Превышен лимит полигонов')
            actual_edges={edge_key(a,b) for f in local
                          for a,b in zip(f, f[1:]+f[:1])}
            # Ctrl+R must pick a line present in the final mesh, not a
            # theoretical interpolation that failed to produce any polygons.
            out.cut_lines[p.index]={
                x/w:tuple(out.vertices[grid[k][x]] for k in range(len(grid)))
                for x in range(1,w)
                if all(edge_key(grid[k][x],grid[k+1][x]) in actual_edges
                       for k in range(len(grid)-1)
                       if grid[k][x]!=grid[k+1][x])
            }
            produced.update(local_keys)
            out.faces.extend(local)
            out.owners.extend([p.index]*len(local))
            out.surface_grids[p.index]=tuple(tuple(row) for row in grid)
        except (ValueError,IndexError,ZeroDivisionError) as exc:
            out.warnings[p.index]=str(exc)
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
    grid=result.surface_grids.get(zone)
    if not grid:
        raise ValueError('У зоны нет рассчитанной поверхности')
    width=len(grid[0])-1
    x=max(0.,min(float(width),float(t)*width))
    column=min(width-1,int(x))
    blend=x-column
    line=[mix(result.vertices[row[column]],result.vertices[row[column+1]],blend)
          for row in grid]
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
    return output
