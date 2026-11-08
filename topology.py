"""First-owner rail schedules and local stitches; no Blender dependencies.

The curve evaluator is supplied by core. Topological sample identity is a
source vertex or an oriented parameter on a source edge, never a position.
"""
import bisect
from collections import defaultdict
from .core import EPS,sub,add,cross,length,norm,edge_key,newell


class CurvePath:
    def __init__(self,points,path,evaluate):
        self.points=points; self.path=tuple(path); self.evaluate=evaluate
        self.vertices={v:i for i,v in enumerate(path)}
        self.edges={edge_key(a,b):(i,a,b) for i,(a,b) in enumerate(zip(path,path[1:]))}
        self.tables=[]; self.offsets=[0.]
        for a,b in zip(path,path[1:]):
            positions=[evaluate(a,b,j/12.) for j in range(13)]
            arc=[0.]
            for p,q in zip(positions,positions[1:]):arc.append(arc[-1]+length(sub(q,p)))
            self.tables.append(arc); self.offsets.append(self.offsets[-1]+arc[-1])
        self.total=self.offsets[-1]
        if self.total<EPS:raise ValueError('Нулевая длина направляющей')

    def parameter(self,key):
        if key[0]=='v':return float(self.vertices[key[1]]) if key[1] in self.vertices else None
        if key[0]!='s':return None
        entry=self.edges.get((key[1],key[2]))
        if entry is None:return None
        i,a,b=entry
        return i+(key[3] if a<b else 1.-key[3])

    def fraction(self,key):
        s=self.parameter(key)
        if s is None:raise ValueError('Точка не принадлежит направляющей')
        i=min(len(self.tables)-1,int(s));t=s-i
        x=max(0.,min(12.,t*12.));j=min(11,int(x));blend=x-j
        arc=self.tables[i]
        return (self.offsets[i]+arc[j]*(1.-blend)+arc[j+1]*blend)/self.total

    def at(self,u):
        u=max(0.,min(1.,float(u)))
        # Zero-length construction edges must not change endpoint identity.
        if u==0.:
            v=self.path[0];return ('v',v),self.points[v]
        if u==1.:
            v=self.path[-1];return ('v',v),self.points[v]
        distance=u*self.total
        i=min(len(self.tables)-1,max(0,bisect.bisect_right(self.offsets,distance)-1))
        if abs(distance-self.offsets[i])<1e-10:
            v=self.path[i];return ('v',v),self.points[v]
        if abs(distance-self.total)<1e-10:
            v=self.path[-1];return ('v',v),self.points[v]
        arc=self.tables[i];d=distance-self.offsets[i]
        j=min(12,max(1,bisect.bisect_left(arc,d)))
        t=((j-1)+(d-arc[j-1])/max(EPS,arc[j]-arc[j-1]))/12.
        a,b=self.path[i:i+2];lo,hi=edge_key(a,b)
        t=round(t if a==lo else 1.-t,10)
        if t<=1e-10:return ('v',lo),self.points[lo]
        if t>=1.-1e-10:return ('v',hi),self.points[hi]
        return ('s',lo,hi,t),self.evaluate(lo,hi,t)

    def sample(self,fractions):return [self.at(u) for u in fractions]


def _apportion(weights,budget):
    """Allocate only the remaining budget, not rows anew for every fragment."""
    if not weights:return []
    budget=max(len(weights),int(budget))
    extra=budget-len(weights);total=max(EPS,sum(weights))
    raw=[extra*w/total for w in weights]
    result=[1+int(x) for x in raw]
    for i in sorted(range(len(weights)),key=lambda i:(-(raw[i]-int(raw[i])),i))[:budget-sum(result)]:
        result[i]+=1
    return result


