"""Point thickness weights applied to the reverse sheet, leaving the front intact."""
import bisect
from . import core


def _weight(key,points):
    if key[0]=='v':return 1.-points.get(key[1],0.)
    if key[0] in ('s','cap'):
        t=key[3] if key[0]=='s' else key[3]/key[4]
        return 1.-(points.get(key[1],0.)*(1.-t)+points.get(key[2],0.)*t)
    return None


def _section(rows,u,v):
    """Sample the existing front mesh rows even with independent back width."""
    t=u*(len(rows)-1);i=min(len(rows)-2,int(t));t-=i
    def cross(row):
        x=v*(len(row)-1);j=min(len(row)-2,int(x));return core.mix(row[j],row[j+1],x-j)
    return core.mix(cross(rows[i]),cross(rows[i+1]),t)


def profile(front,back,graph,points):
    """Interpolate pinned guide weights across each connected reverse surface."""
    stamp=(id(front),tuple(graph),tuple(sorted(points.items())));saved=getattr(back,'volume_profile_cache',None)
    if saved and saved[0]==stamp:return saved[1:]
    fixed={v:max(0.,min(1.,value)) for v in graph if (value:=_weight(back.vertex_keys[v],points)) is not None}
    used=sorted(graph)
    if not fixed:return {},{}
    if min(fixed.values())==max(fixed.values()):weights={v:next(iter(fixed.values())) for v in used}
    else:
        import numpy as np
        ids={v:i for i,v in enumerate(used)}
        edges=[(ids[a],ids[b],1./max(core.EPS,size)) for a in used for b,size in graph[a].items()]
        src=np.asarray([a for a,b,w in edges]);dst=np.asarray([b for a,b,w in edges]);gain=np.asarray([w for a,b,w in edges])
        denom=np.maximum(core.EPS,np.bincount(src,weights=gain,minlength=len(used)))
        values=np.ones(len(used));pinned=np.asarray([ids[v] for v in fixed]);pins=np.asarray(list(fixed.values()));values[pinned]=pins
        for _ in range(64):
            new=.25*values+.75*np.bincount(src,weights=gain*values[dst],minlength=len(used))/denom
            new[pinned]=pins;delta=np.max(np.abs(new-values));values=new
            if delta<1e-5:break
        weights={v:float(values[ids[v]]) for v in used}
    anchors={};lookup={key:p for key,p in zip(front.vertex_keys,front.vertices)}
    # Regular and width-transition grids retain the exact row/column parameters.
    for owner,grid in back.surface_grids.items():
        rows=front.surface_sections.get(owner)
        if rows is None:continue
        for k,row in enumerate(grid):
            for x,v in enumerate(row):
                if v is not None:anchors[v]=_section(rows,k/(len(grid)-1),x/(len(row)-1))
    # Adaptive columns are also supported for programmatic generator callers.
    for owner,columns in back.surface_columns.items():
        rows=front.surface_sections.get(owner)
        if rows is None:continue
        for x,column in enumerate(columns):
            for k,v in enumerate(column):
                if len(column)>1 and v not in anchors:anchors[v]=_section(rows,k/(len(column)-1),x/(len(columns)-1))
    # Shared guide/cap identities take precedence over per-patch interpolation.
    caps={}
    for key,pos in lookup.items():
        if key[0]=='cap':caps.setdefault(key[1:3],[]).append((key[3]/key[4],pos))
    for edge,samples in caps.items():
        samples.extend((t,lookup[('v',v)]) for t,v in ((0.,edge[0]),(1.,edge[1])) if ('v',v) in lookup);samples.sort()
    for v in used:
        key=back.vertex_keys[v]
        if key[0] in ('v','s'):anchors[v]=back.vertices[v]
        elif key in lookup and key[0]=='cap':anchors[v]=lookup[key]
        elif key[0]=='cap' and key[1:3] in caps:
            samples=caps[key[1:3]];t=key[3]/key[4];i=min(len(samples)-1,max(1,bisect.bisect_left([s for s,p in samples],t)))
            a,p=samples[i-1];b,q=samples[i];anchors[v]=core.mix(p,q,(t-a)/max(core.EPS,b-a))
    back.volume_profile_cache=(stamp,weights,anchors)
    return weights,anchors


def seed(result,owner,position):
    """Initialize new Loop/support controls from the previous boundary profile."""
    plan=next((p for p in result.plans_by_loop.values() if p.index==owner),None)
    if plan is None or not plan.settings.volume_points:return 0.
    values=dict(plan.settings.volume_points);hits=[]
    for path in (plan.left,plan.right):
        samples=[]
        for a,b in zip(path,path[1:]):
            line=[result.edge_evaluator(a,b,i/24) for i in range(25)]
            for i,(p,q) in enumerate(zip(line,line[1:])):
                delta=core.sub(q,p);t=max(0.,min(1.,core.dot(core.sub(position,p),delta)/max(core.EPS**2,core.dot(delta,delta))))
                distance=core.length(core.sub(position,core.mix(p,q,t)));u=(i+t)/24
                samples.append((distance,values.get(a,0.)*(1.-u)+values.get(b,0.)*u))
        if samples:hits.append(min(samples))
    if len(hits)!=2:return hits[0][1] if hits else 0.
    (a,x),(b,y)=hits
    return (x*b+y*a)/(a+b) if a+b>core.EPS else (x+y)*.5
