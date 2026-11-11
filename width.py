"""Progressive cross-row transitions with exact inherited cap samples."""
from .i18n import tr
import math
from .core import EPS,add,sub,mul,dot,cross,length,norm,mix,bias_cross_position,edge_key
from .surface import _rotate,CapBlend
from .ribbon import zipper_flows


def _limit_row_changes(values,body):
    """Spread reductions over available rows, keeping inherited endpoints exact."""
    values=list(values);steps=len(values)-1
    if steps<2:return values
    budget=abs(values[0]-body)+abs(values[-1]-body)
    step=max(1,math.ceil(budget/steps))
    for i in range(1,steps):values[i]=max(values[i-1]-step,min(values[i-1]+step,values[i]))
    for i in range(steps-1,0,-1):values[i]=max(values[i+1]-step,min(values[i+1]+step,values[i]))
    return values


def _coherent_paths(fractions):
    """Fit a consistent left- or right-indexed transition track across a run.

    Keep the minimum triangle count and monotone correspondence. Fall back
    to the local zipper if a track would substantially worsen alignment.
    """
    paths=[zipper_flows(a,b) for a,b in zip(fractions,fractions[1:])]
    def path_at(a,b,j):
        swap=len(a)<len(b);nb=min(len(a),len(b))-1
        path=[(i,i) for i in range(j+1)]+[(j+1,j)]+[(i+1,i) for i in range(j+1,nb+1)]
        return [(b,a) for a,b in path] if swap else path
    def cost(k,path):
        a,b=fractions[k:k+2]
        return sum((a[i]-b[j])**2 for i,j in path)
    runs=[];run=[];direction=0
    for k,(a,b) in enumerate(zip(fractions,fractions[1:])):
        delta=len(b)-len(a)
        if not delta:continue
        sign=1 if delta>0 else -1
        if abs(delta)!=1 or min(len(a),len(b))<2:
            if run:runs.append(run);run=[]
            direction=0;continue
        if run and direction!=sign:runs.append(run);run=[]
        run.append(k);direction=sign
    if run:runs.append(run)
    for run in runs:
        if len(run)<2:continue
        limit=min(min(len(fractions[k]),len(fractions[k+1]))-1 for k in run)
        best=None
        for reverse in (False,True):
            for offset in range(limit+1):
                trial=[path_at(fractions[k],fractions[k+1],min(len(fractions[k]),len(fractions[k+1]))-1-offset if reverse else offset) for k in run]
                score=sum(cost(k,path) for k,path in zip(run,trial))
                if best is None or score<best[0]-1e-12:best=(score,trial)
        baseline=sum(cost(k,paths[k]) for k in run)
        if best[0]<=max(1e-12,baseline)*2.:
            for k,path in zip(run,best[1]):paths[k]=path
    return paths