class RailRegistry:
    """Schedules are rebuilt in persisted creation order on every generation.

    Only the first owner chooses samples. Later owners keep those positions;
    a new endpoint inside an old cell is stitched locally after all walls.
    No Python cache or geometric nearest-owner guess is involved.
    """
    def __init__(self,points,evaluate,rows):
        self.points=points;self.evaluate=evaluate;self.rows=rows
        self.stations=defaultdict(dict);self.owners={};self.paths={};self.boundaries=[]

    def curve(self,path):
        path=tuple(path)
        if path not in self.paths:self.paths[path]=CurvePath(self.points,path,self.evaluate)
        return self.paths[path]

    def priority(self,curve):
        return min((self.owners[e] for e in curve.edges if e in self.owners),default=None)

    def inherited(self,curve):
        """Existing subpaths plus a bounded remainder for genuinely new edges."""
        path=curve.path;n=len(path)-1;parts=[];i=0
        while i<n:
            known=edge_key(path[i],path[i+1]) in self.owners
            end=i+1
            while end<n and (edge_key(path[end],path[end+1]) in self.owners)==known:end+=1
            if known:
                samples={('v',path[i]):self.points[path[i]],('v',path[end]):self.points[path[end]]}
                for j in range(i,end):
                    for key,pos in self.stations[edge_key(path[j],path[j+1])].values():
                        s=curve.parameter(key)
                        if s is not None and i-1e-9<=s<=end+1e-9:samples[key]=pos
                items=sorted(samples.items(),key=lambda pair:curve.parameter(pair[0]))
                parts.append((True,i,end,items))
            else:parts.append((False,i,end,None))
            i=end
        if not any(p[0] for p in parts):return None
        locked=sum(len(p[3])-1 for p in parts if p[0])
        free=[p for p in parts if not p[0]]
        weights=[curve.offsets[p[2]]-curve.offsets[p[1]] for p in free]
        budgets=iter(_apportion(weights,max(len(free),self.rows-locked)))
        output=[]
        for known,i,end,samples in parts:
            if not known:
                budget=next(budgets)
                lo=curve.offsets[i]/curve.total;hi=curve.offsets[end]/curve.total
                samples=curve.sample(lo+(hi-lo)*j/budget for j in range(budget+1))
                samples[0]=(('v',path[i]),self.points[path[i]])
                samples[-1]=(('v',path[end]),self.points[path[end]])
            output.extend(samples if not output else samples[1:])
        return output

    def pair(self,left,right):
        a=self.curve(left);b=self.curve(right)
        pa=self.priority(a);pb=self.priority(b)
        master_left=pb is None or pa is not None and pa<=pb
        master=a if master_left else b
        actual=self.inherited(master)
        if actual is None:actual=master.sample(i/self.rows for i in range(self.rows+1))
        fractions=[master.fraction(key) for key,pos in actual]
        fractions[0]=0.;fractions[-1]=1.
        other=b if master_left else a
        paired=other.sample(fractions)
        actual_other=self.inherited(other)
        if actual_other is None:actual_other=paired
        if master_left:return a,b,actual,actual_other,actual,paired,fractions
        return a,b,actual_other,actual,paired,actual,fractions

    def register(self,curve,samples,priority):
        path=curve.path
        for edge in curve.edges:self.owners.setdefault(edge,priority)
        for key,pos in samples:
            if key[0]=='s':
                edge=(key[1],key[2]);self.stations[edge].setdefault(key[3],(key,pos))
            elif key[0]=='v':
                i=curve.vertices[key[1]]
                for j in (i-1,i):
                    if 0<=j<len(path)-1:
                        edge=edge_key(path[j],path[j+1]);t=0. if key[1]==edge[0] else 1.
                        self.stations[edge].setdefault(t,(key,pos))
        self.boundaries.append((curve,samples))

    def normal(self,a,b,u,left,right):
        eps=.001
        lo=max(0.,u-eps);hi=min(1.,u+eps)
        tangent=sub(add(a.at(hi)[1],b.at(hi)[1]),add(a.at(lo)[1],b.at(lo)[1]))
        return norm(cross(sub(right,left),tangent))

    def stitch(self,vertices,faces,owners,index):
        """Insert missing samples only into cells bordering the changed seam."""
        used={v for face in faces for v in face}
        insertions={}
        for curve,samples in self.boundaries:
            candidates={}
            for edge in curve.edges:
                for key,pos in self.stations[edge].values():
                    vid=index.get(key)
                    if vid is None or vid not in used:continue
                    parameter=curve.parameter(key)
                    if parameter is not None:candidates[key]=(parameter,vid)
            ordered=sorted(candidates.values())
            parameters=[p[0] for p in ordered]
            for (ka,_),(kb,_) in zip(samples,samples[1:]):
                va=index.get(ka);vb=index.get(kb)
                if va is None or vb is None or va==vb:continue
                sa=curve.parameter(ka);sb=curve.parameter(kb)
                lo=bisect.bisect_right(parameters,sa+1e-9)
                hi=bisect.bisect_left(parameters,sb-1e-9)
                mids=[vid for _,vid in ordered[lo:hi] if vid not in (va,vb)]
                if mids:
                    key=edge_key(va,vb)
                    insertions[key]=mids if va==key[0] else mids[::-1]
        output=[];out_owners=[]
        for face,owner in zip(faces,owners):
            expanded=[];changes=[]
            for i,a in enumerate(face):
                b=face[(i+1)%len(face)]
                expanded.append(a)
                mids=insertions.get(edge_key(a,b),())
                if mids:
                    if a>b:mids=mids[::-1]
                    changes.append((len(expanded)-1,len(mids)))
                    expanded.extend(mids)
            if not changes:polygons=[face]
            elif len(face)==4 and len(changes)==1:
                start,count=changes[0];poly=expanded[start:]+expanded[:start]
                polygons=[(poly[j],poly[j+1],poly[-1]) for j in range(count)]
                polygons.append(tuple(poly[-4:]))
            else:polygons=_triangulate(vertices,expanded)
            for poly in polygons:
                if len(set(poly))>=3 and length(newell(vertices,poly))>EPS:
                    output.append(tuple(poly));out_owners.append(owner)
        return output,out_owners


def _triangulate(points,polygon):
    """Ear clipping retains collinear boundary stations instead of bypassing them."""
    polygon=list(polygon)
    normal=newell(points,polygon);axis=max(range(3),key=lambda i:abs(normal[i]))
    axes=[i for i in range(3) if i!=axis]
    xy={v:(points[v][axes[0]],points[v][axes[1]]) for v in polygon}
    def area(a,b,c):
        a,b,c=xy[a],xy[b],xy[c]
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    signed=sum(xy[a][0]*xy[b][1]-xy[b][0]*xy[a][1]
               for a,b in zip(polygon,polygon[1:]+polygon[:1]))
    sign=1 if signed>=0 else -1
    output=[]
    while len(polygon)>3:
        for i,b in enumerate(polygon):
            a=polygon[i-1];c=polygon[(i+1)%len(polygon)]
            if sign*area(a,b,c)<=1e-12:continue
            if any(sign*area(a,b,v)>=-1e-12 and sign*area(b,c,v)>=-1e-12 and
                   sign*area(c,a,v)>=-1e-12 for v in polygon if v not in (a,b,c)):continue
            output.append((a,b,c));polygon.pop(i);break
        else:raise ValueError('Не удалось локально сшить сложный контур')
    if len(polygon)==3:output.append(tuple(polygon))
    return output
