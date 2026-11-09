"""A temporary quadratic Bezier wire, sampled at approximately equal arc length."""
import bisect
from mathutils import Vector

MAX_POINTS=64

def positions(start,end,bend,intermediate):
    start=Vector(start);end=Vector(end);bend=Vector(bend);chord=end-start
    divisions=max(64,4*(intermediate+1))
    dense=[start+chord*(i/divisions)+bend*(4*(i/divisions)*(1-i/divisions)) for i in range(divisions+1)]
    lengths=[0.]
    for a,b in zip(dense,dense[1:]):lengths.append(lengths[-1]+(b-a).length)
    out=[start.copy()]
    for i in range(1,intermediate+1):
        target=lengths[-1]*i/(intermediate+1);j=min(divisions,max(1,bisect.bisect_left(lengths,target)))
        t=(target-lengths[j-1])/max(1e-20,lengths[j]-lengths[j-1]);out.append(dense[j-1].lerp(dense[j],t))
    out.append(end.copy());return out

def rebuild(bm,start_index,end_index,old_indices,coords):
    """Replace only vertices owned by this in-progress operator; retain endpoint identity."""
    from . import addon as a,editing
    bm.verts.ensure_lookup_table();start=bm.verts[start_index];end=bm.verts[end_index]
    old=[bm.verts[i] for i in old_indices]
    if any(v.link_faces for v in old):raise ValueError('Точки текущей кривой уже принадлежат поверхности')
    if not old:
        edge=bm.edges.get((start,end))
        if edge is None or edge.link_faces:raise ValueError('Ребро текущей кривой изменилось')
        bm.edges.remove(edge)
    for vertex in old:bm.verts.remove(vertex)
    inside=[];uid=start[bm.verts.layers.int[a.L_VERTEX_GROUP]]
    for point in coords[1:-1]:
        vertex=bm.verts.new(point);vertex[bm.verts.layers.int[a.L_VERTEX_GROUP]]=uid;inside.append(vertex)
    end.co=coords[-1];chain=[start,*inside,end]
    for one,two in zip(chain,chain[1:]):bm.edges.new((one,two))
    editing._select(bm,[end]);bm.verts.index_update()
    return tuple(v.index for v in inside)
