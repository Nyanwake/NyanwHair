"""Monotone transverse correspondence between two curved ribbon boundaries."""
import bisect
import math
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
        self.a=a;self.b=b;self._samples={}
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
                # Squared projection needs no normalized temporary vector
                # or square root at every cell of the correspondence table.
                dx=bb[j][0]-aa[i][0];dy=bb[j][1]-aa[i][1];dz=bb[j][2]-aa[i][2]
                tx=ta[i][0]+tb[j][0];ty=ta[i][1]+tb[j][1];tz=ta[i][2]+tb[j][2]
                square=tx*tx+ty*ty+tz*tz
                projection=dx*tx+dy*ty+dz*tz
                local=dx*dx+dy*dy+dz*dz+(4*projection*projection/square if square>EPS*EPS else 0.)
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
        turn=sum(math.acos(max(-1.,min(1.,dot(p,q)))) for tangent in (ta,tb) for p,q in zip(tangent,tangent[1:]))*.5
        widths=[norm(sub(b.at(v)[1],p)) for p,v in zip(aa,vs)]
        twist=sum(math.acos(max(-1.,min(1.,dot(p,q)))) for p,q in zip(widths,widths[1:]) if length(p)>EPS and length(q)>EPS)
        self.strong_turn=turn>1.6 or twist>1.6
        centers=[mul(add(p,b.at(v)[1]),.5) for p,v in zip(aa,vs)]
        distance=[0.]
        for p,q in zip(centers,centers[1:]):distance.append(distance[-1]+max(EPS,length(sub(q,p))))
        self.total=distance[-1]
        flow=[v/self.total for v in distance]
        self.u_to_flow=MonotoneMap(us,flow);self.flow_to_u=MonotoneMap(flow,us)

    def at(self,flow):
        if flow not in self._samples:
            u=self.flow_to_u(flow)
            self._samples[flow]=(self.a.at(u),self.b.at(self.u_to_v(u)))
        return self._samples[flow]

    def fraction(self,curve,key):
        u=curve.fraction(key)
        if curve is self.b:u=self.v_to_u(u)
        return self.u_to_flow(u)

    def normal(self,flow,left,right):
        lo=self.at(max(0.,flow-.001));hi=self.at(min(1.,flow+.001))
        tangent=sub(add(hi[0][1],hi[1][1]),add(lo[0][1],lo[1][1]))
        return norm(cross(sub(right,left),tangent))


def zipper_flows(a,b):
    """Use the minimum possible triangles: exactly abs(interval counts).

    Equal counts are always a quad strip. For unequal counts only the
    longer side may advance alone; dynamic alignment places those necessary
    transitions near their matching growth coordinate.
    """
    swap=len(a)<len(b)
    long,short=(b,a) if swap else (a,b)
    na=len(long)-1;nb=len(short)-1;extra=na-nb
    if extra==0:return [(i,i) for i in range(na+1)]
    if (nb+1)*(extra+1)>400000:
        # Bounded-memory fallback for extreme detail or count imbalance.
        j=k=0;path=[(0,0)]
        while j<nb or k<extra:
            i=j+k
            if j==nb or k<extra and abs(long[i+1]-short[j])<abs(long[i+1]-short[j+1]):k+=1
            else:j+=1
            path.append((j+k,j))
    else:
        width=extra+1;previous=bytearray((nb+1)*width)
        before=[float('inf')]*width
        for j in range(nb+1):
            current=[float('inf')]*width
            for k in range(width):
                if j==k==0:current[k]=0.;continue
                error=(long[j+k]-short[j])**2
                diagonal=before[k] if j else float('inf')
                triangle=current[k-1] if k else float('inf')
                if diagonal<=triangle:current[k]=diagonal+error;previous[j*width+k]=1
                else:current[k]=triangle+error;previous[j*width+k]=2
            before=current
        j=nb;k=extra;path=[(na,nb)]
        while j or k:
            if previous[j*width+k]==1:j-=1
            else:k-=1
            path.append((j+k,j))
        path.reverse()
    return [(j,i) for i,j in path] if swap else path
