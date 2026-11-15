"""Local cross-section matching; physical border samples stay shared."""
from .core import EPS, add, sub, mul, dot, length, norm


def station(values,t):
    x=t*(len(values)-1);i=min(len(values)-2,int(x));blend=x-i
    return values[i]*(1.-blend)+values[i+1]*blend


def correspondence(registry,a,b):
    pairs=[(a.at(i/16)[1],b.at(i/16)[1]) for i in range(17)]
    centers=[mul(add(p,q),.5) for p,q in pairs]
    skew=[]
    for i,(p,q) in enumerate(pairs[1:-1],1):
        width=sub(q,p);tangent=sub(centers[i+1],centers[i-1])
        if length(width)>EPS and length(tangent)>EPS:skew.append(abs(dot(norm(width),norm(tangent))))
    if not skew or max(skew)<.5:return None
    registry.pair(a.path,b.path,False,match_samples=32)
    return registry.maps[a.path,b.path]


def rows(a,b,left,right,count,mapping=None):
    fa=[mapping.fraction(a,key) if mapping else a.fraction(key) for key,pos in left]
    fb=[mapping.fraction(b,key) if mapping else b.fraction(key) for key,pos in right]
    fractions=[(station(fa,i/count)+station(fb,i/count))*.5 for i in range(count+1)]
    if mapping:
        pairs=[mapping.at(u) for u in fractions]
        aa=[one for one,two in pairs];bb=[two for one,two in pairs]
    else:
        aa=[a.at(station(fa,i/count)) for i in range(count+1)]
        bb=[b.at(station(fb,i/count)) for i in range(count+1)]
    return aa,bb,fractions,fa,fb
