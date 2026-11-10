"""Compact, smooth point density measured along the physical guide graph."""
import bisect
import heapq
import math
from .core import EPS


class DensityField:
    def __init__(self,curves,points,reference_length):
        self.sources=[]
        graph={}
        for curve in curves:
            for i,(a,b) in enumerate(zip(curve.path,curve.path[1:])):
                size=curve.tables[i][-1]
                graph.setdefault(a,{})[b]=size;graph.setdefault(b,{})[a]=size
        for vertex,(factor,radius) in (points or {}).items():
            radius=max(EPS,float(radius)*reference_length)
            factor=max(1.,min(8.,float(factor)))
            if factor<=1. or vertex not in graph:continue
            distances={vertex:0.};queue=[(0.,vertex)]
            while queue:
                distance,node=heapq.heappop(queue)
                if distance!=distances[node]:continue
                for neighbor,size in graph[node].items():
                    candidate=distance+size
                    if candidate<radius and candidate<distances.get(neighbor,float('inf')):
                        distances[neighbor]=candidate;heapq.heappush(queue,(candidate,neighbor))
            self.sources.append((factor,radius,distances))
        self.cache={}

    def profile(self,curve):
        if curve.path not in self.cache:
            entries=[];knots=set()
            for factor,radius,distances in self.sources:
                for i,(a,b) in enumerate(zip(curve.path,curve.path[1:])):
                    size=curve.tables[i][-1];da=distances.get(a,float('inf'));db=distances.get(b,float('inf'))
                    if min(da,db)>=radius:continue
                    entries.append((i,factor,radius,da,db,size))
                    # Resolve even a very narrow footprint, independently of
                    # the polygon count and of the number of source supports.
                    for start,end in ((0.,min(size,radius-da)),(max(0.,size-radius+db),size)):
                        if end<=start:continue
                        knots.update((curve.offsets[i]+start+(end-start)*j/16)/curve.total for j in range(17))
            self.cache[curve.path]=(entries,sorted(knots))
        return self.cache[curve.path]

    def affected(self,curve):return bool(self.profile(curve)[0])

    def at(self,curve,fraction):
        entries,_=self.profile(curve)
        if not entries:return 1.
        distance=max(0.,min(1.,fraction))*curve.total
        i=min(len(curve.path)-2,max(0,bisect.bisect_right(curve.offsets,distance)-1))
        x=distance-curve.offsets[i];value=1.
        for index,factor,radius,da,db,size in entries:
            if index!=i:continue
            q=min(da+x,db+size-x)/radius
            if q<1.:value=max(value,1.+(factor-1.)*(1.-q*q)**3)
        return value


def redistribute(base,arc_at,factor_at,knots=(),max_samples=400000):
    """Integrate a continuous density; retain the original grid outside it.

    Connected affected cells are sampled at equal increments of weighted
    length. Integer topology changes remain necessary, but there is no
    ceil-per-cell doubling or step in the underlying density field.
    """
    nodes=set(base)
    nodes.update(t for t in knots if base[0]<t<base[-1])
    for a,b in zip(base,base[1:]):nodes.update(a+(b-a)*j/4 for j in (1,2,3))
    nodes=sorted(nodes);weights=[factor_at(t) for t in nodes]
    arcs=[arc_at(t) for t in nodes];mass=[0.]
    for i in range(1,len(nodes)):
        mass.append(mass[-1]+(arcs[i]-arcs[i-1])*(weights[i]+weights[i-1])*.5)
    bounds=[bisect.bisect_left(nodes,t) for t in base]
    active=[max(weights[a:b+1])>1.+1e-7 for a,b in zip(bounds,bounds[1:])]
    output=[base[0]];i=0
    while i<len(active):
        if not active[i]:output.append(base[i+1]);i+=1;continue
        start=i
        while i<len(active) and active[i]:i+=1
        lo,hi=bounds[start],bounds[i]
        distance=arcs[hi]-arcs[lo];total=mass[hi]-mass[lo]
        count=max(i-start,int((i-start)*total/max(EPS,distance)+.5))
        if len(output)+count>max_samples:raise ValueError('Превышен лимит локального уплотнения')
        for j in range(1,count):
            target=mass[lo]+total*j/count
            k=min(hi,max(lo+1,bisect.bisect_left(mass,target,lo,hi+1)))
            # Invert the integral of a linearly varying density, rather than
            # interpolating the CDF and introducing little spacing plateaus.
            delta=arcs[k]-arcs[k-1];slope=(weights[k]-weights[k-1])/max(EPS,delta)
            value=target-mass[k-1]
            root=math.sqrt(max(0.,weights[k-1]**2+2*slope*value))
            step=2*value/max(EPS,weights[k-1]+root)
            t=max(0.,min(1.,step/max(EPS,delta)))
            output.append(nodes[k-1]*(1.-t)+nodes[k]*t)
        output.append(base[i])
    return output
