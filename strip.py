"""A common row grid between two rails, with local border transitions."""
import math
from .core import EPS,add,sub,mul,dot,cross,length,norm,mix,bias_cross_position,edge_key
from .surface import _rotate
from .ribbon import zipper_flows


def build_strip(plan,width,registry,ca,cb,left,right,profiles,put,emit,max_polygons,cache,previous):
    cap_data=[]
    for kind,i in (plan.start,plan.end):
        if kind=='edge':
            edge=edge_key(plan.boundary[i],plan.boundary[(i+1)%len(plan.boundary)])
            cap_data.append((edge,tuple(profiles[edge]),tuple(registry.cap_keys.get(edge,()))))
    signature=('strip',plan,width,ca.geometry_signature,cb.geometry_signature,tuple(left),tuple(right),tuple(cap_data))
    old=previous.get(plan.boundary);reused=bool(old and old[0]==signature)
    if reused:recipe=old[1]
    else:
        points=[];faces=[];indices={}
        def vertex(key,pos):
            if key not in indices:indices[key]=len(points);points.append((key,pos))
            return indices[key]
        actual_a=[vertex(k,p) for k,p in left];actual_b=[vertex(k,p) for k,p in right]
        fa=[ca.fraction(key) for key,pos in left];fb=[cb.fraction(key) for key,pos in right]
        count=max(1,int(((len(left)-1)+(len(right)-1))*.5+.5))
        def station(values,t):
            offset=t*(len(values)-1);i=min(len(values)-2,int(offset));f=offset-i
            return values[i]*(1.-f)+values[i+1]*f
        fractions=[(station(fa,i/count)+station(fb,i/count))*.5 for i in range(count+1)]
        body_a=left if len(left)==count+1 else [ca.at(station(fa,i/count)) for i in range(count+1)]
        body_b=right if len(right)==count+1 else [cb.at(station(fb,i/count)) for i in range(count+1)]
        if len(body_a)*(width+1)>2*max_polygons:raise ValueError('Превышен лимит точек полосы')
        centers=[mul(add(a,b),.5) for (_,a),(_,b) in zip(body_a,body_b)]
        tangents=[norm(sub(centers[min(count,i+1)],centers[max(0,i-1)])) for i in range(count+1)]
        normals=[];old_tangent=tangents[0];previous_normal=plan.normal
        for i,((_,a),(_,b)) in enumerate(zip(body_a,body_b)):
            reference=_rotate(previous_normal,old_tangent,tangents[i])
            normal=norm(cross(sub(b,a),tangents[i]))
            if length(normal)<EPS:normal=reference
            if dot(normal,reference)<0.:normal=mul(normal,-1.)
            normals.append(normal);previous_normal=normal;old_tangent=tangents[i]
        grid=[];sections=[]
        for row_index,((ka,a),(kb,b)) in enumerate(zip(body_a,body_b)):
            row=[];coords=[]
            for x in range(width+1):
                if x==0:key,pos=ka,a
                elif x==width:key,pos=kb,b
                elif ka==kb:key,pos=ka,a
                else:
                    v=bias_cross_position(x/width,plan.settings.bias)
                    amount=plan.settings.bulge*length(sub(b,a))*math.sin(math.pi*v)
                    if plan.settings.flip:amount=-amount
                    pos=add(mix(a,b,v),mul(normals[row_index],amount))
                    if row_index==0 and plan.start[0]=='edge' or row_index==len(body_a)-1 and plan.end[0]=='edge':
                        cap=plan.start if row_index==0 else plan.end
                        edge=edge_key(plan.boundary[cap[1]],plan.boundary[(cap[1]+1)%len(plan.boundary)])
                        aligned=x if plan.left[0 if row_index==0 else -1]==edge[0] else width-x
                        key=registry.cap_keys[edge][aligned];pos=profiles[edge][aligned]
                    else:key=('inside',plan.index,row_index,x)
                row.append(indices.get(key) if x in (0,width) else vertex(key,pos));coords.append(pos)
            grid.append(tuple(row));sections.append(tuple(coords))
        columns=[actual_a]+[[row[x] for row in grid] for x in range(1,width)]+[actual_b]
        flows_a=fa;flows_b=fb
        for x in range(width):
            fa=flows_a if x==0 else fractions;fb=flows_b if x==width-1 else fractions
            for (i,j),(ni,nj) in zip((path:=zipper_flows(fa,fb)),path[1:]):
                faces.append((columns[x][i],columns[x+1][j],columns[x+1][nj],columns[x][ni]))
        if len(faces)>max_polygons:raise ValueError('Превышен лимит полигонов полосы')
        recipe=(tuple(points),tuple(faces),tuple(sections),tuple(tuple(c) for c in columns),tuple(grid))
    cache[plan.boundary]=(signature,recipe)
    points,faces,sections,columns,grid=recipe
    remap=[put(key,pos) for key,pos in points]
    for face in faces:emit([remap[v] for v in face])
    return sections,[[remap[v] for v in c] for c in columns],[[remap[v] if v is not None else None for v in row] for row in grid],reused
