"""The shared starter shape, in metres, for new objects and empty groups."""
POINTS=((.008,0.,0.),(-.039760862,-.023122435,.091648161),
        (-.068638220,-.024972228,.244375512),(-.038,0.,.335),
        (.039,0.,.335),(.069638222,-.024972228,.244375512),
        (.042760860,-.023122435,.091648161))
RING=(0,6,5,4,3,2,1)


def add(bm,group=1):
    from . import addon as a
    a._prepare_bmesh(bm,group_id=group)
    order=bm.faces.layers.int[a.L_ORDER]
    identity=max((int(f[order]) for f in bm.faces),default=0)+1
    vertices=[bm.verts.new(point) for point in POINTS]
    face=bm.faces.new([vertices[i] for i in RING]);face[order]=identity
    face[bm.faces.layers.int[a.L_GROUP]]=group
    for name,value in (('width',4),('bulge',.08)):
        a._set_zone(face,bm,name,value)
    for vertex in vertices:vertex[bm.verts.layers.int[a.L_VERTEX_GROUP]]=group
    a._write_boundary_pair(face,bm,(('edge',3),('point',0)))
    face.select_set(True)
    return vertices,face
