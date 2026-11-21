"""One quadratic Bezier, with arc-spaced supports and a central bend point."""
import bisect
from mathutils import Vector

MAX_POINTS=64

def sample(start,end,bend,intermediate,peak=.5):
    start=Vector(start);end=Vector(end);bend=Vector(bend)
    control=start.lerp(end,max(.05,min(.95,peak)))+bend*2.
    def at(t):return start*((1.-t)**2)+control*(2.*t*(1.-t))+end*(t*t)
    divisions=max(64,4*(intermediate+1));parameters=[i/divisions for i in range(divisions+1)]
    dense=[at(t) for t in parameters];lengths=[0.]
    for a,b in zip(dense,dense[1:]):lengths.append(lengths[-1]+(b-a).length)
    middle=lengths[divisions//2];intervals=intermediate+1
    center=max(1,min(intermediate,int(middle/max(1e-20,lengths[-1])*intervals+.5)))
    values=[0.]
    for i in range(1,intermediate+1):
        distance=middle*i/center if i<=center else middle+(lengths[-1]-middle)*(i-center)/(intervals-center)
        j=min(divisions,max(1,bisect.bisect_left(lengths,distance)))
        blend=(distance-lengths[j-1])/max(1e-20,lengths[j]-lengths[j-1])
        values.append((j-1+blend)/divisions)
    values.append(1.)
    if intermediate:values[center]=.5
    return [at(t) for t in values],values,control


def positions(start,end,bend,intermediate,peak=.5):return sample(start,end,bend,intermediate,peak)[0]

