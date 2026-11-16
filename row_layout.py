"""Local cross-section matching; physical border samples stay shared."""
import math
from .core import EPS, add, sub, mul, dot, cross, length, norm
try:
    import numpy as np
except ImportError:
    np=None


def profile_normals(aa,bb,reference):
    from . import kernels
    accelerated=kernels.normals(aa,bb,reference)
    if accelerated is not NotImplemented:return accelerated
    from .surface import _rotate
    if np is None:
        centers=[mul(add(a,b),.5) for (_,a),(_,b) in zip(aa,bb)];last=len(centers)-1
        tangents=[norm(sub(centers[min(last,i+1)],centers[max(0,i-1)])) for i in range(last+1)]
        output=[];previous=reference;old=tangents[0]
        for i,((key,a),(other,b)) in enumerate(zip(aa,bb)):
            transported=_rotate(previous,old,tangents[i]);normal=norm(cross(sub(b,a),tangents[i]))
            if length(normal)<EPS:normal=transported
            if dot(normal,transported)<0.:normal=mul(normal,-1.)
            output.append(normal);previous=normal;old=tangents[i]
        return output
    a=np.asarray([p for key,p in aa]);b=np.asarray([p for key,p in bb]);centers=(a+b)*.5
    indices=np.arange(len(centers));tangent=centers[np.minimum(indices+1,len(indices)-1)]-centers[np.maximum(0,indices-1)]
    size=np.linalg.norm(tangent,axis=1)
    tangent=np.divide(tangent,size[:,None],out=np.zeros_like(tangent),where=size[:,None]>EPS)
    raw=np.cross(b-a,tangent);size=np.linalg.norm(raw,axis=1)
    raw=np.divide(raw,size[:,None],out=np.zeros_like(raw),where=size[:,None]>EPS)
    for i in np.flatnonzero(size<=EPS):
        raw[i]=reference if i==0 else _rotate(tuple(raw[i-1]),tuple(tangent[i-1]),tuple(tangent[i]))
    axis=np.cross(tangent[:-1],tangent[1:]);sine=np.linalg.norm(axis,axis=1)
    axis=np.divide(axis,sine[:,None],out=np.zeros_like(axis),where=sine[:,None]>EPS)
    cosine=np.clip(np.einsum('ij,ij->i',tangent[:-1],tangent[1:]),-1.,1.)
    previous=raw[:-1]
    transported=previous*cosine[:,None]+np.cross(axis,previous)*sine[:,None]+axis*(np.einsum('ij,ij->i',axis,previous)*(1.-cosine))[:,None]
    transported[sine<EPS]=previous[sine<EPS]
    signs=np.ones(len(raw));signs[0]=-1. if float(raw[0]@np.asarray(reference))<0. else 1.
    signs[1:]=np.where(np.einsum('ij,ij->i',raw[1:],transported)<0.,-1.,1.)
    return (raw*np.cumprod(signs)[:,None]).tolist()


def profile_grid(aa,bb,normals,bulge,profile,blend,fractions):
    from . import kernels
    accelerated=kernels.grid(aa,bb,normals,bulge,profile,blend,fractions)
    if accelerated is not NotImplemented:return accelerated
    if np is None:return None
    a=np.asarray([p for key,p in aa]);b=np.asarray([p for key,p in bb])
    v=np.asarray(profile);sine=np.asarray([math.sin(math.pi*x) for x in profile])
    width=np.sqrt(np.sum((b-a)**2,axis=1))
    amount=bulge*width[:,None]*sine[None,:]
    positions=(a[:,None,:]*(1.-v)[None,:,None]+b[:,None,:]*v[None,:,None])+np.asarray(normals)[:,None,:]*amount[:,:,None]
    weights=np.asarray([blend.weights(u) for u in fractions])
    for x in range(1,len(profile)-1):
        for end,shift in enumerate(blend.column(x/(len(profile)-1))):
            if shift is not None:positions[:,x,:]+=weights[:,end,None]*np.asarray(shift)
    return positions.tolist()


def station(values,t):
    x=t*(len(values)-1);i=min(len(values)-2,int(x));blend=x-i
    return values[i]*(1.-blend)+values[i+1]*blend


def correspondence(registry,a,b):
    values=[i/16 for i in range(17)]
    pairs=[(one[1],two[1]) for one,two in zip(a.sample(values),b.sample(values))]
    centers=[mul(add(p,q),.5) for p,q in pairs]
    skew=[]
    for i,(p,q) in enumerate(pairs[1:-1],1):
        width=sub(q,p);tangent=sub(centers[i+1],centers[i-1])
        if length(width)>EPS and length(tangent)>EPS:skew.append(abs(dot(norm(width),norm(tangent))))
    if not skew or max(skew)<.5:return None
    registry.pair(a.path,b.path,False,match_samples=32)
    return registry.maps[a.path,b.path]


def rows(a,b,left,right,count,mapping=None):
    fa=mapping.fractions(a,[key for key,pos in left]) if mapping else [a.fraction(key) for key,pos in left]
    fb=mapping.fractions(b,[key for key,pos in right]) if mapping else [b.fraction(key) for key,pos in right]
    fractions=[(station(fa,i/count)+station(fb,i/count))*.5 for i in range(count+1)]
    if mapping:
        pairs=mapping.sample(fractions)
        aa=[one for one,two in pairs];bb=[two for one,two in pairs]
    else:
        aa=a.sample(station(fa,i/count) for i in range(count+1))
        bb=b.sample(station(fb,i/count) for i in range(count+1))
    return aa,bb,fractions,fa,fb
