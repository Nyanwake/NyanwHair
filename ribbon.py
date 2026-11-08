"""Monotone transverse correspondence between two curved ribbon boundaries."""
import bisect
from .core import EPS,add,sub,mul,dot,length,norm,cross


class MonotoneMap:
    def __init__(self,x,y):
        self.x=x;self.y=y
        h=[b-a for a,b in zip(x,x[1:])]
        s=[(b-a)/max(EPS,step) for a,b,step in zip(y,y[1:],h)]
        self.d=[s[0]]
        for i in range(1,len(x)-1):
            if s[i-1]*s[i]<=0:self.d.append(0.)
            else:
                a=2*h[i]+h[i-1];b=h[i]+2*h[i-1]
                self.d.append((a+b)/(a/s[i-1]+b/s[i]))
        self.d.append(s[-1])
    def __call__(self,t):
        if t<=self.x[0]:return self.y[0]
        if t>=self.x[-1]:return self.y[-1]
        i=bisect.bisect_right(self.x,t)-1;h=self.x[i+1]-self.x[i];u=(t-self.x[i])/h
        u2=u*u;u3=u2*u
        return ((2*u3-3*u2+1)*self.y[i]+(u3-2*u2+u)*h*self.d[i]+
                (-2*u3+3*u2)*self.y[i+1]+(u3-u2)*h*self.d[i+1])


class RibbonMap:
    def __init__(self,a,b):
        self.a=a;self.b=b
        n=max(24,min(64,8*max(len(a.path),len(b.path))))
        us=[i/n for i in range(n+1)]
        aa=[a.at(u)[1] for u in us];bb=[b.at(u)[1] for u in us]
        ta=[norm(sub(aa[min(n,i+1)],aa[max(0,i-1)])) for i in range(n+1)]
        tb=[norm(sub(bb[min(n,i+1)],bb[max(0,i-1)])) for i in range(n+1)]
        # Dynamic monotone matching favors short, transverse cross-sections.
        # This map is independent of the requested output polygon count.
        size=n+1;cost=[float('inf')]*(size*size);previous=bytearray(size*size)
        cost[0]=0.
        for i in range(size):
            for j in range(size):
                if i==j==0:continue
                width=sub(bb[j],aa[i]);tangent=norm(add(ta[i],tb[j]))
                local=dot(width,width)+4*dot(width,tangent)**2
                candidates=[]
                if i and j:candidates.append((cost[(i-1)*size+j-1]+local,1))
                if i:candidates.append((cost[(i-1)*size+j]+local*.55,2))
                if j:candidates.append((cost[i*size+j-1]+local*.55,3))
                value,step=min(candidates);cost[i*size+j]=value;previous[i*size+j]=step
        matches=[[] for _ in us];i=j=n
        while True:
            matches[i].append(j/n)
            if i==j==0:break
            step=previous[i*size+j]
            if step in (1,2):i-=1
            if step in (1,3):j-=1
        vs=[sum(group)/len(group) for group in matches];vs[0]=0.;vs[-1]=1.
        # Remove staircase noise before using the match to construct geometry.
        for _ in range(5):vs=[0.]+[(vs[k-1]+2*vs[k]+vs[k+1])/4 for k in range(1,n)]+[1.]
        for k in range(1,n):vs[k]=max(vs[k],vs[k-1]+1e-7)
        for k in range(n-1,0,-1):vs[k]=min(vs[k],vs[k+1]-1e-7)
        self.u_to_v=MonotoneMap(us,vs);self.v_to_u=MonotoneMap(vs,us)
        centers=[mul(add(p,b.at(v)[1]),.5) for p,v in zip(aa,vs)]
        distance=[0.]
        for p,q in zip(centers,centers[1:]):distance.append(distance[-1]+max(EPS,length(sub(q,p))))
        self.total=distance[-1]
        flow=[v/self.total for v in distance]
        self.u_to_flow=MonotoneMap(us,flow);self.flow_to_u=MonotoneMap(flow,us)

    def at(self,flow):
        u=self.flow_to_u(flow)
        return self.a.at(u),self.b.at(self.u_to_v(u))

    def fraction(self,curve,key):
        u=curve.fraction(key)
        if curve is self.b:u=self.v_to_u(u)
        return self.u_to_flow(u)

    def normal(self,flow,left,right):
        lo=self.at(max(0.,flow-.001));hi=self.at(min(1.,flow+.001))
        tangent=sub(add(hi[0][1],hi[1][1]),add(lo[0][1],lo[1][1]))
        return norm(cross(sub(right,left),tangent))


def zipper_flows(a,b):
    i=j=0;path=[(i,j)]
    while i<len(a)-1 or j<len(b)-1:
        if i==len(a)-1:j+=1
        elif j==len(b)-1:i+=1
        else:
            step=min(a[i+1]-a[i],b[j+1]-b[j])
            if abs(a[i+1]-b[j+1])<=.5*step+1e-9:i+=1;j+=1
            elif a[i+1]<b[j+1]:i+=1
            else:j+=1
        path.append((i,j))
    return path
