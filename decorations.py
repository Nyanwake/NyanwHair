"""Closed tapered backs and linked local-axis mirrors without scene IDs."""
import math,heapq
from dataclasses import fields
from collections import defaultdict
from . import core,appearance


def _clone(result):
    out=core.MeshResult()
    for field in fields(out):setattr(out,field.name,getattr(result,field.name))
    out.vertices=list(result.vertices);out.vertex_keys=list(result.vertex_keys);out.faces=list(result.faces);out.owners=list(result.owners)
    out.extra_sharp_edges=set();out.replica_faces=set()
    return out


def _simplify(out,faces,owners,protected):
    try:import bmesh
    except ImportError:return faces,owners
    bm=bmesh.new()
    try:
        identity=bm.verts.layers.int.new('source');owner=bm.faces.layers.int.new('owner')
        used=sorted({v for f in faces for v in f});vertices={i:bm.verts.new(out.vertices[i]) for i in used}
        for i,v in vertices.items():v[identity]=i
        for ring,value in zip(faces,owners):bm.faces.new([vertices[i] for i in ring])[owner]=value
        bm.normal_update()
        edges=[edge for edge in bm.edges if len(edge.link_faces)==2 and edge.link_faces[0][owner]==edge.link_faces[1][owner] and edge.link_faces[0][owner] in protected[1] and core.edge_key(*(v[identity] for v in edge.verts)) not in protected[0]]
        if edges:bmesh.ops.dissolve_limit(bm,angle_limit=math.radians(5),use_dissolve_boundaries=False,verts=[],edges=edges,delimit=set())
        return [tuple(v[identity] for v in f.verts) for f in bm.faces],[int(f[owner]) for f in bm.faces]
    finally:bm.free()


