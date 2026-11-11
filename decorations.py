"""Linked local-axis Mirror previews and Mesh copies, without scene IDs."""
from dataclasses import fields
from collections import defaultdict
from . import core,appearance


def _clone(result,copy_mesh=True):
    out=core.MeshResult()
    for field in fields(out):setattr(out,field.name,getattr(result,field.name))
    if copy_mesh:
        out.vertices=list(result.vertices);out.vertex_keys=list(result.vertex_keys);out.faces=list(result.faces);out.owners=list(result.owners)
    out.source_result=result
    out.extra_sharp_edges=set(getattr(result,'extra_sharp_edges',()));out.replica_faces=set()
    return out


def build(result,axes=(),replicate=True,merge=True):
    out=_clone(result,copy_mesh=bool(any(axes) and replicate))
    if not any(axes) or not replicate:return out
    source_vertices=list(out.vertices);source_keys=list(out.vertex_keys);source_faces=list(out.faces);source_owners=list(out.owners)
    sharp=appearance.sharp_edges(out);lookup={};axis_bits=[1<<i for i,value in enumerate(axes) if value]
    seen_faces={frozenset(face) for face in source_faces};masks=[0]
    for bit in axis_bits:masks+=[value|bit for value in list(masks)]
    for mask in (masks[1:] if replicate else ()):
        mapping={}
        for i,p in enumerate(source_vertices):
            effective=mask
            for axis in range(3):
                if merge and abs(p[axis])<1e-7:effective&=~(1<<axis)
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
    merge=group.mirror_merge if group else True
    signature=(id(cache['result']),axes,merge)
    if cache.get('display_signature')!=signature:
        cache['display_result']=build(cache['result'],axes,merge=merge)
        cache['display_signature']=signature
    return cache['display_result']


def axes_for(cache,obj):
    from . import groups
    group=groups.settings(obj,cache.get('group_id',1))
    return (group.mirror_x,group.mirror_y,group.mirror_z) if group else (False,)*3


def preview(cache,obj):
    """One source mesh for GPU copies; the bake retains welded CPU geometry."""
    from . import groups
    group=groups.settings(obj,cache.get('group_id',1));merge=group.mirror_merge if group else True
    axes=axes_for(cache,obj);signature=(id(cache['result']),axes,merge)
    if cache.get('preview_signature')!=signature:
        base=build(cache['result'],axes,replicate=False)
        base.preview_merge=merge
        # Fully planar faces need the old face-deduplication/nonmanifold rules.
        # Keep that uncommon case exact rather than drawing coincident copies.
        import numpy as np
        pending=any(axes) and bool(base.faces)
        planar=False;effective=axes
        if pending:
            offsets=[];vertices=[]
            for face in base.faces:offsets.append(len(vertices));vertices.extend(face)
            base.preview_on_plane=np.abs(np.asarray(base.vertices))<1e-7
            on=base.preview_on_plane[vertices]
            effective=tuple(bool(value and not on[:,axis].all()) for axis,value in enumerate(axes))
            planar=bool(np.any(np.logical_and.reduceat(on,offsets,axis=0)[:,np.asarray(effective,dtype=bool)]))
        if planar:base=display(cache,obj)
        else:
            if pending and any(effective) and merge:
                # An internal edge on a mirror plane has four incident faces
                # in the welded output. Its original smoothing connection is
                # therefore absent there as well.
                active_bits=sum(1<<axis for axis,on in enumerate(effective) if on)
                flags=(base.preview_on_plane.astype(np.uint8)@np.asarray((1,2,4),dtype=np.uint8))&active_bits
                if flags.any():
                    key=(tuple(base.faces),flags.tobytes());saved=cache.get('preview_axis_sharp')
                    if saved and saved[0]==key:internal=saved[1]
                    else:
                        counts=defaultdict(int);bits=flags.tolist()
                        for face in base.faces:
                            for a,b in zip(face,face[1:]+face[:1]):
                                if bits[a]&bits[b]:counts[core.edge_key(a,b)]+=1
                        internal={edge for edge,count in counts.items() if count>1}
                        cache['preview_axis_sharp']=(key,internal)
                    base.extra_sharp_edges.update(internal)
            masks=[0]
            for axis,on in enumerate(effective):
                if on:masks+=[value|(1<<axis) for value in list(masks)]
            base.preview_masks=tuple(masks);base.preview_axes=effective
        cache['preview_result']=base;cache['preview_signature']=signature
    return cache['preview_result']


def instance_matrices(result,matrix):
    """World transforms of the same local-axis reflection used by the bake."""
    from mathutils import Matrix
    inverse=matrix.inverted_safe()
    return tuple((mask,matrix@Matrix.Diagonal(tuple(-1. if mask&(1<<i) else 1. for i in range(3))+(1.,))@inverse if mask else Matrix.Identity(4)) for mask in getattr(result,'preview_masks',(0,)))


def preview_normals(result,smooth,normals,cache=None,packed=False):
    """Match welded seam normals without evaluating every reflected polygon."""
    axes=getattr(result,'preview_axes',())
    if not smooth or not any(axes) or not result.faces or not getattr(result,'preview_merge',True):return normals
    on_plane=getattr(result,'preview_on_plane',None)
    if on_plane is not None and not on_plane[:,axes].any():return normals
    old=getattr(result,'preview_normal_cache',None)
    if old and old[0] is normals:return old[1] if packed else tuple(tuple(tuple(v) for v in face) for face in old[1])
    import numpy as np
    topology=result.normal_topology;offsets,vertices,_,_,groups=topology[1:]
    signature=(id(topology),tuple(axes),on_plane.tobytes() if on_plane is not None else None)
    stored=cache.get('preview_normal_groups') if cache is not None else None
    if stored and stored[0]==signature:bits=stored[1]
    else:
        sharp=topology[0][2];incidence=defaultdict(list)
        for fi,face in enumerate(result.faces):
            for j,a in enumerate(face):
                b=face[(j+1)%len(face)];incidence[core.edge_key(a,b)].append((int(offsets[fi])+j,int(offsets[fi])+(j+1)%len(face)))
        points=np.asarray(result.vertices);group_bits={}
        for (a,b),corners in incidence.items():
            if len(corners)!=1 or (a,b) in sharp:continue
            bits_here=sum(1<<axis for axis,on in enumerate(axes) if on and abs(points[a,axis])<1e-7 and abs(points[b,axis])<1e-7)
            if bits_here:
                for corner in corners[0]:
                    g=int(groups[corner]);group_bits[g]=group_bits.get(g,0)|bits_here
        bits=np.asarray([group_bits.get(int(g),0) for g in groups])
        if cache is not None:cache['preview_normal_groups']=(signature,bits)
    if not np.any(bits):return normals
    values=np.asarray([normal for face in normals for normal in face],dtype=float)
    for axis,on in enumerate(axes):
        if on:values[(bits&(1<<axis))!=0,axis]=0.
    sizes=np.linalg.norm(values,axis=1);values/=np.maximum(sizes,1e-12)[:,None]
    output=tuple(values[offset:offset+len(face)] for offset,face in zip(offsets,result.faces))
    result.preview_normal_array=values
    result.preview_normal_cache=(normals,output)
    return output if packed else tuple(tuple(tuple(v) for v in face) for face in output)
