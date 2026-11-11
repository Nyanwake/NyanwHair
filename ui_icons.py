"""Small cached theme accents for native UI; no global theme changes."""
import math
import bpy
from . import palette

_PREVIEWS=None
_ICONS={}


def _pixels(mode,color):
    corners=((8.,7.),(25.,7.),(22.,25.),(6.,24.))
    segments=list(zip(corners,corners[1:]+corners[:1]))
    def distance(point,one,two):
        x,y=point;u,v=one;dx,dy=two[0]-u,two[1]-v
        t=max(0.,min(1.,((x-u)*dx+(y-v)*dy)/(dx*dx+dy*dy)))
        return math.hypot(x-u-t*dx,y-v-t*dy)
    def inside(x,y):
        return all((two[0]-one[0])*(y-one[1])-(two[1]-one[1])*(x-one[0])>=0. for one,two in segments)
    pixels=[]
    for y in range(32):
        for x in range(32):
            coverage=0.
            for oy in (.25,.75):
                for ox in (.25,.75):
                    p=x+ox,y+oy
                    if mode=='VERT':amount=1. if any(math.dist(p,corner)<3. for corner in corners) else .35 if any(distance(p,*edge)<.7 for edge in segments) else 0.
                    elif mode=='EDGE':amount=1. if any(distance(p,*edge)<1.8 for edge in segments) else 0.
                    else:amount=1. if any(distance(p,*edge)<1.1 for edge in segments) else .60 if inside(*p) else 0.
                    coverage+=amount*.25
            pixels.extend((*(c*coverage for c in color),coverage))
    return pixels


def mode_icon(props,mode):
    global _PREVIEWS
    if bpy.app.background:return 0
    style=palette.get(props)
    color=tuple(12.92*c if c<=.0031308 else 1.055*c**(1/2.4)-.055 for c in style.group)
    if props.palette=='BLENDER':color=tuple(style.points[1][:3])
    key=(props.palette,mode);saved=_ICONS.get(key)
    if saved is None or saved[0]!=color:
        from bpy.utils import previews
        if _PREVIEWS is None:_PREVIEWS=previews.new()
        preview=saved[1] if saved else _PREVIEWS.new(str(len(_ICONS)))
        pixels=_pixels(mode,color)
        preview.image_size=(32,32);preview.image_pixels_float=pixels
        preview.icon_size=(32,32);preview.icon_pixels_float=pixels
        _ICONS[key]=(color,preview)
    return _ICONS[key][1].icon_id


def clear():
    global _PREVIEWS
    if _PREVIEWS is not None:
        from bpy.utils import previews
        previews.remove(_PREVIEWS);_PREVIEWS=None
    _ICONS.clear()
