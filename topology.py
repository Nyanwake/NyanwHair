"""Shared curve sampling at a common physical spacing; no Blender dependencies.

The curve evaluator is supplied by core. Topological sample identity is a
source vertex or an oriented parameter on a source edge, never a position.
"""
import bisect
import math
try:
    import numpy as np
except ImportError:
    np=None
from collections import defaultdict
from .core import EPS,CURVE_SUBDIVISIONS,sub,add,cross,length,norm,edge_key,newell,mix,dot,mul


class CurvePath:
    def __init__(self,points,path,evaluate,cached=None):
        self.points=points; self.path=tuple(path); self.evaluate=evaluate
        self.vertices={v:i for i,v in enumerate(path)}
        self.edges={edge_key(a,b):(i,a,b) for i,(a,b) in enumerate(zip(path,path[1:]))}
        self.tables=[]; self.offsets=[0.]; self._samples={}
        if cached is not None:
            self.tables,self.offsets,self.total=cached
            return
        if np is not None and hasattr(evaluate,'coefficients'):
            coefficients=[]
            for a,b in zip(path,path[1:]):
                data=evaluate.coefficients.get((a,b))
                if data is None:
                    q,p,mb,ma=evaluate.coefficients[b,a];data=(p,q,mul(ma,-1.),mul(mb,-1.))
                coefficients.append(data)
            t=np.linspace(0.,1.,CURVE_SUBDIVISIONS+1);tt=t*t;ttt=tt*t
            basis=np.stack((2*ttt-3*tt+1,-2*ttt+3*tt,ttt-2*tt+t,ttt-tt),axis=1)
            positions=np.einsum('si,eij->esj',basis,np.asarray(coefficients))
            distances=np.linalg.norm(np.diff(positions,axis=1),axis=2)
            arcs=np.concatenate((np.zeros((len(coefficients),1)),np.cumsum(distances,axis=1)),axis=1)
            self.tables=arcs.tolist();self.offsets=np.concatenate(([0.],np.cumsum(arcs[:,-1]))).tolist();self.total=self.offsets[-1]
            if self.total<EPS:raise ValueError('Нулевая длина направляющей')
            return
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
        if u in self._samples:return self._samples[u]
        sample=self._at(u);self._samples[u]=sample
        return sample

    def _at(self,u):
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

    def positions(self,fractions):
        if np is None:return [self.at(u)[1] for u in fractions]
        if not hasattr(self.evaluate,'coefficients'):return np.asarray([self.at(u)[1] for u in fractions])
        if not hasattr(self,'_vector_data'):
            arcs=[];parameters=[];coefficients=[]
            for i,(a,b) in enumerate(zip(self.path,self.path[1:])):
                arcs.extend(self.offsets[i]+v for v in self.tables[i])
                parameters.extend(i+j/CURVE_SUBDIVISIONS for j in range(CURVE_SUBDIVISIONS+1))
                data=self.evaluate.coefficients.get((a,b))
                if data is None:
                    q,p,mb,ma=self.evaluate.coefficients[b,a];data=(p,q,mul(ma,-1.),mul(mb,-1.))
                coefficients.append(data)
            self._vector_data=(np.asarray(arcs),np.asarray(parameters),np.asarray(coefficients))
        arcs,parameters,coefficients=self._vector_data
        value=np.interp(np.clip(np.asarray(fractions),0.,1.)*self.total,arcs,parameters)
        index=np.minimum(value.astype(int),len(self.tables)-1);t=value-index;tt=t*t;ttt=tt*t
        basis=np.stack((2*ttt-3*tt+1,-2*ttt+3*tt,ttt-2*tt+t,ttt-tt),axis=1)
        return np.einsum('ni,nij->nj',basis,coefficients[index])

    def sample(self,fractions):return [self.at(u) for u in fractions]



