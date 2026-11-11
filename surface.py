"""Flow-aligned columns for curled and locally refined ribbon patches."""
from .i18n import tr
import bisect,math
try:
    import numpy as np
except ImportError:
    np=None
from .core import EPS,add,sub,mul,dot,cross,length,norm,mix,edge_key,bias_cross_position
from .ribbon import zipper_flows


def _rotate(vector,old,new):
    axis=cross(old,new);sine=length(axis);cosine=max(-1.,min(1.,dot(old,new)))
    if sine<EPS:return vector
    axis=mul(axis,1./sine)
    return add(add(mul(vector,cosine),mul(cross(axis,vector),sine)),mul(axis,dot(axis,vector)*(1.-cosine)))


class CapBlend:
    """Bring the interior profile smoothly onto the shared end curves."""
    def __init__(self,plan,ends,normals,profiles):
        self.bias=plan.settings.bias;self.ends=[];self.cache={}
        for (a,b),normal,profile in zip(ends,normals,profiles):
            amplitude=plan.settings.bulge*length(sub(b,a))*(-1. if plan.settings.flip else 1.)
            self.ends.append((a,b,mul(normal,amplitude),profile) if profile else None)
    @classmethod
    def from_profiles(cls,plan,ends,normals,profiles):
        aligned=[]
        for k,(kind,i) in enumerate((plan.start,plan.end)):
            if kind!='edge':aligned.append(None);continue
            edge=edge_key(plan.boundary[i],plan.boundary[(i+1)%len(plan.boundary)])
            profile=profiles[edge]
            aligned.append(profile if plan.left[-1 if k else 0]==edge[0] else profile[::-1])
        return cls(plan,ends,normals,aligned)
    def column(self,t):
        if t not in self.cache:
            v=bias_cross_position(t,self.bias);sine=math.sin(math.pi*v);shifts=[]
            for end in self.ends:
                if end is None:shifts.append(None);continue
                a,b,normal,profile=end;x=t*(len(profile)-1);i=min(len(profile)-2,int(x))
                wanted=mix(profile[i],profile[i+1],x-i)
                delta=sub(wanted,add(mix(a,b,v),mul(normal,sine)))
                shifts.append(delta if length(delta)>EPS else None)
            self.cache[t]=tuple(shifts)
        return self.cache[t]
    def weights(self,u):
        if .25<=u<=.75:return (0.,0.)
        a=max(0.,min(1.,u*4.));b=max(0.,min(1.,(1.-u)*4.))
        return (1.-a*a*(3.-2.*a) if self.ends[0] is not None and u<.25 else 0.,
                1.-b*b*(3.-2.*b) if self.ends[1] is not None and u>.75 else 0.)
    def apply(self,point,t,weights):
        if not any(weights):return point
        for shift,weight in zip(self.column(t),weights):
            if shift is not None and weight:point=add(point,mul(shift,weight))
        return point


