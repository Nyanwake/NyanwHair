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


def _enabled(option):return option.back_enabled if option.back_enabled is not None else option.back>0.


def _back(out,result,axes=(),previous=None):
    from dataclasses import replace
    plans=list(result.plans_by_loop.values());options={p.index:p.settings for p in plans}
    enabled={owner for owner,o in options.items() if _enabled(o)}
    if not enabled:return None
    # Reuse the same controls, curve dependencies, length detail and density.
    # Only transverse resolution and convexity belong to the reverse sheet.
    settings=[replace(p.settings,width=max(2,p.settings.back_width or p.settings.width),bulge=-p.settings.back,back=0.,back_enabled=False,back_simple=False) for p in plans]
    back=core.generate(result.source_points,[p.boundary for p in plans],settings,rows=result.base_rows,
        caps=[(p.start,p.end) for p in plans],face_indices=[p.index for p in plans],previous=previous or result,
        reference_length=result.reference_length,directional_smoothing=result.directional_smoothing,
        local_density=result.local_density,point_density=result.point_density,layout_lengths=result.layout_lengths,regular_rows=result.regular_rows)
    enabled.intersection_update(back.owners)
    front_faces=[(f,o) for f,o in zip(result.faces,result.owners) if o in enabled]
    back_faces=[(f,o) for f,o in zip(back.faces,back.owners) if o in enabled]
    if not front_faces or not back_faces:return back
    front_lookup={key:i for i,key in enumerate(result.vertex_keys)}
    def plane(edge):return any(on and all(abs(back.vertices[v][axis])<1e-7 for v in edge) for axis,on in enumerate(axes))
    incidence=defaultdict(int);graph=defaultdict(dict);normals={};owners=defaultdict(set)
    for face,owner in back_faces:
        origin=back.vertices[face[0]];area=(0.,0.,0.)
        for i in range(1,len(face)-1):area=core.add(area,core.cross(core.sub(back.vertices[face[i]],origin),core.sub(back.vertices[face[i+1]],origin)))
        for v in face:normals[v]=core.add(normals.get(v,(0.,0.,0.)),area);owners[v].add(owner)
        for a,b in zip(face,face[1:]+face[:1]):
            incidence[core.edge_key(a,b)]+=1;size=core.length(core.sub(back.vertices[a],back.vertices[b]));graph[a][b]=size;graph[b][a]=size
    boundary={edge for edge,count in incidence.items() if count==1}
    outer={v for edge in boundary if not plane(edge) for v in edge}
    # Internal guide lines separate the two sheets. A harmonic interpolation
    # extends their displacement into each patch without pinching shared rails.
    distances={v:0. for v in outer};queue=[(0.,v) for v in outer];heapq.heapify(queue)
    while queue:
        distance,v=heapq.heappop(queue)
        if distance!=distances[v]:continue
        for neighbor,size in graph[v].items():
            candidate=distance+size
            if candidate<distances.get(neighbor,float('inf')):distances[neighbor]=candidate;heapq.heappush(queue,(candidate,neighbor))
    widths={}
    for owner in enabled:
        rows=back.surface_sections[owner];sample=[rows[i] for i in {0,len(rows)//2,len(rows)-1}]
        widths[owner]=max(core.EPS,sum(core.length(core.sub(row[-1],row[0])) for row in sample)/len(sample))
    guides={v for edge in appearance.sharp_edges(back) if edge in incidence for v in edge}
    fixed={v:0. for v in outer}
    for v in guides-outer:
        fixed[v]=sum(options[o].back*widths[o]*math.sin(min(math.pi*.5,math.pi*distances.get(v,widths[o]*.5)/widths[o])) for o in owners[v])/len(owners[v])
    heights=dict(fixed)
    if any(value>core.EPS for value in fixed.values()):
        used=sorted(graph);ids={v:i for i,v in enumerate(used)}
        try:import numpy as np
        except ImportError:
            for _ in range(48):
                heights={v:fixed[v] if v in fixed else sum(heights.get(n,0.)/max(core.EPS,size) for n,size in graph[v].items())/sum(1./max(core.EPS,size) for size in graph[v].values()) for v in used}
        else:
            edges=[(ids[a],ids[b],1./max(core.EPS,size)) for a in used for b,size in graph[a].items()]
            source=np.asarray([a for a,b,w in edges]);target=np.asarray([b for a,b,w in edges]);weights=np.asarray([w for a,b,w in edges]);denom=np.bincount(source,weights=weights,minlength=len(used))
            values=np.asarray([fixed.get(v,0.) for v in used]);pinned=np.asarray([ids[v] for v in fixed]);pinned_values=values[pinned].copy()
            for _ in range(48):
                new=.25*values+.75*np.bincount(source,weights=weights*values[target],minlength=len(used))/np.maximum(core.EPS,denom);new[pinned]=pinned_values
                delta=np.max(np.abs(new-values));values=new
                if delta<1e-7:break
            heights={v:float(values[ids[v]]) for v in used}
    all_owners=defaultdict(set)
    for face,owner in zip(result.faces,result.owners):
        for v in face:all_owners[v].add(owner)
    used_front={v for f,o in front_faces for v in f};front_map={}
    for v in used_front:
        if all_owners[v]-enabled:front_map[v]=len(out.vertices);out.vertices.append(result.vertices[v]);out.vertex_keys.append(('shell',result.vertex_keys[v]))
        else:front_map[v]=v
    for i,(face,owner) in enumerate(zip(result.faces,result.owners)):
        if owner in enabled:out.faces[i]=tuple(front_map[v] for v in face)
    mapping={}
    for v in graph:
        position=list(core.sub(back.vertices[v],core.mul(core.norm(normals[v]),heights.get(v,0.))))
        for axis,on in enumerate(axes):
            if on and abs(back.vertices[v][axis])<1e-7:position[axis]=0.
        position=tuple(position);front=front_lookup.get(back.vertex_keys[v])
        if v in outer and front in front_map and core.length(core.sub(position,result.vertices[front]))<1e-7:mapping[v]=front_map[front]
        else:mapping[v]=len(out.vertices);out.vertices.append(position);out.vertex_keys.append(('back',back.vertex_keys[v]))
    out.faces.extend(tuple(mapping[v] for v in reversed(face)) for face,owner in back_faces);out.owners.extend(owner for face,owner in back_faces)
    sharp=appearance.sharp_edges(result)
    out.extra_sharp_edges.update(core.edge_key(front_map[a],front_map[b]) for a,b in sharp if a in front_map and b in front_map)
    out.extra_sharp_edges.update(core.edge_key(mapping[a],mapping[b]) for a,b in appearance.sharp_edges(back) if a in mapping and b in mapping)
    source_owners=defaultdict(set);cap_owner={}
    for p in plans:
        if p.index not in enabled:continue
        for a,b in zip(p.boundary,p.boundary[1:]+p.boundary[:1]):source_owners[core.edge_key(a,b)].add(p.index)
        for kind,i in (p.start,p.end):
            if kind=='edge':cap_owner[core.edge_key(p.boundary[i],p.boundary[(i+1)%len(p.boundary)])]=p.index
    def samples(mesh,edge):
        found=[]
        for i,key in enumerate(mesh.vertex_keys):
            if key[0]=='v' and key[1] in edge:found.append((float(key[1]==edge[1]),i))
            elif key[0] in ('s','cap') and key[1:3]==edge:found.append((key[3] if key[0]=='s' else key[3]/key[4],i))
        return sorted(found)
    front_directions={}
    for face,owner in front_faces:
        for a,b in zip(face,face[1:]+face[:1]):front_directions[core.edge_key(a,b)]=(a,b)
    from .ribbon import zipper_flows
    for edge,owner in cap_owner.items():
        if len(source_owners[edge])!=1:continue
        fs=samples(result,edge);bs=samples(back,edge)
        if len(fs)<2 or len(bs)<2 or any(on and all(abs(result.source_points[v][axis])<1e-7 for v in edge) for axis,on in enumerate(axes)):continue
        forward=front_directions.get(core.edge_key(fs[0][1],fs[1][1]))==(fs[0][1],fs[1][1])
        path=zipper_flows([t for t,v in fs],[t for t,v in bs])
        for (i,j),(ni,nj) in zip(path,path[1:]):
            ring=(front_map[fs[ni][1]],front_map[fs[i][1]],mapping[bs[j][1]],mapping[bs[nj][1]])
            clean=[]
            for v in ring:
                if not clean or clean[-1]!=v:clean.append(v)
            if len(clean)>1 and clean[0]==clean[-1]:clean.pop()
            if len(set(clean))<3:continue
            if not forward:clean.reverse()
            out.faces.append(tuple(clean));out.owners.append(owner)
    return back


def build(result,axes=(),previous_back=None):
    out=_clone(result);out.back_result=_back(out,result,axes,previous_back)
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
        cache['display_result']=build(cache['result'],axes,cache.get('back_result'));cache['back_result']=cache['display_result'].back_result;cache['display_signature']=signature
    return cache['display_result']
