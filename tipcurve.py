"""Add curve supports to a pointed extrusion without splitting its segments."""
from mathutils import Vector
import bmesh
from . import addon as a,editing,extrusion


def fractions(points):
    lengths=[0.]
    for one,two in zip(points,points[1:]):lengths.append(lengths[-1]+(two-one).length)
    return [value/max(1e-20,lengths[-1]) for value in lengths]


def insert(bm,data,points):
    bm.verts.ensure_lookup_table();bm.faces.index_update()
    records=extrusion._records(bm);caps=[(int(f[bm.faces.layers.int[a.L_ORDER]]),editing.boundary_vertices(f,bm)) for f in bm.faces]
    origin=Vector(data['opening'][0][0]);samples=[];splits=[];visited=set();flow=fractions(points)
    pairs=[(base,end,bm.verts[base],bm.verts[end]) for base,end,curve in data['opening'][0][3]]
    for base,end,one,tip in pairs:
        if (base,end) in visited:continue
        visited.add((base,end));offset=one.co-origin
        edge=bm.edges.get((one,tip));previous=one;count=len(points)-1
        for i,center in enumerate(points[1:-1],1):
            _,vertex=bmesh.utils.edge_split(edge,previous,1./(count-i+1))
            vertex.co=center+offset*(1.-flow[i]);vertex[bm.verts.layers.int[a.L_VERTEX_GROUP]]=one[bm.verts.layers.int[a.L_VERTEX_GROUP]]
            samples.append((vertex,offset.copy(),i));splits.append((previous,tip,vertex))
            previous=vertex;edge=bm.edges.get((previous,tip))
    for identity,slots in caps:
        face=next(f for f in bm.faces if int(f[bm.faces.layers.int[a.L_ORDER]])==identity)
        editing.assign_vertices(face,bm,slots)
    a._restore_directions(bm,records[0],splits);a._restore_density(bm,records[1],splits);a._restore_layout(bm,records[2],splits)
    bm.verts.index_update();bm.verts.ensure_lookup_table();editing._select(bm,[bm.verts[data['indices'][0]]])
    return tuple((v.index,tuple(offset),i) for v,offset,i in samples)


def move(bm,samples,points):
    bm.verts.ensure_lookup_table();flow=fractions(points)
    for index,offset,i in samples:bm.verts[index].co=points[i]+Vector(offset)*(1.-flow[i])
