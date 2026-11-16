"""Optional C kernels over owned numeric buffers; Python remains the fallback."""
import ctypes
import sys
import os
import hashlib
import tempfile
from pathlib import Path
import numpy as np

_library=False

def _get():
    global _library
    if _library is False:
        _library=None
        filename={'win32':'_kernels_windows_x64.dll','linux':'_kernels_linux_x64.so'}.get(sys.platform)
        if filename is None:return None
        path=Path(__file__).with_name(filename)
        if not path.is_file():return None
        try:
            data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
            base=Path(os.environ.get('LOCALAPPDATA' if sys.platform=='win32' else 'XDG_CACHE_HOME',Path.home()/'.cache'))
            cache=base/'NyanwHair'/'native'/digest
            cache.mkdir(mode=0o700,parents=True,exist_ok=True);target=cache/path.name
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
                with tempfile.NamedTemporaryFile(dir=cache,prefix='kernel-',suffix='.tmp',delete=False) as file:
                    file.write(data);temporary=Path(file.name)
                try:
                    try:os.replace(temporary,target)
                    except OSError:
                        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:raise
                finally:temporary.unlink(missing_ok=True)
            path=target
            lib=ctypes.CDLL(str(path))
            lib.nwh_abi.restype=ctypes.c_int
            if lib.nwh_abi()!=1:return None
            f=np.ctypeslib.ndpointer(dtype=np.float64,flags='C_CONTIGUOUS')
            i=np.ctypeslib.ndpointer(dtype=np.intp,flags='C_CONTIGUOUS')
            n=ctypes.c_ssize_t
            lib.nwh_projection.argtypes=[f,f,n,f,n,ctypes.c_double,f,f,f,n,f];lib.nwh_projection.restype=ctypes.c_int
            lib.nwh_zipper.argtypes=[f,n,f,n,i];lib.nwh_zipper.restype=n
            lib.nwh_geometry.argtypes=[f,n,f,n,f,f,i,n,i];lib.nwh_geometry.restype=n
            lib.nwh_normals.argtypes=[f,f,n,f,f];lib.nwh_normals.restype=ctypes.c_int
            lib.nwh_grid.argtypes=[f,f,f,n,f,f,n,ctypes.c_double,f,f,f];lib.nwh_grid.restype=ctypes.c_int
            u=np.ctypeslib.ndpointer(dtype=np.uint8,flags='C_CONTIGUOUS')
            lib.nwh_assembly.argtypes=[f,n,i,n,i,n,u,i,i,i,i,i,i,i];lib.nwh_assembly.restype=ctypes.c_int
            lib.nwh_shade.argtypes=[f,n,i,n,i,i,i,n,ctypes.c_int,f];lib.nwh_shade.restype=ctypes.c_int
            lib.nwh_locate.argtypes=[f,f,n,n,ctypes.c_double,f,n,i,f,i];lib.nwh_locate.restype=ctypes.c_int
            lib.nwh_evaluate.argtypes=[f,u,n,i,f,n,f];lib.nwh_evaluate.restype=ctypes.c_int
            _library=lib
        except (OSError,AttributeError):pass
    return _library

def available():return _get() is not None


def reset():
    global _library
    _library=False


def projection(curve,us,aa,ta):
    lib=_get()
    if lib is None or not hasattr(curve,'_vector_data'):return NotImplemented
    arc,parameters,coefs=curve._vector_data
    arc,parameters,coefs=(np.ascontiguousarray(a,dtype=np.float64) for a in (arc,parameters,coefs))
    u,a,t=(np.ascontiguousarray(v,dtype=np.float64) for v in (us,aa,ta))
    if u.ndim!=1 or a.shape!=(len(u),3) or t.shape!=a.shape or coefs.shape!=(len(coefs),4,3) or arc.ndim!=1 or parameters.shape!=arc.shape or not np.isfinite(curve.total):return NotImplemented
    out=np.empty(len(u))
    status=lib.nwh_projection(arc,parameters,len(arc),coefs,len(coefs),curve.total,u,a,t,len(u),out)
    return out.tolist() if status>0 else None if status==0 else NotImplemented

