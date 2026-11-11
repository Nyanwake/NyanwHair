"""One sine-profile offset per segment, from its side neighbours."""
import math
from collections import defaultdict
from dataclasses import replace
from .core import EPS,sub,mul,dot,length,norm,edge_key,_plan_priority


def rounded(points,plans,face_owners,smoothing):
    rails=defaultdict(list);across={};sums=defaultdict(float);weights=defaultdict(float);smooth=set()
    by_id={p.index:p for p in plans}
    for plan in plans:
        def straight(cap):
            kind,i=cap
            if kind!='edge':return False
            a,b=plan.boundary[i],plan.boundary[(i+1)%len(plan.boundary)]
            return smoothing.get((a,b),1.)==smoothing.get((b,a),1.)==0.
        if straight(plan.start) and straight(plan.end):continue
        centers=[mul(tuple(map(sum,zip(*(points[v] for v in path)))),1./len(path)) for path in (plan.left,plan.right)]
        direction=sub(centers[1],centers[0]);across[plan.index]=norm(sub(direction,mul(plan.normal,dot(direction,plan.normal))))
        for side,path in enumerate((plan.left,plan.right)):
            for one,two in zip(path,path[1:]):rails[edge_key(one,two)].append((plan,side))
    for edge,owners in rails.items():
        if len(owners)>2:
            primary=sorted(face_owners[edge],key=lambda i:_plan_priority(by_id[i]))[:2]
            owners=[owner for owner in owners if owner[0].index in primary]
        if len(owners)!=2:continue
        (one,left),(two,right)=owners;cosine=max(-1.,min(1.,dot(one.normal,two.normal)))
        if cosine<=1e-5:continue
        divisor=math.pi*(1.+cosine)
        size=length(sub(points[edge[1]],points[edge[0]]))
        for plan,side,other in ((one,left,two),(two,right,one)):
            value=dot(other.normal,across[plan.index])*(1. if side else -1.)/divisor
            sums[plan.index]+=value*size;weights[plan.index]+=size
        if len(face_owners[edge])==2 and one.settings.normal_flip==two.settings.normal_flip:smooth.add(edge)
    return [replace(p,curvature=sums[p.index]/weights[p.index]) if weights[p.index]>EPS else p for p in plans],smooth