class FlowSurface:
    def __init__(self,plan,width,mapping,cap_profiles,cap_keys=None):
        self.plan=plan;self.width=width;self.mapping=mapping;self.caps={};self._samples={};self.cap_keys=cap_keys or {}
        for kind,i in (plan.start,plan.end):
            if kind=='edge':
                edge=edge_key(plan.boundary[i],plan.boundary[(i+1)%len(plan.boundary)]);self.caps[edge]=cap_profiles[edge]
        self.n=max(64,min(192,16*max(len(mapping.a.path),len(mapping.b.path))))
        self.u=[i/self.n for i in range(self.n+1)]
        if np is None:pairs=[mapping.at(u) for u in self.u]
        else:
            a=mapping.flow_to_u.many(self.u);b=mapping.u_to_v.many(a)
            pairs=[((None,tuple(p)),(None,tuple(q))) for p,q in zip(mapping.a.positions(a).tolist(),mapping.b.positions(b).tolist())]
        centers=[mul(add(a[1],b[1]),.5) for a,b in pairs]
        tangents=[norm(sub(centers[min(self.n,i+1)],centers[max(0,i-1)])) for i in range(self.n+1)]
        self.normals=[]
        previous=plan.normal;old=tangents[0]
        for i,(a,b) in enumerate(pairs):
            transported=_rotate(previous,old,tangents[i])
            normal=norm(cross(sub(b[1],a[1]),tangents[i]))
            if length(normal)<EPS:normal=transported
            if dot(normal,transported)<0:normal=mul(normal,-1.)
            self.normals.append(normal);previous=normal;old=tangents[i]
        ends=[tuple(p[1] for p in pairs[i]) for i in (0,-1)]
        self.cap_blend=CapBlend.from_profiles(plan,ends,(self.normals[0],self.normals[-1]),cap_profiles)

    def prime(self,values,x):
        values=list(dict.fromkeys(float(u) for u in values if (float(u),x) not in self._samples))
        if not values:return
        if np is None or x in (0,self.width) or len(values)<4:
            for u in values:self.at(u,x)
            return
        interior=[u for u in values if 0.<u<1.]
        for u in values:
            if not 0.<u<1.:self.at(u,x)
        if not interior:return
        u=np.asarray(interior);a=self.mapping.flow_to_u.many(u);b=self.mapping.u_to_v.many(a)
        pa=self.mapping.a.positions(a);pb=self.mapping.b.positions(b)
        index=np.minimum(self.n-1,(u*self.n).astype(int));t=u*self.n-index;normals=np.asarray(self.normals)
        normal=normals[index]*(1.-t[:,None])+normals[index+1]*t[:,None]
        normal/=np.maximum(EPS,np.linalg.norm(normal,axis=1))[:,None]
        v=bias_cross_position(x/self.width,self.plan.settings.bias)
        amount=self.plan.settings.bulge*np.linalg.norm(pb-pa,axis=1)*math.sin(math.pi*v)
        if self.plan.settings.flip:amount=-amount
        positions=pa*(1.-v)+pb*v+normal*amount[:,None]
        for end,shift in enumerate(self.cap_blend.column(x/self.width)):
            if shift is not None:
                t=np.clip((u if end==0 else 1.-u)/.25,0.,1.);weight=1.-t*t*(3.-2.*t)
                positions+=weight[:,None]*np.asarray(shift)
        self._samples.update(((value,x),(None,tuple(pos))) for value,pos in zip(interior,positions.tolist()))

    def at(self,u,x):
        key=(u,x)
        if key not in self._samples:self._samples[key]=self._at(u,x)
        return self._samples[key]

    def _at(self,u,x):
        (ka,a),(kb,b)=self.mapping.at(u)
        if x==0:return ka,a
        if x==self.width:return kb,b
        if ka==kb:return ka,a
        v=bias_cross_position(x/self.width,self.plan.settings.bias)
        t=min(self.n-1,int(u*self.n));blend=u*self.n-t
        normal=norm(mix(self.normals[t],self.normals[t+1],blend))
        amplitude=self.plan.settings.bulge*length(sub(b,a))*math.sin(math.pi*v)
        if self.plan.settings.flip:amplitude=-amplitude
        pos=add(mix(a,b,v),mul(normal,amplitude))
        pos=self.cap_blend.apply(pos,x/self.width,self.cap_blend.weights(u))
        if u==0. or u==1.:
            end=u==1.;cap=self.plan.end if end else self.plan.start
            if cap[0]=='edge':
                i=cap[1];lo,hi=edge_key(self.plan.boundary[i],self.plan.boundary[(i+1)%len(self.plan.boundary)])
                a_v=self.plan.left[-1 if end else 0];aligned=x if a_v==lo else self.width-x
                keys=self.cap_keys.get((lo,hi))
                return (keys[aligned] if keys is not None else ('cap',lo,hi,aligned,self.width)),self.caps[lo,hi][aligned]
        return None,pos


def _density(curve,fraction,values):
    i=min(len(curve.path)-2,max(0,bisect.bisect_right(curve.offsets,fraction*curve.total)-1))
    return values.get(edge_key(curve.path[i],curve.path[i+1]),1.)


def _column(surface,x,spacing,density):
    us=surface.u;surface.prime(us,x);positions=[surface.at(u,x)[1] for u in us];arc=[0.]
    for a,b in zip(positions,positions[1:]):arc.append(arc[-1]+length(sub(b,a)))
    count=max(1,int(arc[-1]/spacing+.5))
    def inverse(distance):
        i=min(len(arc)-1,max(1,bisect.bisect_left(arc,distance)))
        t=(distance-arc[i-1])/max(EPS,arc[i]-arc[i-1])
        return us[i-1]*(1.-t)+us[i]*t
    def refine(a,b,depth=0):
        middle=(a+b)*.5
        if depth<5 and length(sub(surface.at(middle,x)[1],mix(surface.at(a,x)[1],surface.at(b,x)[1],.5)))>spacing*.065:
            return refine(a,middle,depth+1)[:-1]+refine(middle,b,depth+1)
        return [a,b]
    base=[inverse(arc[-1]*i/count) for i in range(count+1)];base[0]=0.;base[-1]=1.
    surface.prime(base+[(a+b)*.5 for a,b in zip(base,base[1:])],x)
    samples=[]
    for a,b in zip(base,base[1:]):samples.extend(refine(a,b)[:-1])
    samples.append(1.)
    if not density:return samples
    output=[];v=bias_cross_position(x/surface.width,surface.plan.settings.bias)
    for a,b in zip(samples,samples[1:]):
        factor=0.
        for j in range(5):
            u=a+(b-a)*(j+.5)/5;left=surface.mapping.flow_to_u(u);right=surface.mapping.u_to_v(left)
            factor+=(_density(surface.mapping.a,left,density)*(1.-v)+_density(surface.mapping.b,right,density)*v)/5
        extra=max(1,int(math.ceil(factor-1e-8)))
        output.extend(a+(b-a)*j/extra for j in range(extra))
    output.append(1.)
    return output


