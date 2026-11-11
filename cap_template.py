"""Hair-cap control geometry supplied by Nyanwake; metres, centred on the head axis."""
POINTS=((0.0, -0.1777614504, 0.1444406062),
 (0.0, -0.0884242952, 0.1868460476),
 (0.0, 0.0693780184, 0.1956362724),
 (0.0, 0.1897996515, 0.1538745612),
 (0.2354748249, -0.1777614504, 0.09505108),
 (0.1192120388, -0.1777614504, 0.1491867304),
 (0.2676810026, -0.0836296231, 0.1256474257),
 (0.1402182877, -0.0884242952, 0.1960733533),
 (0.2671530247, 0.0693780184, 0.1246041283),
 (0.1399412453, 0.0693780184, 0.2053037733),
 (0.2365307659, 0.1897996515, 0.0867269933),
 (0.1220349222, 0.1897996515, 0.1590594202),
 (-0.0, 0.279699713, 0.0927144065),
 (0.0996051431, 0.2736773491, 0.1053193063),
 (0.1842538416, 0.2629614472, 0.0421289317))
FACES=((0, 5, 4, 6, 7, 1), (1, 7, 6, 8, 9, 2), (3, 2, 9, 8, 10, 11), (3, 11, 10, 14, 13, 12))
CAPS=((('edge', 5), ('edge', 2)), (('edge', 5), ('edge', 2)), (('edge', 0), ('edge', 3)), (('edge', 5), ('edge', 2)))
REFERENCE_LENGTH=0.366441935300827

def add(bm,group=1):
    from . import addon as a
    a._prepare_bmesh(bm,group_id=group)
    order=bm.faces.layers.int[a.L_ORDER];identity=max((int(f[order]) for f in bm.faces),default=0)+1
    vertices=[bm.verts.new(point) for point in POINTS]
    for index,(ring,caps) in enumerate(zip(FACES,CAPS)):
        face=bm.faces.new([vertices[i] for i in ring]);face[order]=identity+index
        face[bm.faces.layers.int[a.L_GROUP]]=group
        for name,value in (('width',2),('bulge',0.)):a._set_zone(face,bm,name,value)
        a._write_boundary_pair(face,bm,caps);face.select_set(True)
    for vertex in vertices:vertex[bm.verts.layers.int[a.L_VERTEX_GROUP]]=group
    return vertices