def zipper(a,b):
    lib=_get()
    if lib is None or min(len(a),len(b))<2:return NotImplemented
    a,b=(np.ascontiguousarray(v,dtype=np.float64) for v in (a,b))
    if a.ndim!=1 or b.ndim!=1:return NotImplemented
    out=np.empty((len(a)+1,2),dtype=np.intp)
    size=lib.nwh_zipper(a,len(a),b,len(b),out)
    return [tuple(v) for v in out[:size][::-1].tolist()] if size>0 else NotImplemented

def geometry(a,b,fa,fb,baseline):
    lib=_get()
    if lib is None:return NotImplemented
    a,b,fa,fb=(np.ascontiguousarray(v,dtype=np.float64) for v in (a,b,fa,fb))
    path=np.ascontiguousarray(baseline,dtype=np.intp)
    if a.shape!=(len(fa),3) or b.shape!=(len(fb),3) or path.ndim!=2 or path.shape[1]!=2:return NotImplemented
    out=np.empty((len(a)+len(b),2),dtype=np.intp)
    size=lib.nwh_geometry(a,len(a),b,len(b),fa,fb,path,len(path),out)
    return [tuple(v) for v in out[:size][::-1].tolist()] if size>0 else baseline if size==0 else NotImplemented


def normals(aa,bb,reference,packed=False):
    lib=_get()
    if lib is None:return NotImplemented
    a,b,ref=(np.ascontiguousarray(v,dtype=np.float64) for v in ([p for k,p in aa],[p for k,p in bb],reference))
    if a.ndim!=2 or a.shape[1]!=3 or b.shape!=a.shape or ref.shape!=(3,):return NotImplemented
    out=np.empty_like(a)
    status=lib.nwh_normals(a,b,len(a),ref,out)
    return (out if packed else out.tolist()) if status>0 else NotImplemented


def grid(aa,bb,normals,bulge,profile,blend,fractions,packed=False):
    lib=_get()
    if lib is None:return NotImplemented
    import math
    a,b,n,v,sine,weights=(np.ascontiguousarray(values,dtype=np.float64) for values in ([p for k,p in aa],[p for k,p in bb],normals,profile,[math.sin(math.pi*x) for x in profile],[blend.weights(u) for u in fractions]))
    if a.ndim!=2 or a.shape[1]!=3 or b.shape!=a.shape or n.shape!=a.shape or weights.shape!=(len(a),2) or v.ndim!=1:return NotImplemented
    shifts=np.zeros((len(v),2,3))
    for x in range(1,len(v)-1):
        for end,value in enumerate(blend.column(x/(len(v)-1))):
            if value is not None:shifts[x,end]=value
    out=np.empty((len(a),len(v),3))
    status=lib.nwh_grid(a,b,n,len(a),v,sine,len(v),bulge,weights,shifts,out)
    return (out if packed else out.tolist()) if status>0 else NotImplemented


def assemble(vertices,faces,owners,packed=False):
    lib=_get()
    if lib is None:return NotImplemented
    from itertools import chain
    points=np.ascontiguousarray(vertices,dtype=np.float64)
    if len(points) and points.shape!=(len(points),3):return NotImplemented
    if len(owners)!=len(faces):return NotImplemented
    from .packed import FaceStream,PackedFaces,SelectedRows,IndexMap
    numeric=faces.packed() if isinstance(faces,FaceStream) else faces if isinstance(faces,PackedFaces) else None
    if numeric is not None:offsets=numeric.offsets;flat=numeric.indices
    else:
        sizes=np.fromiter(map(len,faces),dtype=np.intp,count=len(faces));offsets=np.r_[0,np.cumsum(sizes)]
        flat=np.fromiter(chain.from_iterable(faces),dtype=np.intp,count=int(offsets[-1]))
    keep=np.empty(len(faces),dtype=np.uint8);remap=np.empty(len(vertices),dtype=np.intp);used=np.empty_like(remap)
    indices=np.empty_like(flat);next_vertices=np.empty_like(flat);face_ids=np.empty_like(flat);out_offsets=np.empty_like(offsets);counts=np.empty(3,dtype=np.intp)
    status=lib.nwh_assembly(points,len(points),flat,len(flat),offsets,len(faces),keep,remap,used,indices,out_offsets,next_vertices,face_ids,counts)
    if status<0:return NotImplemented
    nf,nv,nvalues=map(int,counts)
    if packed:
        compact=PackedFaces(out_offsets[:nf+1],indices[:nvalues]);positions=np.flatnonzero(keep)
        kept=SelectedRows(faces,positions);kept_owners=[owners[int(i)] for i in positions]
        layout=(out_offsets[:nf],indices[:nvalues],next_vertices[:nvalues],face_ids[:nvalues])
        for value in layout:value.setflags(write=False)
        return kept,kept_owners,used[:nv],IndexMap(remap),compact,(compact.signature,*layout)
    used=used[:nv].tolist();values=indices[:nvalues].tolist();bounds=out_offsets[:nf+1].tolist()
    compact=[tuple(values[a:b]) for a,b in zip(bounds,bounds[1:])]
    positions=np.flatnonzero(keep).tolist();kept=[faces[i] for i in positions];kept_owners=[owners[i] for i in positions]
    packed=(out_offsets[:nf],indices[:nvalues],next_vertices[:nvalues],face_ids[:nvalues])
    for values in packed:values.setflags(write=False)
    return kept,kept_owners,used,{old:new for new,old in enumerate(used)},compact,(tuple(compact),*packed)


