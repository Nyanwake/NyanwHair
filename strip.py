"""A common row grid between two rails, with local border transitions."""
from .i18n import tr
import math
from .core import add,sub,mul,length,mix,bias_cross_position,edge_key
from .surface import CapBlend
from .ribbon import zipper_flows
from .row_layout import correspondence,rows,profile_grid,profile_normals


def build_strip(plan,width,registry,ca,cb,left,right,profiles,put,emit,max_polygons,cache,previous):
    cap_data=[]
    for kind,i in (plan.start,plan.end):
        if kind=='edge':
            edge=edge_key(plan.boundary[i],plan.boundary[(i+1)%len(plan.boundary)])
            cap_data.append((edge,tuple(profiles[edge]),tuple(registry.cap_keys.get(edge,()))))
    signature=('strip_rows',plan,width,ca.geometry_signature,cb.geometry_signature,tuple(left),tuple(right),tuple(cap_data))
    old=previous.get(plan.boundary);reused=bool(old and old[0]==signature)
    if reused:recipe=old[1]
    else:
        points=[];faces=[];indices={}
        def vertex(key,pos):
            if key not in indices:indices[key]=len(points);points.append((key,pos))
            return indices[key]
        actual_a=[vertex(k,p) for k,p in left];actual_b=[vertex(k,p) for k,p in right]
        count=max(1,int(((len(left)-1)+(len(right)-1))*.5+.5))
        mapping=correspondence(registry,ca,cb) if width>1 else None
        body_a,body_b,fractions,fa,fb=rows(ca,cb,left,right,count,mapping)
        if len(body_a)*(width+1)>2*max_polygons:raise ValueError(tr('Strip vertex limit exceeded'))
        normals=profile_normals(body_a,body_b,plan.normal)
        cap_blend=CapBlend.from_profiles(plan,[(body_a[i][1],body_b[i][1]) for i in (0,-1)],(normals[0],normals[-1]),profiles)
        profile=[bias_cross_position(x/width,plan.settings.bias) for x in range(width+1)]
        sine=[math.sin(math.pi*v) for v in profile];bulge=plan.bulge
        positions=profile_grid(body_a,body_b,normals,bulge,profile,cap_blend,fractions)
        grid=[];sections=[]
        for row_index,((ka,a),(kb,b)) in enumerate(zip(body_a,body_b)):
            row=[];coords=[];row_width=length(sub(b,a)) if positions is None else 0.
            weights=cap_blend.weights(fractions[row_index]) if positions is None else None
            for x in range(width+1):
                if x==0:key,pos=ka,a
                elif x==width:key,pos=kb,b
                elif ka==kb:key,pos=ka,a
                else:
                    if positions is not None:pos=tuple(positions[row_index][x])
                    else:
                        v=profile[x];amount=bulge*row_width*sine[x]
                        pos=add(mix(a,b,v),mul(normals[row_index],amount))
                        pos=cap_blend.apply(pos,x/width,weights)
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
        frozen=registry.frozen and old and tuple(key for key,pos in points)==tuple(key for key,pos in old[1][0])
        if frozen:faces=list(old[1][1])
        else:
            for x in range(width):
                fa=flows_a if x==0 else fractions;fb=flows_b if x==width-1 else fractions
                if mapping and x in (0,width-1):
                    from .surface import geometry_path
                    path=geometry_path([points[i][1] for i in columns[x]],[points[i][1] for i in columns[x+1]],fa,fb)
                else:path=zipper_flows(fa,fb)
                for (i,j),(ni,nj) in zip(path,path[1:]):
                    faces.append((columns[x][i],columns[x+1][j],columns[x+1][nj],columns[x][ni]))
        if len(faces)>max_polygons:raise ValueError(tr('Strip polygon limit exceeded'))
        recipe=(tuple(points),tuple(faces),tuple(sections),tuple(tuple(c) for c in columns),tuple(grid))
    cache[plan.boundary]=(signature,recipe)
    points,faces,sections,columns,grid=recipe
    remap=[put(key,pos) for key,pos in points]
    if hasattr(emit,'bulk'):emit.bulk(faces,remap)
    else:
        for face in faces:emit([remap[v] for v in face])
    return sections,[[remap[v] for v in c] for c in columns],[[remap[v] if v is not None else None for v in row] for row in grid],reused
