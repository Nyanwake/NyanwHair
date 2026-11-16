"""Validate one packed face stream; cache connectivity without vertex positions."""
from itertools import chain,groupby
import numpy as np
from .core import EPS
from .mesh_views import OwnerEdges
from .i18n import tr


def validity(vertices,faces):
    if not faces:return ()
    sizes=np.fromiter(map(len,faces),dtype=np.intp,count=len(faces))
    offsets=np.r_[0,np.cumsum(sizes)]
    flat=np.fromiter(chain.from_iterable(faces),dtype=np.intp,count=int(offsets[-1]))
    following=np.arange(len(flat))+1;following[offsets[1:]-1]=offsets[:-1]
    points=np.asarray(vertices,dtype=np.float64);a=points[flat];b=a[following]
    components=(a-b)*np.roll(a+b,-1,axis=1)
    vectors=np.add.reduceat(np.roll(components,-1,axis=1),offsets[:-1],axis=0)
    square=np.einsum('ij,ij->i',a-b,a-b)
    scale=np.maximum.reduceat(square,offsets[:-1])
    return np.einsum('ij,ij->i',vectors,vectors)>(EPS*scale)**2


def filter_faces(vertices,faces,owners,valid=None):
    if valid is None:valid=validity(vertices,faces)
    seen=set();output=[];kept=[]
    for face,owner,keep in zip(faces,owners,valid):
        if not keep:continue
        key=tuple(sorted(face))
        if key in seen:continue
        seen.add(key);output.append(face);kept.append(owner)
    return output,kept


def build(vertices,faces,owners,previous,limit,registry,index):
    from . import kernels
    native=kernels.assemble(vertices,faces,owners) if not registry.needs_stitch else NotImplemented
    if native is not NotImplemented:
        kept,kept_owners,used,remap,compact,corner_layout=native
        if len(kept)>limit:raise ValueError(tr('Polygon limit exceeded'))
        edges=OwnerEdges(compact,kept_owners)
        return kept,kept_owners,used,remap,compact,edges,None,corner_layout
    valid=tuple(bool(v) for v in validity(vertices,faces))
    signature=(len(vertices),tuple(faces),tuple(owners),valid,bool(registry.needs_stitch))
    if previous and previous[0]==signature:return (*previous[1:],previous,None)
    kept,kept_owners=filter_faces(vertices,faces,owners,valid)
    if len(kept)>limit:raise ValueError(tr('Polygon limit exceeded'))
    kept,kept_owners=registry.stitch(vertices,kept,kept_owners,index)
    if len(kept)>limit:raise ValueError(tr('Polygon limit exceeded after stitching'))
    used=sorted({v for face in kept for v in face});remap={old:new for new,old in enumerate(used)}
    mapped=np.full(len(vertices),-1,dtype=np.intp);mapped[used]=np.arange(len(used))
    compact=[]
    for size,run in groupby(kept,key=len):
        matrix=np.asarray(list(run),dtype=np.intp)
        dtype=np.dtype(','.join([matrix.dtype.str]*size))
        compact.extend(mapped[matrix].view(dtype).reshape(-1).tolist())
    edges=OwnerEdges(compact,kept_owners)
    saved=(signature,kept,kept_owners,used,remap,compact,edges)
    return kept,kept_owners,used,remap,compact,edges,saved,None