def _regular_transition(plan,width,ca,cb,left,right,caps,max_polygons):
    """Fair interior rows; unequal border budgets affect only the rim strips."""
    points=[];faces=[];indices={}
    def vertex(key,pos):
        if key not in indices:indices[key]=len(points);points.append((key,pos))
        return indices[key]
    borders=[[vertex(key,pos) for key,pos in samples] for samples in (left,right)]
    fa=[ca.fraction(key) for key,pos in left];fb=[cb.fraction(key) for key,pos in right]
    count=max(1,int(((len(left)-1)+(len(right)-1))*.5+.5))
    if (count+1)*(max([width]+[len(cap)-1 for cap in caps if cap])+1)>2*max_polygons:raise ValueError(tr('Width-transition vertex limit exceeded'))
    def station(values,t):
        offset=t*(len(values)-1);i=min(len(values)-2,int(offset));blend=offset-i
        return values[i]*(1.-blend)+values[i+1]*blend
    aa=[ca.at(station(fa,i/count)) for i in range(count+1)]
    bb=[cb.at(station(fb,i/count)) for i in range(count+1)]
    flows=[(station(fa,i/count)+station(fb,i/count))*.5 for i in range(count+1)]
    centers=[mul(add(a,b),.5) for (_,a),(_,b) in zip(aa,bb)]
    tangents=[norm(sub(centers[min(count,i+1)],centers[max(0,i-1)])) for i in range(count+1)]
    normals=[];reference=plan.normal;old_tangent=tangents[0]
    for k,((ka,a),(kb,b)) in enumerate(zip(aa,bb)):
        transported=_rotate(reference,old_tangent,tangents[k]);normal=norm(cross(sub(b,a),tangents[k]))
        if length(normal)<EPS:normal=transported
        if dot(normal,transported)<0.:normal=mul(normal,-1.)
        normals.append(normal);reference=normal;old_tangent=tangents[k]
    cap_blend=CapBlend(plan,[(aa[i][1],bb[i][1]) for i in (0,-1)],(normals[0],normals[-1]),
        [[point for key,point in cap] if cap else None for cap in caps])
    widths=[length(sub(b,a)) for (_,a),(_,b) in zip(aa,bb)];span=max(EPS,max(widths))
    counts=[len(cap)-1 if cap else width for cap in caps]
    def smooth(t):t=max(0.,min(1.,t));return t*t*(3.-2.*t)
    pointed=plan.start[0]=='point' or plan.end[0]=='point'
    row_counts=[]
    for k,u in enumerate(flows):
        body=width*min(1.,widths[k]/span) if pointed else width
        value=body+(counts[0]-width)*(1.-smooth(u/.4))+(counts[1]-width)*smooth((u-.6)/.4)
        row_counts.append(max(2,int(value+.5)))
    row_counts[0]=counts[0];row_counts[-1]=counts[1]
    row_counts=_limit_row_changes(row_counts,width)
    rows=[];inner=[];fractions=[];sections=[]
    # Grid border entries are references to existing shared rail samples.
    import bisect
    def nearest(values,u):
        j=min(len(values)-1,bisect.bisect_left(values,u));return j-1 if j and abs(values[j-1]-u)<abs(values[j]-u) else j
    for k,((ka,a),(kb,b)) in enumerate(zip(aa,bb)):
        n=row_counts[k];cap=caps[0] if k==0 else caps[1] if k==count else None
        weights=cap_blend.weights(flows[k])
        coords=[];row=[]
        for x in range(n+1):
            if cap:key,pos=cap[x]
            elif ka==kb:key,pos=ka,a
            elif x==0:key,pos=ka,a
            elif x==n:key,pos=kb,b
            else:
                v=bias_cross_position(x/n,plan.settings.bias);amount=plan.settings.signed_bulge*widths[k]*math.sin(math.pi*v)
                key=('width',plan.index,k,x,n);pos=add(mix(a,b,v),mul(normals[k],amount))
                pos=cap_blend.apply(pos,x/n,weights)
            coords.append(pos)
            if x==0:index=borders[0][nearest(fa,station(fa,k/count))]
            elif x==n:index=borders[1][nearest(fb,station(fb,k/count))]
            else:index=vertex(key,pos)
            row.append(index)
        rows.append(tuple(row));inside=[];values=[]
        for x in range(1,n):
            if not inside or inside[-1]!=row[x]:inside.append(row[x]);values.append(x/n)
        inner.append(inside);fractions.append(values)
        section=[]
        for x in range(width+1):
            value=x/width*n;j=min(n-1,int(value));section.append(mix(coords[j],coords[j+1],value-j))
        sections.append(tuple(section))
    for k,path in enumerate(_coherent_paths(fractions)):
        for (i,j),(ni,nj) in zip(path,path[1:]):faces.append((inner[k][i],inner[k][ni],inner[k+1][nj],inner[k+1][j]))
    columns=[borders[0]]
    for x in range(1,width):
        line=[]
        for row in rows:
            index=row[min(len(row)-2,max(1,int(x/width*(len(row)-1)+.5)))]
            if not line or line[-1]!=index:line.append(index)
        if length(sub(points[line[0]][1],sections[0][x]))>1e-7 or length(sub(points[line[-1]][1],sections[-1][x]))>1e-7:line=[]
        columns.append(line)
    columns.append(borders[1])
    first=[row[0] for row in inner];last=[row[-1] for row in inner]
    for one,two,fu,fv in ((borders[0],first,fa,flows),(last,borders[1],flows,fb)):
        path=zipper_flows(fu,fv)
        for (i,j),(ni,nj) in zip(path,path[1:]):faces.append((one[i],two[j],two[nj],one[ni]))
    if len(faces)>max_polygons:raise ValueError(tr('Width-transition polygon limit exceeded'))
    return tuple(points),tuple(faces),tuple(sections),tuple(tuple(c) for c in columns),tuple(rows)