def _bad_quad(a0,a1,b0,b1):
    p=[a0,b0,b1,a1]
    first=cross(sub(p[1],p[0]),sub(p[2],p[0]));second=cross(sub(p[2],p[0]),sub(p[3],p[0]))
    area_a=length(first);area_b=length(second)
    if area_a>0. and area_b>0. and dot(first,second)<.25*area_a*area_b:return True
    normal=add(first,second);area=length(normal)
    edges=[sub(p[(i+1)%4],p[i]) for i in range(4)]
    for i in range(4):
        signed=dot(cross(edges[i],edges[(i+1)%4]),normal)
        if signed<0 and signed<-.015*length(edges[i])*length(edges[(i+1)%4])*area:return True
    return False


def geometry_path(a,b,fa,fb):
    baseline=zipper_flows(fa,fb)
    def tangent(points,i):return norm(sub(points[min(len(points)-1,i+1)],points[max(0,i-1)]))
    ta=[tangent(a,i) for i in range(len(a))];tb=[tangent(b,i) for i in range(len(b))]
    def skew(i,j):
        width=sub(b[j],a[i]);t=norm(add(ta[i],tb[j]))
        return abs(dot(norm(width),t)) if length(width)>EPS else 0.
    severe=any(skew(i,j)>.65 for i,j in baseline)
    severe|=any(ni>i and nj>j and _bad_quad(a[i],a[ni],b[j],b[nj])
                for (i,j),(ni,nj) in zip(baseline,baseline[1:]))
    if not severe or len(a)*len(b)>160000:return baseline
    # Additional triangles are justified only by a substantially skewed or
    # folded quad. The fixed penalty keeps regular strips predominantly quad.
    width=len(b);previous=bytearray(len(a)*width);before=[float('inf')]*width
    # A fixed station band also applies to small patches: the previous full
    # product search was repeated for every curved strip on every edit.
    corridor=[[] for _ in a]
    for i,j in baseline:corridor[i].append(j)
    for i in range(len(a)):
        current=[float('inf')]*width
        center=min(width-1,bisect.bisect_left(fb,fa[i]))
        lo=max(0,min(center,min(corridor[i]))-2);hi=min(width,max(center,max(corridor[i]))+3)
        for j in range(lo,hi):
            if i==j==0:current[j]=0.;continue
            local=skew(i,j)**2+.1*(fa[i]-fb[j])**2
            candidates=[]
            if i and j:
                folded=_bad_quad(a[i-1],a[i],b[j-1],b[j])
                candidates.append((before[j-1]+local+(5. if folded else 0.),1))
            if i:candidates.append((before[j]+local+.22,2))
            if j:candidates.append((current[j-1]+local+.22,3))
            current[j],previous[i*width+j]=min(candidates)
        before=current
    if not math.isfinite(before[-1]):return baseline
    i=len(a)-1;j=len(b)-1;path=[(i,j)]
    while i or j:
        step=previous[i*width+j]
        if step in (1,2):i-=1
        if step in (1,3):j-=1
        path.append((i,j))
    return path[::-1]


class _SurfaceSections:
    """Reference rows are needed by editing tools, not viewport generation."""
    lazy=True
    def __init__(self,surface,count):self.surface=surface;self.count=count;self.rows={}
    def __len__(self):return self.count+1
    def __getitem__(self,index):
        if isinstance(index,slice):return tuple(self[i] for i in range(*index.indices(len(self))))
        if index<0:index+=len(self)
        if not 0<=index<len(self):raise IndexError(index)
        if index not in self.rows:self.rows[index]=tuple(self.surface.at(index/self.count,x)[1] for x in range(self.surface.width+1))
        return self.rows[index]
    def __iter__(self):
        for x in range(1,self.surface.width):self.surface.prime([i/self.count for i in range(len(self))],x)
        for i in range(len(self)):yield self[i]


