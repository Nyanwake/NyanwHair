"""An arc-length-sampled wire with a movable bend apex."""
from .i18n import tr
import bisect
from mathutils import Vector

MAX_POINTS=64

def positions(start,end,bend,intermediate,peak=.5):
    start=Vector(start);end=Vector(end);bend=Vector(bend);chord=end-start
    divisions=max(64,4*(intermediate+1))
    peak=max(.05,min(.95,peak))
    def profile(u):
        t=u/peak if u<=peak else (1.-u)/(1.-peak)
        return t*(2.-t)
    parameters=sorted(set([i/divisions for i in range(divisions+1)]+[peak]))
    dense=[start+chord*u+bend*profile(u) for u in parameters]
    divisions=len(dense)-1
    lengths=[0.]
    for a,b in zip(dense,dense[1:]):lengths.append(lengths[-1]+(b-a).length)
    out=[start.copy()];intervals=intermediate+1;apex=lengths[parameters.index(peak)]
    apex_index=max(1,min(intermediate,int(apex/max(1e-20,lengths[-1])*intervals+.5)))
    for i in range(1,intermediate+1):
        target=apex*i/apex_index if i<=apex_index else apex+(lengths[-1]-apex)*(i-apex_index)/(intervals-apex_index)
        j=min(divisions,max(1,bisect.bisect_left(lengths,target)))
        t=(target-lengths[j-1])/max(1e-20,lengths[j]-lengths[j-1]);out.append(dense[j-1].lerp(dense[j],t))
    out.append(end.copy());return out

def rebuild(bm,start_index,end_index,old_indices,coords):
    """Replace only vertices owned by this in-progress operator; retain endpoint identity."""
    from . import addon as a,editing
    bm.verts.ensure_lookup_table();start=bm.verts[start_index];end=bm.verts[end_index]
    old=[bm.verts[i] for i in old_indices]
    if any(v.link_faces for v in old):raise ValueError(tr('Curve points already belong to a surface'))
    if not old:
        edge=bm.edges.get((start,end))
        if edge is None or edge.link_faces:raise ValueError(tr('The curve edge has changed'))
        bm.edges.remove(edge)
    for vertex in old:bm.verts.remove(vertex)
    inside=[];uid=start[bm.verts.layers.int[a.L_VERTEX_GROUP]]
    for point in coords[1:-1]:
        vertex=bm.verts.new(point);vertex[bm.verts.layers.int[a.L_VERTEX_GROUP]]=uid;inside.append(vertex)
    end.co=coords[-1];chain=[start,*inside,end]
    for one,two in zip(chain,chain[1:]):bm.edges.new((one,two))
    editing._select(bm,[end]);bm.verts.index_update()
    return tuple(v.index for v in inside)
