"""Shared transverse tangents; guide positions remain interpolation anchors."""
import math,bisect
from collections import defaultdict
from .core import EPS,add,sub,mul,dot,length,edge_key,_plan_priority

_MIN_ALIGNMENT=math.cos(math.radians(135.))


def weight(v,automatic=False):
    sine=math.sin(math.pi*v)
    return sine*sine if automatic else sine


def offset(deltas,v):
    return add(mul(deltas[0],v*(1.-v)**2),mul(deltas[1],-v*v*(1.-v)))


class _Station:
    def __init__(self,curve,opposites):
        self.curve=curve;self.opposites=opposites;self.cache={}
        self.signature=(curve.geometry_signature,tuple((shared.geometry_signature,other.geometry_signature) for shared,other in opposites))
    def tangent(self,u):
        u=round(u,12)
        if u not in self.cache:
            key,center=self.curve.at(u)
            one,two=[other.at(shared.fraction(key))[1] for shared,other in self.opposites]
            first,second=sub(center,one),sub(two,center);a,b=length(first),length(second)
            self.cache[u]=mul(sub(two,one),1./(a+b)) if min(a,b)>EPS and dot(first,second)>_MIN_ALIGNMENT*a*b else None
        return self.cache[u]


class _Border:
    def __init__(self,curve,nodes):
        self.curve=curve;self.nodes=[nodes.get(edge_key(a,b)) for a,b in zip(curve.path,curve.path[1:])]
        self.signature=tuple(node.signature if node else None for node in self.nodes)
    def tangent(self,u):
        distance=max(0.,min(1.,u))*self.curve.total
        i=max(0,min(len(self.nodes)-1,bisect.bisect_right(self.curve.offsets,distance)-1));node=self.nodes[i]
        if node is None:return None
        local=(distance-self.curve.offsets[i])/max(EPS,self.curve.offsets[i+1]-self.curve.offsets[i])
        return node.tangent(local if self.curve.path[i]<self.curve.path[i+1] else 1.-local)


class Profile:
    def __init__(self,curves,nodes):
        self.curves=curves;self.borders=tuple(_Border(curve,nodes) for curve in curves)
        self.signature=tuple(border.signature for border in self.borders)
    def deltas(self,ua,ub,a,b):
        chord=sub(b,a);size=length(chord);tangents=[]
        for border,u in zip(self.borders,(ua,ub)):
            tangent=border.tangent(u)
            tangents.append(mul(tangent,-size if dot(tangent,chord)<0. else size) if tangent is not None else None)
        if tangents==[None,None]:return ((0.,0.,0.),)*2
        one,two=tangents
        if one is None:one=mul(sub(mul(chord,3.),two),.5)
        if two is None:two=mul(sub(mul(chord,3.),one),.5)
        return sub(one,chord),sub(two,chord)
    def end(self,k):
        return self.deltas(k,k,self.curves[0].at(k)[1],self.curves[1].at(k)[1])


class Registry:
    def __init__(self,plans,registry):
        owners=defaultdict(list);self.profiles={};self.smooth_edges=set();nodes={}
        if not any(plan.settings.auto_curve for plan in plans):return
        for plan in sorted(plans,key=_plan_priority):
            for side,path in enumerate((plan.left,plan.right)):
                for one,two in zip(path,path[1:]):owners[edge_key(one,two)].append((plan,side))
        for edge,group in owners.items():
            if len(group)!=2 or not all(plan.settings.auto_curve for plan,side in group):continue
            opposites=[]
            for plan,side in group:
                rails=(plan.left,plan.right);opposites.append((registry.curve(rails[side]),registry.curve(rails[1-side])))
            nodes[edge]=_Station(registry.curve(edge),opposites)
            if group[0][0].settings.normal_flip==group[1][0].settings.normal_flip:
                if nodes[edge].tangent(.5) is not None:self.smooth_edges.add(edge)
        for plan in plans:
            if plan.settings.auto_curve:
                paths=(plan.left,plan.right)
                self.profiles[plan.index]=Profile(tuple(registry.curve(path) for path in paths),nodes)
    def get(self,plan):return self.profiles.get(plan.index)
    def __bool__(self):return bool(self.profiles)
    def signature(self,plan):
        profile=self.get(plan)
        return profile.signature if profile else None