def _build_patch(plan,width,registry,left_curve,right_curve,left,right,cap_profiles,put,emit,max_polygons):
    mapping=registry.maps[left_curve.path,right_curve.path]
    surface=FlowSurface(plan,width,mapping,cap_profiles,registry.cap_keys)
    columns=[];flows=[];coords=[]
    boundary_flows=[[mapping.fraction(curve,key) for key,pos in samples]
                    for curve,samples in ((left_curve,left),(right_curve,right))]
    def station(values,t):
        offset=t*(len(values)-1);i=min(len(values)-2,int(offset));f=offset-i
        return values[i]*(1.-f)+values[i+1]*f
    for x in range(width+1):
        if x in (0,width):
            curve,samples=(left_curve,left) if x==0 else (right_curve,right)
            us=[mapping.fraction(curve,key) for key,pos in samples]
            pairs=samples
        else:
            us=_column(surface,x,registry.plan_spacing(plan),registry.density)
            if registry.point_field.affected(left_curve) or registry.point_field.affected(right_curve):
                from .density import redistribute
                positions=[surface.at(u,x)[1] for u in surface.u];arc=[0.]
                for a,b in zip(positions,positions[1:]):arc.append(arc[-1]+length(sub(b,a)))
                def arc_at(u):
                    k=min(surface.n-1,int(u*surface.n));t=u*surface.n-k
                    return arc[k]*(1.-t)+arc[k+1]*t
                v=bias_cross_position(x/width,plan.settings.bias)
                def factor(u):
                    a=mapping.flow_to_u(u);b=mapping.u_to_v(a)
                    return registry.point_field.at(left_curve,a)*(1.-v)+registry.point_field.at(right_curve,b)*v
                knots=[mapping.u_to_flow(u) for u in registry.point_field.profile(left_curve)[1]]
                knots.extend(mapping.u_to_flow(mapping.v_to_u(u)) for u in registry.point_field.profile(right_curve)[1])
                us=redistribute(us,arc_at,factor,knots,2*max_polygons)
            # Adjacent columns share a row phase. Independent arc resampling
            # made each column invent a different transverse zigzag.
            count=len(us)-1;v=bias_cross_position(x/width,plan.settings.bias)
            us=[station(boundary_flows[0],k/count)*(1.-v)+station(boundary_flows[1],k/count)*v for k in range(count+1)]
            surface.prime(us,x)
            pairs=[]
            for k,u in enumerate(us):
                key,pos=surface.at(u,x)
                pairs.append((key if key is not None else ('inside',plan.index,k,x),pos))
        if sum(map(len,columns))+len(pairs)>2*max_polygons:raise ValueError(tr('Surface vertex limit exceeded'))
        columns.append([put(key,pos) for key,pos in pairs]);flows.append(us);coords.append([pos for key,pos in pairs])
    for x in range(width):
        path=geometry_path(coords[x],coords[x+1],flows[x],flows[x+1])
        for (i,j),(ni,nj) in zip(path,path[1:]):emit([columns[x][i],columns[x+1][j],columns[x+1][nj],columns[x][ni]])
    count=max(2,min(256,max(map(len,columns))-1))
    sections=_SurfaceSections(surface,count)
    return sections,columns


def build_patch(plan,width,registry,left_curve,right_curve,left,right,cap_profiles,put,emit,max_polygons,cache=None,previous=None):
    """Reuse an unchanged patch recipe; never retain another MeshResult.

    Include exact curve coefficients, seam stations, cap profiles, settings
    and local field distances. A matching source boundary alone is unsafe.
    """
    caps=[]
    for kind,i in (plan.start,plan.end):
        if kind=='edge':
            edge=edge_key(plan.boundary[i],plan.boundary[(i+1)%len(plan.boundary)])
            caps.append((edge,tuple(cap_profiles[edge]),tuple(registry.cap_keys.get(edge,()))))
    edges=set(left_curve.edges)|set(right_curve.edges)
    signature=('adaptive',plan,width,registry.plan_spacing(plan),left_curve.geometry_signature,right_curve.geometry_signature,
        tuple(left),tuple(right),tuple(caps),tuple(sorted((e,registry.density[e]) for e in edges if e in registry.density)),
        tuple(registry.point_field.profile(left_curve)[0]),tuple(registry.point_field.profile(right_curve)[0]))
    old=(previous or {}).get(plan.boundary)
    reused=bool(old and old[0]==signature)
    if reused:recipe=old[1]
    else:
        points=[];faces=[];indices={}
        def local_put(key,pos):
            if key not in indices:indices[key]=len(points);points.append((key,pos))
            return indices[key]
        def local_emit(face):faces.append(tuple(face))
        sections,columns=_build_patch(plan,width,registry,left_curve,right_curve,left,right,cap_profiles,local_put,local_emit,max_polygons)
        recipe=(tuple(points),tuple(faces),sections,tuple(tuple(c) for c in columns))
    if cache is not None:cache[plan.boundary]=(signature,recipe)
    points,faces,sections,columns=recipe
    remap=[put(key,pos) for key,pos in points]
    if hasattr(emit,'bulk'):emit.bulk(faces,remap)
    else:
        for face in faces:emit([remap[v] for v in face])
    return sections,[[remap[v] for v in column] for column in columns],reused
