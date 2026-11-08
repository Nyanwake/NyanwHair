"""Shared curve sampling at a common physical spacing; no Blender dependencies.

The curve evaluator is supplied by core. Topological sample identity is a
source vertex or an oriented parameter on a source edge, never a position.
"""
import bisect
from collections import defaultdict
from .core import EPS,CURVE_SUBDIVISIONS,sub,add,cross,length,norm,edge_key,newell


class CurvePath:
    def __init__(self,points,path,evaluate):
        self.points=points; self.path=tuple(path); self.evaluate=evaluate
        self.vertices={v:i for i,v in enumerate(path)}
        self.edges={edge_key(a,b):(i,a,b) for i,(a,b) in enumerate(zip(path,path[1:]))}
        self.tables=[]; self.offsets=[0.]
        subdivisions=CURVE_SUBDIVISIONS
        for a,b in zip(path,path[1:]):
            positions=[evaluate(a,b,j/subdivisions) for j in range(subdivisions+1)]
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
        subdivisions=CURVE_SUBDIVISIONS
        x=max(0.,min(float(subdivisions),t*subdivisions))
        j=min(subdivisions-1,int(x));blend=x-j
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
        j=min(CURVE_SUBDIVISIONS,max(1,bisect.bisect_left(arc,d)))
        t=((j-1)+(d-arc[j-1])/max(EPS,arc[j]-arc[j-1]))/CURVE_SUBDIVISIONS
        a,b=self.path[i:i+2];lo,hi=edge_key(a,b)
        t=round(t if a==lo else 1.-t,10)
        if t<=1e-10:return ('v',lo),self.points[lo]
        if t>=1.-1e-10:return ('v',hi),self.points[hi]
        return ('s',lo,hi,t),self.evaluate(lo,hi,t)

    def sample(self,fractions):return [self.at(u) for u in fractions]



class RailRegistry:
    """Sample each physical curve once, then share its topological identities.

    Logical junctions are mandatory anchors. Regular support vertices do
    not allocate rows. No RAM cache or per-zone length budget is involved.
    """
    def __init__(self,points,evaluate,rows,plans,reference_length=0.,max_samples=200000):
        self.points=points;self.evaluate=evaluate;self.rows=rows
        self.stations=defaultdict(dict);self.paths={};self.boundaries=[]
        self.maps={}
        chains=[]
        for path in evaluate.paths:
            if sum(length(sub(points[b],points[a])) for a,b in zip(path,path[1:]))>EPS:
                chains.append(self.curve(path))
        self.reference_length=reference_length if reference_length>EPS else max((c.total for c in chains),default=1.)
        self.spacing=self.reference_length/rows
        mandatory={v for p in plans for rail in (p.left,p.right) for v in (rail[0],rail[-1])}
        mandatory.update(v for v,neighbors in evaluate.adjacency.items() if len(neighbors)!=2)
        allocated=0
        for curve in chains:
            stops=[(0.,curve.path[0])]
            stops.extend((curve.fraction(('v',v)),v) for v in curve.path[1:-1] if v in mandatory)
            stops.append((1.,curve.path[-1]))
            stops.sort()
            for (lo,first),(hi,last) in zip(stops,stops[1:]):
                intervals=max(1,int((hi-lo)*curve.total/self.spacing+.5))
                allocated+=intervals
                if allocated>2*max_samples:raise ValueError('Превышен лимит точек детализации')
                samples=curve.sample(lo+(hi-lo)*i/intervals for i in range(intervals+1))
                samples[0]=(('v',first),points[first]);samples[-1]=(('v',last),points[last])
                for key,pos in samples:
                    if key[0]=='s':self.stations[(key[1],key[2])][key[3]]=(key,pos)
                    else:
                        vertex=key[1]
                        for adjacent in evaluate.adjacency[vertex]:
                            edge=edge_key(vertex,adjacent);t=0. if vertex==edge[0] else 1.
                            self.stations[edge][t]=(key,pos)

    def curve(self,path):
        path=tuple(path)
        if path not in self.paths:self.paths[path]=CurvePath(self.points,path,self.evaluate)
        return self.paths[path]

    def boundary(self,curve):
        samples={('v',curve.path[0]):self.points[curve.path[0]],
                 ('v',curve.path[-1]):self.points[curve.path[-1]]}
        for edge in curve.edges:
            for key,pos in self.stations[edge].values():
                if curve.parameter(key) is not None:samples[key]=pos
        return sorted(samples.items(),key=lambda item:curve.parameter(item[0]))

    def pair(self,left,right):
        from .ribbon import RibbonMap
        a=self.curve(left);b=self.curve(right)
        left_samples=self.boundary(a);right_samples=self.boundary(b)
        mapping=RibbonMap(a,b);self.maps[a.path,b.path]=mapping
        count=max(1,int(mapping.total/self.spacing+.5))
        fractions=[i/count for i in range(count+1)]
        paired=[mapping.at(u) for u in fractions]
        return a,b,left_samples,right_samples,[p[0] for p in paired],[p[1] for p in paired],fractions

    def flows(self,a,b,curve,samples):
        mapping=self.maps[a.path,b.path]
        return [mapping.fraction(curve,key) for key,pos in samples]

    def register(self,curve,samples):
        self.boundaries.append((curve,samples))

    def normal(self,a,b,u,left,right):
        return self.maps[a.path,b.path].normal(u,left,right)

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
