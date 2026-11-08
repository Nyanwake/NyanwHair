"""Flow-aligned columns for curled and locally refined ribbon patches."""
import bisect,math
from .core import EPS,add,sub,mul,dot,cross,length,norm,mix,edge_key,bias_cross_position
from .ribbon import zipper_flows


def _rotate(vector,old,new):
    axis=cross(old,new);sine=length(axis);cosine=max(-1.,min(1.,dot(old,new)))
    if sine<EPS:return vector
    axis=mul(axis,1./sine)
    return add(add(mul(vector,cosine),mul(cross(axis,vector),sine)),mul(axis,dot(axis,vector)*(1.-cosine)))


class FlowSurface:
    def __init__(self,plan,width,mapping,cap_profiles):
        self.plan=plan;self.width=width;self.mapping=mapping;self.caps=cap_profiles;self._samples={}
        self.n=max(64,min(192,16*max(len(mapping.a.path),len(mapping.b.path))))
        self.u=[i/self.n for i in range(self.n+1)]
        pairs=[mapping.at(u) for u in self.u]
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
        if u==0. or u==1.:
            end=u==1.;cap=self.plan.end if end else self.plan.start
            if cap[0]=='edge':
                i=cap[1];lo,hi=edge_key(self.plan.boundary[i],self.plan.boundary[(i+1)%len(self.plan.boundary)])
                a_v=self.plan.left[-1 if end else 0];aligned=x if a_v==lo else self.width-x
                return ('cap',lo,hi,aligned,self.width),self.caps[lo,hi][aligned]
        return None,pos


def _density(curve,fraction,values):
    i=min(len(curve.path)-2,max(0,bisect.bisect_right(curve.offsets,fraction*curve.total)-1))
    return values.get(edge_key(curve.path[i],curve.path[i+1]),1.)


def _column(surface,x,spacing,density):
    us=surface.u;positions=[surface.at(u,x)[1] for u in us];arc=[0.]
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
    if area_a>EPS and area_b>EPS and dot(first,second)<.25*area_a*area_b:return True
    normal=add(first,second);area=length(normal)
    edges=[sub(p[(i+1)%4],p[i]) for i in range(4)]
    for i in range(4):
        signed=dot(cross(edges[i],edges[(i+1)%4]),normal)
        if signed<0 and signed<-.015*max(EPS,length(edges[i])*length(edges[(i+1)%4])*area):return True
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
    largest_step=max([y-x for flow in (fa,fb) for x,y in zip(flow,flow[1:])],default=1.)
    window=min(.35,max(.08,2.*largest_step)) if len(a)*len(b)>4096 else 1.
    for i in range(len(a)):
        current=[float('inf')]*width
        lo=max(0,bisect.bisect_left(fb,fa[i]-window));hi=min(width,bisect.bisect_right(fb,fa[i]+window))
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


def _build_patch(plan,width,registry,left_curve,right_curve,left,right,cap_profiles,put,emit,max_polygons):
    mapping=registry.maps[left_curve.path,right_curve.path]
    surface=FlowSurface(plan,width,mapping,cap_profiles)
    columns=[];flows=[];coords=[]
    for x in range(width+1):
        if x in (0,width):
            curve,samples=(left_curve,left) if x==0 else (right_curve,right)
            us=[mapping.fraction(curve,key) for key,pos in samples]
            pairs=samples
        else:
            us=_column(surface,x,registry.spacing,registry.density)
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
            pairs=[]
            for k,u in enumerate(us):
                key,pos=surface.at(u,x)
                pairs.append((key if key is not None else ('inside',plan.index,k,x),pos))
        if sum(map(len,columns))+len(pairs)>2*max_polygons:raise ValueError('Превышен лимит точек поверхности')
        columns.append([put(key,pos) for key,pos in pairs]);flows.append(us);coords.append([pos for key,pos in pairs])
    for x in range(width):
        path=geometry_path(coords[x],coords[x+1],flows[x],flows[x+1])
        for (i,j),(ni,nj) in zip(path,path[1:]):emit([columns[x][i],columns[x+1][j],columns[x+1][nj],columns[x][ni]])
    count=max(2,min(256,max(map(len,columns))-1))
    sections=[tuple(surface.at(i/count,x)[1] for x in range(width+1)) for i in range(count+1)]
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
            caps.append((edge,tuple(cap_profiles[edge])))
    edges=set(left_curve.edges)|set(right_curve.edges)
    signature=(plan,width,registry.spacing,left_curve.geometry_signature,right_curve.geometry_signature,
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
        recipe=(tuple(points),tuple(faces),tuple(sections),tuple(tuple(c) for c in columns))
    if cache is not None:cache[plan.boundary]=(signature,recipe)
    points,faces,sections,columns=recipe
    remap=[put(key,pos) for key,pos in points]
    for face in faces:emit([remap[v] for v in face])
    return sections,[[remap[v] for v in column] for column in columns],reused
