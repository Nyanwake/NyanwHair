"""Small cached theme accents for native UI; no global theme changes."""
import math
import bpy
from . import palette

_PREVIEWS=None
_ICONS={}


def _shapes(kind):
    quad=((4.,4.),(27.,5.),(28.,27.),(4.,28.),(4.,4.))
    if kind in ('VERT','EDGE','FACE','FILL'):return [quad],list(quad[:-1]) if kind=='VERT' else [],[quad] if kind in ('FACE','FILL') else []
    if kind in ('MESH','LOOP'):
        return [quad,((4,15),(28,16)),((15,4),(16,28))],[],[]
    if kind in ('STRAND','REVERSE'):
        one=[(16-10*math.sin(math.pi*t),3+26*t) for t in (i/16 for i in range(17))]
        two=[(16+10*math.sin(math.pi*t),3+26*t) for t in (i/16 for i in range(17))]
        paths=[one+two[::-1],((16,3),(13,12),(18,22),(16,29))]
        if kind=='REVERSE':paths.append([(x+3,y-1) for x,y in two])
        return paths,[],[]
    if kind=='CAP':
        arch=[(16+13*math.cos(t),7+18*math.sin(t)) for t in (math.pi*i/24 for i in range(25))]
        return [arch,((3,7),(16,4),(29,7)),((16,4),(16,25)),((6,17),(16,14),(26,17))],[],[]
    if kind=='EXTRUDE':return [((4,5),(4,27)),((18,5),(18,27)),((4,5),(18,5)),((4,27),(18,27)),((7,16),(29,16)),((24,11),(29,16),(24,21))],[],[]
    if kind=='MERGE':return [((5,6),(13,14)),((27,6),(19,14)),((16,28),(16,20))],[(4,5),(28,5),(16,29),(16,16)],[]
    if kind=='RIP':return [((3,26),(12,17),(12,4)),((29,26),(20,17),(20,4))],[(12,4),(20,4)],[]
    if kind=='DELETE':return [((6,6),(26,26)),((6,26),(26,6))],[],[]
    if kind=='NORMALS':return [((4,12),(20,8),(28,19),(12,24),(4,12)),((16,16),(16,2)),((11,7),(16,2),(21,7)),((22,17),(22,30)),((17,25),(22,30),(27,25))],[],[]
    return [quad],[],[]


def _pixels(kind,color):
    paths,circles,fills=_shapes(kind);segments=[edge for path in paths for edge in zip(path,path[1:])]
    def distance(point,one,two):
        x,y=point;u,v=one;dx,dy=two[0]-u,two[1]-v
        t=max(0.,min(1.,((x-u)*dx+(y-v)*dy)/(dx*dx+dy*dy)))
        return math.hypot(x-u-t*dx,y-v-t*dy)
    def inside(point,polygon):
        x,y=point;areas=[(b[0]-a[0])*(y-a[1])-(b[1]-a[1])*(x-a[0]) for a,b in zip(polygon,polygon[1:])]
        return all(v>=0. for v in areas) or all(v<=0. for v in areas)
    pixels=[]
    for y in range(32):
        for x in range(32):
            coverage=0.
            for oy in (.25,.75):
                for ox in (.25,.75):
                    p=x+ox,y+oy
                    if any(math.dist(p,center)<3.2 for center in circles):amount=1.
                    elif any(distance(p,*edge)<(.75 if kind=='VERT' else 1.7) for edge in segments):amount=.4 if kind=='VERT' else 1.
                    else:amount=.6 if any(inside(p,polygon) for polygon in fills) else 0.
                    coverage+=amount*.25
            pixels.extend((*(c*coverage for c in color),coverage))
    return pixels


def icon(props,kind):
    global _PREVIEWS
    if bpy.app.background:return 0
    style=palette.get(props) if props else palette.WARM
    color=tuple(12.92*c if c<=.0031308 else 1.055*c**(1/2.4)-.055 for c in style.group)
    if props and props.palette=='BLENDER':color=tuple(style.points[1][:3])
    key=(props.palette if props else 'WARM',kind);saved=_ICONS.get(key)
    if saved is None or saved[0]!=color:
        from bpy.utils import previews
        if _PREVIEWS is None:_PREVIEWS=previews.new()
        preview=saved[1] if saved else _PREVIEWS.new(str(len(_ICONS)))
        pixels=_pixels(kind,color)
        preview.image_size=(32,32);preview.image_pixels_float=pixels
        preview.icon_size=(32,32);preview.icon_pixels_float=pixels
        _ICONS[key]=(color,preview)
    return _ICONS[key][1].icon_id


def mode_icon(props,mode):return icon(props,mode)


def button(layout,props,operator,text,kind,fallback,**options):
    accent=icon(props,kind)
    return layout.operator(operator,text=text,**({'icon_value':accent} if accent else {'icon':fallback}),**options)


def clear():
    global _PREVIEWS
    if _PREVIEWS is not None:
        from bpy.utils import previews
        previews.remove(_PREVIEWS);_PREVIEWS=None
    _ICONS.clear()