def _back(out,result,axes=()):
    options={p.index:p.settings for p in result.plans_by_loop.values()};enabled={i for i,o in options.items() if o.back>0.}
    if not enabled:return
    selected=[(f,owner) for f,owner in zip(result.faces,result.owners) if owner in enabled]
    incidence=defaultdict(int);graph=defaultdict(dict);normals={};owners=defaultdict(set)
    for face,owner in selected:
        origin=result.vertices[face[0]];area=(0.,0.,0.)
        for i in range(1,len(face)-1):area=core.add(area,core.cross(core.sub(result.vertices[face[i]],origin),core.sub(result.vertices[face[i+1]],origin)))
        for v in face:normals[v]=core.add(normals.get(v,(0.,0.,0.)),area);owners[v].add(owner)
        for a,b in zip(face,face[1:]+face[:1]):
            key=core.edge_key(a,b);incidence[key]+=1;size=core.length(core.sub(result.vertices[a],result.vertices[b]));graph[a][b]=size;graph[b][a]=size
    all_owners=defaultdict(set)
    for face,owner in zip(result.faces,result.owners):
        for v in face:all_owners[v].add(owner)
    front_map={}
    for v in owners:
        if all_owners[v]-enabled:
            front_map[v]=len(out.vertices);out.vertices.append(result.vertices[v]);out.vertex_keys.append(('shell',result.vertex_keys[v]))
        else:front_map[v]=v
    for i,(face,owner) in enumerate(zip(result.faces,result.owners)):
        if owner in enabled:out.faces[i]=tuple(front_map[v] for v in face)
    sharp=appearance.sharp_edges(result)
    out.extra_sharp_edges.update(core.edge_key(front_map[a],front_map[b]) for a,b in sharp if a in front_map and b in front_map)
    def mirror_plane(edge):
        return any(enabled_axis and all(abs(result.vertices[v][axis])<1e-7 for v in edge) for axis,enabled_axis in enumerate(axes))
    boundary={edge for edge,count in incidence.items() if count==1};seeds={v for edge in boundary&sharp if not mirror_plane(edge) for v in edge}
    distances={v:0. for v in seeds};queue=[(0.,v) for v in seeds];heapq.heapify(queue)
    while queue:
        distance,v=heapq.heappop(queue)
        if distance!=distances[v]:continue
        for neighbor,size in graph[v].items():
            value=distance+size
            if value<distances.get(neighbor,float('inf')):distances[neighbor]=value;heapq.heappush(queue,(value,neighbor))
    widths={}
    for owner in enabled:
        sections=result.surface_sections[owner];sample=[sections[i] for i in {0,len(sections)//2,len(sections)-1}]
        widths[owner]=max(core.EPS,sum(core.length(core.sub(row[-1],row[0])) for row in sample)/len(sample))
    mapping={}
    for v,group in owners.items():
        amount=0.
        for owner in group:
            width=widths[owner];d=distances.get(v,width*.5)
            amount+=(abs(options[owner].bulge)+options[owner].back)*width*math.sin(min(math.pi*.5,math.pi*d/width))
        amount/=len(group)
        if amount<1e-9:mapping[v]=front_map[v];continue
        position=list(core.sub(result.vertices[v],core.mul(core.norm(normals[v]),amount)))
        for axis,on in enumerate(axes):
            if on and abs(result.vertices[v][axis])<1e-7:position[axis]=0.
        if core.length(core.sub(tuple(position),result.vertices[v]))<1e-9:mapping[v]=front_map[v];continue
        mapping[v]=len(out.vertices);out.vertices.append(tuple(position));out.vertex_keys.append(('back',result.vertex_keys[v]))
    back=[tuple(mapping[v] for v in reversed(face)) for face,owner in selected];back_owners=[owner for face,owner in selected]
    back_sharp={core.edge_key(mapping[a],mapping[b]) for a,b in sharp if a in mapping and b in mapping}
    simplifying={owner for owner in enabled if options[owner].back_simple}
    if simplifying:back,back_owners=_simplify(out,back,back_owners,(back_sharp,simplifying))
    out.faces.extend(back);out.owners.extend(back_owners);out.extra_sharp_edges.update(back_sharp)
    # The reverse traversal of each open boundary closes the shell.
    for face,owner in selected:
        for a,b in zip(face,face[1:]+face[:1]):
            if core.edge_key(a,b) not in boundary or mirror_plane((a,b)):continue
            ring=(front_map[b],front_map[a],mapping[a],mapping[b]);clean=[]
            for v in ring:
                if not clean or clean[-1]!=v:clean.append(v)
            if len(clean)>1 and clean[0]==clean[-1]:clean.pop()
            if len(set(clean))<3:continue
            out.faces.append(tuple(clean));out.owners.append(owner)
            out.extra_sharp_edges.add(core.edge_key(front_map[a],front_map[b]));out.extra_sharp_edges.add(core.edge_key(mapping[a],mapping[b]))


def build(result,axes=()):
    out=_clone(result);_back(out,result,axes)
    source_vertices=list(out.vertices);source_keys=list(out.vertex_keys);source_faces=list(out.faces);source_owners=list(out.owners)
    sharp=appearance.sharp_edges(out);lookup={};axis_bits=[1<<i for i,value in enumerate(axes) if value]
    seen_faces={frozenset(face) for face in source_faces};masks=[0]
    for bit in axis_bits:masks+=[value|bit for value in list(masks)]
    for mask in masks[1:]:
        mapping={}
        for i,p in enumerate(source_vertices):
            effective=mask
            for axis in range(3):
                if abs(p[axis])<1e-7:effective&=~(1<<axis)
            if not effective:mapping[i]=i;continue
            key=(i,effective)
            if key not in lookup:
                lookup[key]=len(out.vertices);out.vertices.append(tuple(-value if effective&(1<<axis) else value for axis,value in enumerate(p)));out.vertex_keys.append(('mirror',effective,source_keys[i]))
            mapping[i]=lookup[key]
        for face,owner in zip(source_faces,source_owners):
            ring=tuple(mapping[v] for v in face)
            identity=frozenset(ring)
            if identity in seen_faces:continue
            seen_faces.add(identity)
            if mask.bit_count()%2:ring=ring[::-1]
            out.replica_faces.add(len(out.faces));out.faces.append(ring);out.owners.append(owner)
        out.extra_sharp_edges.update(core.edge_key(mapping[a],mapping[b]) for a,b in sharp)
    used=sorted({v for face in out.faces for v in face});remap={v:i for i,v in enumerate(used)}
    out.vertices=[out.vertices[v] for v in used];out.vertex_keys=[out.vertex_keys[v] for v in used];out.faces=[tuple(remap[v] for v in face) for face in out.faces]
    out.extra_sharp_edges={core.edge_key(remap[a],remap[b]) for a,b in out.extra_sharp_edges if a in remap and b in remap}
    return out


def display(cache,obj):
    from . import groups
    group=groups.settings(obj,cache.get('group_id',1));axes=(group.mirror_x,group.mirror_y,group.mirror_z) if group else (False,)*3
    signature=(id(cache['result']),axes)
    if cache.get('display_signature')!=signature:
        cache['display_result']=build(cache['result'],axes);cache['display_signature']=signature
    return cache['display_result']
