"""Progressive cross-row transitions with exact inherited cap samples."""
import math
from .core import EPS,add,sub,mul,dot,cross,length,norm,mix,bias_cross_position,edge_key
from .surface import _rotate
from .ribbon import zipper_flows


def build_transition(plan,width,registry,ca,cb,left,right,profiles,put,emit,max_polygons,cache,previous,mapping=None):
    caps=[]
    for cap in (plan.start,plan.end):
        if cap[0]=='edge':
            i=cap[1];edge=edge_key(plan.boundary[i],plan.boundary[(i+1)%len(plan.boundary)])
            keys=registry.cap_keys[edge];positions=profiles[edge]
            if plan.left[0 if cap==plan.start else -1]!=edge[0]:keys=keys[::-1];positions=positions[::-1]
            caps.append(tuple(zip(keys,positions)))
        else:caps.append(None)
    signature=('width',plan,width,ca.geometry_signature,cb.geometry_signature,tuple(left),tuple(right),tuple(caps),mapping is not None)
    old=previous.get(plan.boundary);reused=bool(old and old[0]==signature)
    if reused:recipe=old[1]
    else:
        points=[];faces=[];indices={}
        def vertex(key,pos):
            if key not in indices:indices[key]=len(points);points.append((key,pos))
            return indices[key]
        fa=[mapping.fraction(ca,k) if mapping else ca.fraction(k) for k,p in left]
        fb=[mapping.fraction(cb,k) if mapping else cb.fraction(k) for k,p in right]
        path=zipper_flows(fa,fb)
        if len(path)*(max(width,*[len(cap)-1 for cap in caps if cap])+1)>2*max_polygons:raise ValueError('Превышен лимит точек перехода ширины')
        centers=[mul(add(left[i][1],right[j][1]),.5) for i,j in path]
        tangents=[norm(sub(centers[min(len(path)-1,i+1)],centers[max(0,i-1)])) for i in range(len(path))]
        normals=[];previous_normal=plan.normal;old_tangent=tangents[0]
        for k,(i,j) in enumerate(path):
            reference=_rotate(previous_normal,old_tangent,tangents[k]);normal=norm(cross(sub(right[j][1],left[i][1]),tangents[k]))
            if length(normal)<EPS:normal=reference
            if dot(normal,reference)<0.:normal=mul(normal,-1.)
            normals.append(normal);previous_normal=normal;old_tangent=tangents[k]
        counts=[len(cap)-1 if cap else width for cap in caps]
        def smooth(t):t=max(0.,min(1.,t));return t*t*(3.-2.*t)
        row_counts=[]
        for i,j in path:
            u=(fa[i]+fb[j])*.5
            row_counts.append(max(1,int(width+(counts[0]-width)*(1.-smooth(u/.4))+(counts[1]-width)*smooth((u-.6)/.4)+.5)))
        row_counts[0]=counts[0];row_counts[-1]=counts[1]
        change=max(1,math.ceil(max(abs(counts[0]-width),abs(counts[1]-width))/max(1,math.ceil((len(path)-1)*.4))))
        if abs(counts[1]-counts[0])<=change*(len(path)-1):
            for k in range(1,len(path)-1):row_counts[k]=max(row_counts[k-1]-change,min(row_counts[k-1]+change,row_counts[k]))
            for k in range(len(path)-2,0,-1):row_counts[k]=max(row_counts[k+1]-change,min(row_counts[k+1]+change,row_counts[k]))
        rows=[];sections=[];coordinates=[];fractions=[]
        for k,(i,j) in enumerate(path):
            u=(fa[i]+fb[j])*.5
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
                    v=bias_cross_position(x/count,plan.settings.bias);amount=plan.settings.bulge*length(sub(pb,pa))*math.sin(math.pi*v)
                    if plan.settings.flip:amount=-amount
                    key=('width',plan.index,k,x,count);pos=add(mix(pa,pb,v),mul(normals[k],amount))
                row.append(vertex(key,pos));coords.append(pos)
            rows.append(tuple(row));coordinates.append(coords);fractions.append([x/count for x in range(count+1)])
            # Cut supports lie on the actual transverse mesh edges.
            section=[]
            for x in range(width+1):
                value=x/width*count;index=min(count-1,int(value));section.append(mix(coords[index],coords[index+1],value-index))
            sections.append(tuple(section))
        for k in range(len(rows)-1):
            for (i,j),(ni,nj) in zip((path:=zipper_flows(fractions[k],fractions[k+1])),path[1:]):faces.append((rows[k][i],rows[k][ni],rows[k+1][nj],rows[k+1][j]))
        if len(faces)>max_polygons:raise ValueError('Превышен лимит полигонов перехода ширины')
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
    for face in faces:emit([remap[v] for v in face])
    return sections,[[remap[v] for v in col] for col in columns],[[remap[v] for v in row] for row in rows],reused