def shade(points,topology,smooth):
    lib=_get()
    if lib is None:return NotImplemented
    _,offsets,vertices,next_vertices,face_ids,groups=topology
    if points.ndim!=2 or points.shape[1]!=3 or next_vertices.shape!=vertices.shape or offsets.ndim!=1 or vertices.ndim!=1 or smooth and groups.shape!=vertices.shape:return NotImplemented
    empty=np.empty(0,dtype=np.intp)
    out=np.empty((len(vertices),3))
    status=lib.nwh_shade(points,len(points),offsets,len(offsets),vertices,next_vertices,groups if smooth else empty,len(vertices),int(smooth),out)
    return out if status>0 else NotImplemented


def sample(curve,values):
    lib=_get()
    if lib is None or len(values)<12 or not hasattr(curve.evaluate,'coefficients'):return NotImplemented
    data=getattr(curve,'_native_sample_data',None)
    if data is None:
        coefficients=[];reversed_=[];edges=[]
        for a,b in zip(curve.path,curve.path[1:]):
            lo,hi=(a,b) if a<b else (b,a);edges.append((lo,hi))
            c=curve.evaluate.coefficients.get((lo,hi));reversed_.append(c is None)
            coefficients.append(c if c is not None else curve.evaluate.coefficients[hi,lo])
        data=(np.ascontiguousarray(curve.offsets,dtype=np.float64),np.ascontiguousarray(curve.tables,dtype=np.float64),np.ascontiguousarray(coefficients,dtype=np.float64),np.asarray(reversed_,dtype=np.uint8),edges)
        curve._native_sample_data=data
    offsets,tables,coefficients,reversed_,edges=data
    if offsets.shape!=(len(edges)+1,) or tables.ndim!=2 or tables.shape[0]!=len(edges) or coefficients.shape!=(len(edges),4,3):return NotImplemented
    u=np.ascontiguousarray(values,dtype=np.float64);indices=np.empty(len(u),dtype=np.intp);parameters=np.empty(len(u));vertex=np.empty_like(indices)
    status=lib.nwh_locate(offsets,tables,len(edges),tables.shape[1]-1,curve.total,u,len(u),indices,parameters,vertex)
    if status<0:return NotImplemented
    keys=[];endpoints={}
    for k,(i,t,v) in enumerate(zip(indices.tolist(),parameters.tolist(),vertex.tolist())):
        lo,hi=edges[i]
        if v>=0:key=('v',curve.path[v]);endpoints[k]=curve.points[key[1]]
        else:
            value=round(t if curve.path[i]==lo else 1.-t,10);parameters[k]=value
            if value<=1e-10:key=('v',lo);endpoints[k]=curve.points[lo]
            elif value>=1.-1e-10:key=('v',hi);endpoints[k]=curve.points[hi]
            else:key=('s',lo,hi,value)
        keys.append(key)
    output=np.empty((len(u),3))
    status=lib.nwh_evaluate(coefficients,reversed_,len(edges),indices,parameters,len(u),output)
    if status<0:return NotImplemented
    positions=output.tolist()
    return {u:(key,endpoints[k] if k in endpoints else tuple(p)) for k,(u,key,p) in enumerate(zip(values,keys,positions))}
