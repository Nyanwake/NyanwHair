"""Cached guide depth bounds derived from the displayed boundary, without scene writes."""
from mathutils import Vector
from . import core


def rails(cache,result,evaluate,matrix,normal_matrix,visible):
    transform=tuple(float(v) for row in matrix for v in row)
    signature=(id(result),transform,id(cache.get('gpu_boundary_normals')),bool(visible))
    if cache.get('gpu_guide_geometry_signature')==signature:
        return cache['gpu_rail_records'],cache['rail_segments'],cache['rail_attributes']
    edges={}
    for owner,paths in (result.face_paths.items() if visible else ()):
        for path in paths:
            for one,two in zip(path,path[1:]):edges.setdefault(core.edge_key(one,two),set()).add(owner)
    samples={edge:{0.:('v',edge[0]),1.:('v',edge[1])} for edge in edges}
    positions={key:point for key,point in zip(result.vertex_keys,result.vertices) if key[0] in ('v','s')}
    for key in positions:
        if key[0]=='s' and key[1:3] in samples:samples[key[1:3]][float(key[3])]=key
    normals=cache.get('gpu_boundary_normals',{});old=cache.get('gpu_rail_records',{});records={};segments={};attributes={};pending=[]
    linear=matrix.to_3x3()
    for edge,owners in sorted(edges.items()):
        owners=sorted(owners)[:2];one,two=edge
        knots=sorted(samples[edge]);keys=[samples[edge][t] for t in knots]
        points=[positions[key] if key in positions else evaluate(one,two,t) for t,key in zip(knots,keys)]
        rows=[tuple(normals.get((key,owner),(0.,0.,0.)) for owner in owners) for key in keys]
        stamp=(transform,evaluate.coefficients.get(edge),evaluate.coefficients.get((two,one)),tuple(zip(knots,points,rows)))
        saved=old.get(edge)
        if saved and saved[0]==stamp:data=saved[1]
        else:
            # Bound the error of the 24 display chords of a cubic guide.
            c=evaluate.coefficients.get(edge)
            if c is None:
                reverse=evaluate.coefficients.get((two,one))
                if reverse:
                    q,p,mb,ma=reverse;c=(p,q,core.mul(ma,-1.),core.mul(mb,-1.))
            chord_error=0.
            if c:
                p,q,ma,mb=map(Vector,c);quadratic=-3*p+3*q-2*ma-mb;cubic=2*p-2*q+ma+mb
                chord_error=max((linear@(2*quadratic)).length,(linear@(2*quadratic+6*cubic)).length)/(8.*24.*24.)
            pending.append((edge,knots,points,rows,c,chord_error));data=None
        records[edge]=(stamp,data)
    for edge,data in _rail_geometry(pending,evaluate,matrix,normal_matrix):records[edge]=(records[edge][0],data)
    for edge,(_,data) in records.items():segments[edge]=data['pos'];attributes[edge]=data
    cache['gpu_guide_geometry_signature']=signature
    return records,segments,attributes


def _rail_geometry(jobs,evaluate,matrix,normal_matrix):
    """Evaluate dirty guide ribbons together; no per-edge NumPy kernels."""
    if not jobs:return ()
    import numpy as np
    ts=np.arange(25,dtype=np.float64)/24.;tt=ts*ts;ttt=tt*ts
    coefficients=[];knots=[];points=[];normals=[];offsets=[0]
    for edge,ks,ps,ns,c,error in jobs:
        coefficients.append(c or ((0.,0.,0.),)*4)
        knots.extend(ks);points.extend(ps)
        normals.extend(row if len(row)==2 else (row[0],row[0]) for row in ns)
        offsets.append(len(knots))
    values=np.asarray(coefficients);p0,p1,m0,m1=(values[:,i,None,:] for i in range(4))
    local=((2*ttt-3*tt+1)[None,:,None]*p0+(-2*ttt+3*tt)[None,:,None]*p1)+((ttt-2*tt+ts)[None,:,None]*m0+(ttt-tt)[None,:,None]*m1)
    for i,(edge,ks,ps,ns,c,error) in enumerate(jobs):
        if c is None:local[i]=[evaluate(*edge,t) for t in ts]
    local=local.astype(np.float32);ks=np.asarray(knots);offsets=np.asarray(offsets)
    # Disjoint [2*i,2*i+1] ranges allow one search over ragged knot rows.
    bucket=2.*np.arange(len(jobs))
    shifted=ks+np.repeat(bucket,np.diff(offsets))
    index=np.searchsorted(shifted,bucket[:,None]+ts,side='right')-1
    index=np.clip(index,offsets[:-1,None],offsets[1:,None]-2)
    blend=((ts-ks[index])/np.maximum(1e-12,ks[index+1]-ks[index])).astype(np.float32)
    sample_positions=np.asarray(points,dtype=np.float32)
    chords=(1-blend[:,:,None])*sample_positions[index]+blend[:,:,None]*sample_positions[index+1]
    linear=np.asarray(matrix.to_3x3(),dtype=np.float32);translation=np.asarray(matrix.translation,dtype=np.float32)
    world=local@linear.T+translation
    source=np.asarray(normals,dtype=np.float32)
    interpolated=(1-blend[:,:,None,None])*source[index]+blend[:,:,None,None]*source[index+1]
    adjusted=interpolated@np.asarray(normal_matrix,dtype=np.float32).T
    adjusted/=np.maximum(1e-12,np.linalg.norm(adjusted,axis=3))[:,:,:,None]
    gap=np.empty((len(jobs),25,4),dtype=np.float32);gap[:,:,:3]=(local-chords)@linear.T
    gap[:,:,3]=np.asarray([job[-1] for job in jobs])[:,None]
    pairs=np.column_stack((np.arange(24),np.arange(1,25))).reshape(-1)
    arrays={'pos':world[:,pairs],'normalA':adjusted[:,pairs,0],'normalB':adjusted[:,pairs,1],'curveGap':gap[:,pairs]}
    return [(job[0],{name:np.array(data[i],copy=True,order='C') for name,data in arrays.items()}) for i,job in enumerate(jobs)]