class RailRegistry:
    """Sample each physical curve once, then share its topological identities.

    Logical junctions are mandatory anchors. Regular support vertices do
    not allocate rows. No RAM cache or per-zone length budget is involved.
    """
    def __init__(self,points,evaluate,rows,plans,reference_length=0.,max_samples=200000,local_density=None,point_density=None,previous=None,layout_lengths=None):
        self.points=points;self.evaluate=evaluate;self.rows=rows
        self.stations=defaultdict(dict);self.paths={};self.boundaries=[];self.complete_boundaries={};self.needs_stitch=False
        self.maps={};self.geometry_cache={};self.map_cache={}
        self.old_geometry=getattr(previous,'sampling_cache',{})
        self.old_maps=getattr(previous,'correspondence_cache',{})
        self.density={key:max(1.,min(8.,float(value))) for key,value in (local_density or {}).items()}
        chains=[]
        for path in evaluate.paths:
            if sum(length(sub(points[b],points[a])) for a,b in zip(path,path[1:]))>EPS:
                chains.append(self.curve(path))
        self.layout_lengths={edge:float(value) for edge,value in (layout_lengths or {}).items() if float(value)>EPS}
        for c in chains:
            for edge,(i,a,b) in c.edges.items():self.layout_lengths.setdefault(edge,c.tables[i][-1])
        self.reference_length=reference_length if reference_length>EPS else max((c.total for c in chains),default=1.)
        self.spacing=self.reference_length/rows
        from .density import DensityField
        self.point_field=DensityField(chains,point_density,self.reference_length)
        mandatory={v for p in plans for rail in (p.left,p.right) for v in (rail[0],rail[-1])}
        mandatory.update(v for v,neighbors in evaluate.adjacency.items() if len(neighbors)!=2)
        mandatory.update(a for (a,b),strength in getattr(evaluate,'smoothing',{}).items() if strength<1.)
        self.phases=self._phases(chains,plans,mandatory)
        allocated=0;sections=[]
        for curve in chains:
            stops=[(0.,curve.path[0])]
            stops.extend((curve.fraction(('v',v)),v) for v in curve.path[1:-1] if v in mandatory)
            stops.append((1.,curve.path[-1]))
            stops.sort()
            for (lo,first),(hi,last) in zip(stops,stops[1:]):
                phase,phase_difference,direction=self.phases[curve.path,first,last]
                desired=direction*(hi-lo)*curve.total/self.spacing
                span=phase_difference+math.floor(desired-phase_difference+.5)
                if span*desired<=0. or abs(span)<.2:span=desired
                ends=sorted((phase,phase+span))
                lower=math.ceil(ends[0]);upper=math.floor(ends[1])
                fractions=[lo+(k-phase)/span*(hi-lo) for k in range(lower,upper+1)]
                margin=.28*self.spacing/curve.total
                fractions=[lo]+sorted(u for u in fractions if lo+margin<u<hi-margin)+[hi]
                intervals=len(fractions)-1
                allocated+=intervals
                if allocated>2*max_samples:raise ValueError('Превышен лимит точек детализации')
                # A small curvature safeguard at low detail, independent of
                # support count. Straight parts keep their sparse spacing.
                def refine(a,b,depth=0):
                    middle=(a+b)*.5
                    pa=curve.at(a)[1];pb=curve.at(b)[1];pm=curve.at(middle)[1]
                    if depth<6 and length(sub(pm,mix(pa,pb,.5)))>self.spacing*.065:
                        return refine(a,middle,depth+1)[:-1]+refine(middle,b,depth+1)
                    return [a,b]
                adaptive=[]
                for a,b in zip(fractions,fractions[1:]):adaptive.extend(refine(a,b)[:-1])
                adaptive.append(hi)
                allocated+=len(adaptive)-len(fractions)
                if allocated>2*max_samples:raise ValueError('Превышен лимит точек детализации')
                sections.append([curve,first,last,adaptive])
                self._register_samples(curve,first,last,adaptive)
        self.stations.clear()
        for curve,first,last,adaptive in sections:
            if any(self.density.get(edge,1.)>1. for edge in curve.edges):
                # Preserve every original sample. Insert only inside
                # selected source-edge intervals, sharing their identities.
                expanded=[]
                for start,end in zip(adaptive,adaptive[1:]):
                    cuts=[start]
                    for index in range(1,len(curve.path)-1):
                        fraction=curve.offsets[index]/curve.total
                        before=self.density.get(edge_key(curve.path[index-1],curve.path[index]),1.)
                        after=self.density.get(edge_key(curve.path[index],curve.path[index+1]),1.)
                        if start+1e-10<fraction<end-1e-10 and abs(before-after)>1e-8:cuts.append(fraction)
                    cuts.append(end)
                    for a,b in zip(cuts,cuts[1:]):
                        edge_i=min(len(curve.path)-2,max(0,bisect.bisect_right(curve.offsets,(a+b)*.5*curve.total)-1))
                        factor=self.density.get(edge_key(curve.path[edge_i],curve.path[edge_i+1]),1.)
                        count=max(1,int(math.ceil(factor*(b-a)/(end-start)-1e-8)))
                        if allocated+len(expanded)+count+1-len(adaptive)>2*max_samples:raise ValueError('Превышен лимит локального уплотнения')
                        expanded.extend(a+(b-a)*i/count for i in range(count))
                expanded.append(adaptive[-1])
                allocated+=len(expanded)-len(adaptive)
                adaptive=expanded
                if allocated>2*max_samples:raise ValueError('Превышен лимит локального уплотнения')
            if self.point_field.affected(curve):
                from .density import redistribute
                refined=redistribute(adaptive,lambda u:u*curve.total,
                    lambda u:self.point_field.at(curve,u),self.point_field.profile(curve)[1],2*max_samples)
                allocated+=len(refined)-len(adaptive);adaptive=refined
                if allocated>2*max_samples:raise ValueError('Превышен лимит локального уплотнения')
            self._register_samples(curve,first,last,adaptive)

    def _register_samples(self,curve,first,last,fractions):
        samples=curve.sample(fractions)
        samples[0]=(('v',first),self.points[first]);samples[-1]=(('v',last),self.points[last])
        for key,pos in samples:
            if key[0]=='s':self.stations[(key[1],key[2])][key[3]]=(key,pos)
            else:
                vertex=key[1]
                for adjacent in self.evaluate.adjacency[vertex]:
                    edge=edge_key(vertex,adjacent);t=0. if vertex==edge[0] else 1.
                    self.stations[edge][t]=(key,pos)

    def _phases(self,chains,plans,mandatory):
        """Synchronize row phase modulo one, preserving unequal row counts.

        Longitudinal constraints follow physical length; transverse caps
        favor the same row phase. Junctions do not reset every chain to zero.
        """
        sections=[];edges=[];signs={}
        ordered=sorted(plans,key=lambda p:(p.settings.order<=0,p.settings.order,p.index))
        for c in chains:
            sign=1.
            for p in ordered:
                found=False
                for rail in (p.left,p.right):
                    for a,b in zip(rail,rail[1:]):
                        entry=c.edges.get(edge_key(a,b))
                        if entry:sign=1. if entry[1]==a else -1.;found=True;break
                    if found:break
                if found:break
            stops=[(0.,c.path[0])]+[(c.fraction(('v',v)),v) for v in c.path[1:-1] if v in mandatory]+[(1.,c.path[-1])]
            for (lo,a),(hi,b) in zip(stops,stops[1:]):
                i=c.vertices[a];j=c.vertices[b]
                size=sum(self.layout_lengths[edge_key(c.path[k],c.path[k+1])] for k in range(i,j))/self.spacing
                desired=sign*size
                sections.append((c.path,a,b,desired))
                edges.append((a,b,desired,1./max(.5,size)))
        pins=set()
        for p in plans:
            for end,(kind,i) in enumerate((p.start,p.end)):
                if kind=='edge':
                    a,b=p.boundary[i],p.boundary[(i+1)%len(p.boundary)];edges.append((a,b,0.,4.))
                    if end==0 and all(len(self.evaluate.adjacency[v])==1 for v in (a,b)):pins.update((a,b))
        vertices=sorted({v for a,b,d,w in edges for v in (a,b)});ids={v:i for i,v in enumerate(vertices)}
        phases={v:0. for v in vertices}
        if np is not None and edges:
            src=[];dst=[];rotation=[];weight=[]
            for a,b,desired,w in edges:
                r=complex(math.cos(2*math.pi*desired),math.sin(2*math.pi*desired))
                src.extend((ids[a],ids[b]));dst.extend((ids[b],ids[a]));rotation.extend((r.conjugate(),r));weight.extend((w,w))
            src=np.asarray(src);dst=np.asarray(dst);rotation=np.asarray(rotation);weight=np.asarray(weight)
            z=np.ones(len(vertices),dtype=complex);denom=np.maximum(EPS,np.bincount(src,weights=weight,minlength=len(vertices)))
            pinned=[ids[v] for v in pins]
            if not pinned:pinned=[0]
            for _ in range(32):
                value=z[dst]*rotation*weight
                mean=(np.bincount(src,weights=value.real,minlength=len(vertices))+1j*np.bincount(src,weights=value.imag,minlength=len(vertices)))/denom
                z=.35*z+.65*mean;z/=np.maximum(EPS,np.abs(z));z[pinned]=1.
            phases={v:float(np.angle(z[ids[v]])/(2*math.pi)) for v in vertices}
        result={}
        for path,a,b,desired in sections:
            start=phases[a];difference=phases[b]-start
            result[path,a,b]=(start,difference,1. if desired>=0 else -1.)
        return result

    def curve(self,path):
        path=tuple(path)
        if path not in self.paths:
            coefficients=getattr(self.evaluate,'coefficients',{})
            signature=(tuple(self.points[v] for v in path),
                tuple((coefficients.get((a,b)),coefficients.get((b,a))) for a,b in zip(path,path[1:])))
            old=self.old_geometry.get(path)
            cached=old[1] if coefficients and old and old[0]==signature else None
            curve=CurvePath(self.points,path,self.evaluate,cached)
            curve.geometry_signature=signature
            self.paths[path]=curve
            self.geometry_cache[path]=(signature,(curve.tables,curve.offsets,curve.total))
        return self.paths[path]

    def boundary(self,curve):
        if curve.path in self.complete_boundaries:return self.complete_boundaries[curve.path]
        samples={('v',curve.path[0]):self.points[curve.path[0]],
                 ('v',curve.path[-1]):self.points[curve.path[-1]]}
        for edge in curve.edges:
            for key,pos in self.stations[edge].values():
                if curve.parameter(key) is not None:samples[key]=pos
        ordered=sorted(samples.items(),key=lambda item:curve.parameter(item[0]))
        self.complete_boundaries[curve.path]=ordered
        return ordered

    def pair(self,left,right,reference=True):
        from .ribbon import RibbonMap
        a=self.curve(left);b=self.curve(right)
        left_samples=self.boundary(a);right_samples=self.boundary(b)
        key=(a.path,b.path);signature=(a.geometry_signature,b.geometry_signature)
        old=self.old_maps.get(key)
        if key in self.maps:mapping=self.maps[key]
        elif old and old[0]==signature:
            mapping=RibbonMap.__new__(RibbonMap)
            mapping.__dict__.update(old[1]);mapping.a=a;mapping.b=b;mapping._samples={}
        else:mapping=RibbonMap(a,b)
        self.maps[key]=mapping
        self.map_cache[key]=(signature,{k:v for k,v in mapping.__dict__.items() if k not in ('a','b','_samples')})
        if not reference:return a,b,left_samples,right_samples,(),(),()
        fa=[mapping.fraction(a,key) for key,pos in left_samples]
        fb=[mapping.fraction(b,key) for key,pos in right_samples]
        count=max(1,int(((len(fa)-1)+(len(fb)-1))*.5+.5))
        def station(values,u):
            x=u*(len(values)-1);i=min(len(values)-2,int(x));t=x-i
            return values[i]*(1.-t)+values[i+1]*t
        fractions=[(station(fa,i/count)+station(fb,i/count))*.5 for i in range(count+1)]
        fractions[0]=0.;fractions[-1]=1.
        def body(curve,samples):
            if len(samples)==count+1:return samples
            arc=[curve.fraction(key) for key,pos in samples]
            return [curve.at(station(arc,i/count)) for i in range(count+1)]
        return a,b,left_samples,right_samples,body(a,left_samples),body(b,right_samples),fractions

    def flows(self,a,b,curve,samples):
        mapping=self.maps[a.path,b.path]
        return [mapping.fraction(curve,key) for key,pos in samples]

    def register(self,curve,samples):
        complete=self.complete_boundaries.get(curve.path)
        if complete is not samples and (complete is None or tuple(k for k,p in complete)!=tuple(k for k,p in samples)):
            self.needs_stitch=True
        self.boundaries.append((curve,samples))

    def normal(self,a,b,u,left,right):
        return self.maps[a.path,b.path].normal(u,left,right)

    def stitch(self,vertices,faces,owners,index):
        """Insert missing samples only into cells bordering the changed seam."""
        if not self.needs_stitch:return faces,owners
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
