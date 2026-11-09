"""Cached guide depth bounds derived from the displayed boundary, without scene writes."""
from bisect import bisect_right
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
    normals=cache.get('gpu_boundary_normals',{});old=cache.get('gpu_rail_records',{});records={};segments={};attributes={}
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
            values=[]
            for j in range(25):
                t=j/24.;i=max(0,min(len(knots)-2,bisect_right(knots,t)-1));factor=(t-knots[i])/max(1e-12,knots[i+1]-knots[i])
                point=Vector(evaluate(one,two,t));chord=Vector(points[i]).lerp(Vector(points[i+1]),factor)
                pair=[]
                for side in range(len(owners)):
                    n=Vector(rows[i][side]).lerp(Vector(rows[i+1][side]),factor);pair.append(tuple((normal_matrix@n).normalized()))
                if len(pair)==1:pair.append(pair[0])
                if not pair:pair=[(0.,0.,0.)]*2
                values.append((tuple(matrix@point),pair[0],pair[1],(*tuple(linear@(point-chord)),chord_error)))
            data={name:[values[index][column] for pair in zip(range(24),range(1,25)) for index in pair] for column,name in enumerate(('pos','normalA','normalB','curveGap'))}
        records[edge]=(stamp,data);segments[edge]=data['pos'];attributes[edge]=data
    cache['gpu_guide_geometry_signature']=signature
    return records,segments,attributes


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
