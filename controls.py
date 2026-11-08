"""Pastel endpoint guides for the currently active segment."""
from mathutils import Vector
COLORS=((.52,.80,.62,1.),(.72,.59,.85,1.))

def caps(bm,obj,cache):
    from . import addon as a
    face=a._zone_ui_primary(bm,obj)
    if face is None:return [],{}
    raw=a._read_caps(face,a._boundary_layer(bm));result=cache['result'];vertices={};lines=[]
    if raw is None:return [],{}
    signature=(id(result),face.index,tuple(raw),tuple(float(v) for row in obj.matrix_world for v in row))
    if cache.get('endpoint_signature')==signature:return cache['endpoint_guides']
    lookup={key:pos for key,pos in zip(result.vertex_keys,result.vertices)};loops=list(face.loops)
    for side,cap in enumerate(raw):
        if cap is None or cap[0] not in ('point','edge'):continue
        kind,i=cap;one=loops[i].vert;vertices[one.index]=side
        if kind=='point':lines.append((side,[tuple(obj.matrix_world@one.co)]));continue
        two=loops[(i+1)%len(loops)].vert;vertices[two.index]=side;lo,hi=sorted((one.index,two.index))
        positions=[(0.,lookup.get(('v',lo),tuple(one.co if one.index==lo else two.co))),(1.,lookup.get(('v',hi),tuple(two.co if two.index==hi else one.co)))]
        for key,point in lookup.items():
            if key[0]=='s' and key[1:3]==(lo,hi):positions.append((key[3],point))
            elif key[0]=='cap' and key[1:3]==(lo,hi):positions.append((key[3]/key[4],point))
        lines.append((side,[tuple(obj.matrix_world@Vector(point)) for t,point in sorted(positions)]))
    cache.update(endpoint_signature=signature,endpoint_guides=(lines,vertices))
    return lines,vertices