def view_style(cache,obj,context,pixel=False):
    """Scale overlay strokes by projected control bounds, for either view projection."""
    import gpu
    result=cache.get('result')
    if result is None or not result.faces:return 1.,0.
    points=getattr(result,'source_points',()) or cache.get('positions',())
    if not points:return 1.,0.
    matrix=obj.matrix_world;transform=tuple(float(v) for row in matrix for v in row)
    signature=(id(result),transform)
    saved=cache.get('gpu_stroke_bounds')
    if saved is None or saved[0]!=signature:
        ids={v for plan in result.plans_by_loop.values() for v in plan.boundary} if result else set()
        used=[points[i] for i in sorted(ids)] if ids else points
        lo=tuple(min(p[axis] for p in used) for axis in range(3));hi=tuple(max(p[axis] for p in used) for axis in range(3))
        corners=tuple(matrix@Vector((x,y,z)) for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2]))
        span=max((one-two).length for one in corners for two in corners)
        saved=(signature,corners,span);cache['gpu_stroke_bounds']=saved
    _,corners,span=saved
    projection=context.region_data.perspective_matrix if pixel else gpu.matrix.get_projection_matrix()@gpu.matrix.get_model_view_matrix()
    clips=[projection@point.to_4d() for point in corners]
    if any(point.w<=1e-6 for point in clips):return 1.,0.
    width,height=(context.region.width,context.region.height) if pixel else gpu.state.viewport_get()[2:]
    screen=[(point.x/point.w*width*.5,point.y/point.w*height*.5) for point in clips]
    extent=max(max(p[axis] for p in screen)-min(p[axis] for p in screen) for axis in (0,1))
    amount=max(0.,min(1.,(extent-45.)/655.));amount=amount*amount*(3.-2.*amount)
    scale=.20+.80*amount
    limit=span*.01/max(1e-4,(1.-scale)**2) if scale<.99999 else 0.
    return scale,limit


class _EmptyBatch:
    def __bool__(self):return False
    def draw(self,shader):pass


def batch(shader,kind,attributes):
    """Cached indexed rectangles; the shader expands them in screen space.

    Native wide GL lines have implementation-dependent square ends. Rounded
    analytic capsules cover joints consistently without per-frame CPU work.
    """
    from gpu_extras.batch import batch_for_shader
    import numpy as np
    points=attributes['pos'];count=len(points)
    if count<2:return _EmptyBatch()
    positions=np.asarray(points,dtype=np.float32)
    pairs=np.column_stack((np.arange(count-1),np.arange(1,count))) if kind=='LINE_STRIP' else np.arange(count//2*2).reshape(-1,2)
    pairs=pairs[np.any(positions[pairs[:,0]]!=positions[pairs[:,1]],axis=1)]
    if not len(pairs):return _EmptyBatch()
    ends=pairs[:,(0,0,1,1)].reshape(-1);others=pairs[:,(1,1,0,0)].reshape(-1)
    data={name:np.asarray(values,dtype=np.float32)[ends] for name,values in attributes.items()}
    data['other']=positions[others]
    data['corner']=np.tile(np.array(((-1.,0.),(1.,0.),(1.,1.),(-1.,1.)),dtype=np.float32),(len(pairs),1))
    indices=(np.arange(len(pairs),dtype=np.int32)[:,None,None]*4+np.array(((0,1,2),(0,2,3)),dtype=np.int32)).reshape(-1,3)
    return batch_for_shader(shader,'TRIS',data,indices=indices)
