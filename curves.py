"""Chord-parameterized cubic spline network, independent of mesh sampling."""
from collections import defaultdict
from .core import EPS,add,sub,mul,dot,length,norm,edge_key,_plan_priority


def _tridiagonal(lower,diagonal,upper,rhs):
    diagonal=list(diagonal);rhs=list(rhs)
    for i in range(1,len(diagonal)):
        gain=lower[i]/diagonal[i-1]
        diagonal[i]-=gain*upper[i-1]
        rhs[i]=sub(rhs[i],mul(rhs[i-1],gain))
    result=[(0.,0.,0.)]*len(diagonal)
    result[-1]=mul(rhs[-1],1./diagonal[-1])
    for i in range(len(diagonal)-2,-1,-1):
        result[i]=mul(sub(rhs[i],mul(result[i+1],upper[i])),1./diagonal[i])
    return result


def _spline(points,start,end):
    """Second derivatives of a clamped C2 spline in physical chord time."""
    h=[max(EPS,length(sub(b,a))) for a,b in zip(points,points[1:])]
    sec=[mul(sub(b,a),1./step) for a,b,step in zip(points,points[1:],h)]
    n=len(points);lo=[0.]*n;diag=[0.]*n;up=[0.]*n;rhs=[(0.,0.,0.)]*n
    diag[0]=2*h[0];up[0]=h[0];rhs[0]=mul(sub(sec[0],start),6.)
    lo[-1]=h[-1];diag[-1]=2*h[-1];rhs[-1]=mul(sub(end,sec[-1]),6.)
    for i in range(1,n-1):
        lo[i]=h[i-1];diag[i]=2*(h[i-1]+h[i]);up[i]=h[i]
        rhs[i]=mul(sub(sec[i],sec[i-1]),6.)
    second=_tridiagonal(lo,diag,up,rhs)
    derivatives=[sub(sec[i],mul(add(mul(second[i],2.),second[i+1]),h[i]/6.)) for i in range(n-1)]
    derivatives.append(add(sec[-1],mul(add(second[-2],mul(second[-1],2.)),h[-1]/6.)))
    return h,derivatives


class CurveNetwork:
    def __init__(self,points,plans):
        self.points=points;self.edges=set();self.continuations={};self.paths=[]
        candidates=[];priorities={}
        for plan in sorted(plans,key=_plan_priority):
            priority=_plan_priority(plan)
            for rail in (plan.left,plan.right):
                for a,b in zip(rail,rail[1:]):
                    edge=edge_key(a,b);self.edges.add(edge);priorities.setdefault(edge,priority)
                for a,node,b in zip(rail,rail[1:],rail[2:]):
                    candidates.append((priority,node,*sorted((a,b))))
        # End caps are not votes to break a through-curve. At a true fork,
        # keep the oldest explicit through-pair and attach other branches.
        for _,node,a,b in sorted(set(candidates)):
            if (node,a) not in self.continuations and (node,b) not in self.continuations:
                self.continuations[node,a]=b;self.continuations[node,b]=a
        visited=set()
        starts=sorted(((priorities[edge],a,b) for edge in self.edges for a,b in (edge,edge[::-1])
                       if (a,b) not in self.continuations))
        starts+=sorted((priorities[edge],*edge) for edge in self.edges)
        for _,a,b in starts:
            if edge_key(a,b) in visited:continue
            path=[a]
            while edge_key(a,b) not in visited:
                visited.add(edge_key(a,b));path.append(b)
                nxt=self.continuations.get((b,a))
                if nxt is None:break
                a,b=b,nxt
            self.paths.append(tuple(path))
        self.through=defaultdict(list)
        self.adjacency=defaultdict(set)
        for a,b in self.edges:self.adjacency[a].add(b);self.adjacency[b].add(a)
        for ci,path in enumerate(self.paths):
            for i in range(1,len(path)-1):self.through[path[i]].append((ci,i))
        self.coefficients={};self.derivatives={};self._solving=set()
        for ci in range(len(self.paths)):self._solve(ci)

    def _boundary(self,ci,first):
        path=self.paths[ci];node=path[0 if first else -1]
        chord=sub(self.points[path[1]],self.points[node]) if first else sub(self.points[node],self.points[path[-2]])
        for parent,index in self.through.get(node,()):
            if parent==ci:continue
            if parent in self._solving:
                p=self.paths[parent]
                tangent=norm(add(norm(sub(self.points[node],self.points[p[index-1]])),
                                 norm(sub(self.points[p[index+1]],self.points[node]))))
            else:
                self._solve(parent);tangent=self.derivatives[parent][index]
            return mul(tangent,-1. if dot(tangent,chord)<0 else 1.)
        return (0.,0.,0.)

    def _solve(self,ci):
        if ci in self.derivatives:return
        self._solving.add(ci);path=self.paths[ci]
        start=self._boundary(ci,True);end=self._boundary(ci,False)
        # Closed guide loops are cut at their canonical seam. Matching
        # endpoint directions keep the seam geometric-continuous.
        if path[0]==path[-1]:
            start=end=norm(add(norm(sub(self.points[path[0]],self.points[path[-2]])),
                               norm(sub(self.points[path[1]],self.points[path[0]]))))
        h,d=_spline([self.points[v] for v in path],start,end)
        self.derivatives[ci]=d
        for i,(a,b) in enumerate(zip(path,path[1:])):
            self.coefficients[a,b]=(self.points[a],self.points[b],mul(d[i],h[i]),mul(d[i+1],h[i]))
        self._solving.remove(ci)

    def __call__(self,a,b,t):
        data=self.coefficients.get((a,b))
        if data is None:
            data=self.coefficients.get((b,a));t=1.-t
        if data is None:raise ValueError('Ребро не принадлежит направляющей')
        p0,p1,m0,m1=data
        tt=t*t;ttt=tt*t
        return add(add(mul(p0,2*ttt-3*tt+1),mul(p1,-2*ttt+3*tt)),
                   add(mul(m0,ttt-2*tt+t),mul(m1,ttt-tt)))