def build_transition(plan,width,registry,ca,cb,left,right,profiles,put,emit,max_polygons,cache,previous,mapping=None):
    caps=[]
    for cap in (plan.start,plan.end):
        if cap[0]=='edge':
            i=cap[1];edge=edge_key(plan.boundary[i],plan.boundary[(i+1)%len(plan.boundary)])
            keys=registry.cap_keys[edge];positions=profiles[edge]
            if plan.left[0 if cap==plan.start else -1]!=edge[0]:keys=keys[::-1];positions=positions[::-1]
            caps.append(tuple(zip(keys,positions)))
        else:caps.append(None)
    signature=('width_coherent',plan,width,ca.geometry_signature,cb.geometry_signature,tuple(left),tuple(right),tuple(caps),mapping is not None)
    old=previous.get(plan.boundary);reused=bool(old and old[0]==signature)
    if reused:recipe=old[1]
    elif mapping is None and width>=2 and all(cap is None or len(cap)>=3 for cap in caps):
        recipe=_regular_transition(plan,width,ca,cb,left,right,caps,max_polygons)
    else:
        points=[];faces=[];indices={}
        def vertex(key,pos):
            if key not in indices:indices[key]=len(points);points.append((key,pos))
            return indices[key]
        fa=[mapping.fraction(ca,k) if mapping else ca.fraction(k) for k,p in left]
        fb=[mapping.fraction(cb,k) if mapping else cb.fraction(k) for k,p in right]
        path=zipper_flows(fa,fb)
        if len(path)*(max([width]+[len(cap)-1 for cap in caps if cap])+1)>2*max_polygons:raise ValueError(tr('Width-transition vertex limit exceeded'))
        centers=[mul(add(left[i][1],right[j][1]),.5) for i,j in path]
        tangents=[norm(sub(centers[min(len(path)-1,i+1)],centers[max(0,i-1)])) for i in range(len(path))]
        normals=[];previous_normal=plan.normal;old_tangent=tangents[0]
        for k,(i,j) in enumerate(path):
            reference=_rotate(previous_normal,old_tangent,tangents[k]);normal=norm(cross(sub(right[j][1],left[i][1]),tangents[k]))
            if length(normal)<EPS:normal=reference
            if dot(normal,reference)<0.:normal=mul(normal,-1.)
            normals.append(normal);previous_normal=normal;old_tangent=tangents[k]
        ends=[(left[i][1],right[j][1]) for i,j in (path[0],path[-1])]
        cap_blend=CapBlend(plan,ends,(normals[0],normals[-1]),[[point for key,point in cap] if cap else None for cap in caps])
        counts=[len(cap)-1 if cap else width for cap in caps]
        def smooth(t):t=max(0.,min(1.,t));return t*t*(3.-2.*t)
        row_counts=[]
        for i,j in path:
            u=(fa[i]+fb[j])*.5
            row_counts.append(max(1,int(width+(counts[0]-width)*(1.-smooth(u/.4))+(counts[1]-width)*smooth((u-.6)/.4)+.5)))
        row_counts[0]=counts[0];row_counts[-1]=counts[1]
        row_counts=_limit_row_changes(row_counts,width)
        rows=[];sections=[];coordinates=[];fractions=[]
        for k,(i,j) in enumerate(path):
            u=(fa[i]+fb[j])*.5
            weights=cap_blend.weights(u)
            count=row_counts[k]
            cap=caps[0] if k==0 else caps[1] if k==len(path)-1 else None
            if cap:count=len(cap)-1
            row=[];coords=[];ka,pa=left[i];kb,pb=right[j]
            for x in range(count+1):
                if cap:key,pos=cap[x]
                elif x==0:key,pos=ka,pa
                elif x==count:key,pos=kb,pb
                elif ka==kb:key,pos=ka,pa
                else:
                    v=bias_cross_position(x/count,plan.settings.bias);amount=plan.settings.signed_bulge*length(sub(pb,pa))*math.sin(math.pi*v)
                    key=('width',plan.index,k,x,count);pos=add(mix(pa,pb,v),mul(normals[k],amount))
                    pos=cap_blend.apply(pos,x/count,weights)
                row.append(vertex(key,pos));coords.append(pos)
            rows.append(tuple(row));coordinates.append(coords);fractions.append([x/count for x in range(count+1)])
            # Cut supports lie on the actual transverse mesh edges.
            section=[]
            for x in range(width+1):
                value=x/width*count;index=min(count-1,int(value));section.append(mix(coords[index],coords[index+1],value-index))
            sections.append(tuple(section))
        for k,path in enumerate(_coherent_paths(fractions)):
            for (i,j),(ni,nj) in zip(path,path[1:]):faces.append((rows[k][i],rows[k][ni],rows[k+1][nj],rows[k+1][j]))
        if len(faces)>max_polygons:raise ValueError(tr('Width-transition polygon limit exceeded'))
        columns=[]
        for x in range(width+1):
            column=[]
            for row in rows:
                v=row[min(len(row)-1,int(x/width*(len(row)-1)+.5))]
                if not column or column[-1]!=v:column.append(v)
            if 0<x<width and (length(sub(points[column[0]][1],sections[0][x]))>1e-7 or length(sub(points[column[-1]][1],sections[-1][x]))>1e-7):column=[]
            columns.append(tuple(column))
        recipe=(tuple(points),tuple(faces),tuple(sections),tuple(columns),tuple(rows))
    cache[plan.boundary]=(signature,recipe);points,faces,sections,columns,rows=recipe;remap=[put(k,p) for k,p in points]
    if hasattr(emit,'bulk'):emit.bulk(faces,remap)
    else:
        for face in faces:emit([remap[v] for v in face])
    return sections,[[remap[v] for v in col] for col in columns],[[remap[v] for v in row] for row in rows],reused
