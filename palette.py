"""Six display palettes, shared marker geometry; no scene or GPU resources."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Palette:
    group: tuple
    guide: tuple
    selected: tuple
    wire: tuple
    points: tuple
    caps: tuple
    cap_points: tuple
    outline: tuple
    radii: tuple
    outline_width: float
    guide_width: float
    selected_width: float
    cap_width: float
    tint: tuple
    tint_strength: float


def _rgb(value):
    """Design swatches are sRGB; Blender overlays accept scene-linear colors."""
    channels=tuple(int(value[i:i+2],16)/255. for i in (1,3,5))
    return tuple(c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4 for c in channels)

def _rgba(value):return (*_rgb(value),1.)

def _pixel_rgba(value):
    # POST_PIXEL's builtin shader writes display RGB directly, unlike the
    # scene-linear custom POST_VIEW shader. Keep both representations explicit.
    return (*(int(value[i:i+2],16)/255. for i in (1,3,5)),1.)

RADII=(4.,5.3,6.1,4.8,4.8)
CAPS=(_rgba('#94D8AA'),_rgba('#CA9AE0'))
CAP_POINTS=(_pixel_rgba('#94D8AA'),_pixel_rgba('#CA9AE0'))

def _make(guide,selection,points,active,outline,wire,group,strength=.30):
    return Palette(group=_rgb(group),guide=_rgba(guide),selected=_rgba(selection),wire=_rgb(wire),
        points=(_pixel_rgba(points),_pixel_rgba(selection),_pixel_rgba(active)),caps=CAPS,cap_points=CAP_POINTS,
        outline=_pixel_rgba(outline),radii=RADII,outline_width=1.,
        guide_width=3.,selected_width=4.,cap_width=3.5,
        tint=_rgb(selection),tint_strength=strength)

# Keep the stored identifiers from 0.35.26 (WARM=0, CLASSIC=1).
CLASSIC=_make('#527FA3','#DDFDF8','#D7E3EC','#FFFFFF','#303B44','#343A40','#B3C9D8',.46)
WARM=_make('#A26151','#D1F6E5','#F0D7BC','#FFFFFF','#403631','#3D3531','#D6C1AA',.46)
RED=_make('#E28C83','#FFE3A1','#F6CEBB','#FFFFFF','#4D2B31','#472D30','#EBD2CD')
PURPLE=_make('#8465B3','#F0E6FF','#D8C9EB','#FFFFFF','#3D3449','#3C3543','#BDAFD2',.46)
GREEN=_make('#B4D6B3','#DCFFE2','#E4EED7','#FFFFFF','#303D35','#343E36','#DEE9D8')

BLENDER=_make('#000000','#FF9900','#000000','#FFFFFF','#202020','#202020','#A6A6A6')

PALETTES={'BLENDER':BLENDER,'CLASSIC':CLASSIC,'WARM':WARM,'RED':RED,'PURPLE':PURPLE,'GREEN':GREEN}
# Explicit enum numbers preserve old saved values even though the menu is reordered.
ITEMS=[('CLASSIC','Светлый','Голубовато-серая поверхность, синие направляющие и светлое бирюзовое выделение',1),
       ('WARM','Тёплый','Кремово-песочная поверхность, терракотовые направляющие и светлое мятное выделение',0),
       ('RED','Красный','Кораллово-красные направляющие, золотистое выделение и винный контур',2),
       ('PURPLE','Фиолетовый','Лавандовая поверхность, фиолетовые направляющие и светлое лиловое выделение',3),
       ('GREEN','Зелёный','Мягкие зелёные направляющие и выразительное зелёное выделение',4),
       ('BLENDER','Блендер','Серый объект и цвета редактирования текущей темы Blender',5)]

_NATIVE=None

def _blender():
    global _NATIVE
    try:
        import bpy
        theme=bpy.context.preferences.themes[0].view_3d
        key=tuple(tuple(getattr(theme,k))[:3] for k in ('wire_edit','vertex','vertex_select','edge_select','face_select'))
    except (ImportError,AttributeError,IndexError):return BLENDER
    if _NATIVE and _NATIVE[0]==key:return _NATIVE[1]
    from dataclasses import replace
    def hexcolor(rgb):return '#'+''.join('%02X'%max(0,min(255,round(c*255))) for c in rgb)
    style=_make(hexcolor(key[0]),hexcolor(key[3]),hexcolor(key[1]),'#FFFFFF','#202020','#202020','#A6A6A6')
    style=replace(style,points=(style.points[0],_pixel_rgba(hexcolor(key[2])),style.points[2]),tint=_rgb(hexcolor(key[4])))
    _NATIVE=(key,style);return style

def get(props):return _blender() if props.palette=='BLENDER' else PALETTES.get(props.palette,WARM)

# Legacy saved color is retained for file compatibility; themes own rendered color.
def tint_get(props):return props.get('tint',get(props).tint)
def tint_set(props,value):props['tint']=tuple(value)
def strength_get(props):return float(props.get('opacity',get(props).tint_strength))
def strength_set(props,value):props['opacity']=float(value)
def highlight(props):return get(props).tint,float(props.opacity)


def markers(style):
    """Selected endpoint markers retain their semantic fill and gain an accent ring."""
    colors=(*style.points,*style.cap_points,*style.cap_points)
    radii=(*style.radii,style.radii[3]+.4,style.radii[4]+.4)
    outlines=(style.outline,style.outline,style.outline,style.outline,style.outline,style.points[1],style.points[1])
    widths=(style.outline_width,style.outline_width,1.6,style.outline_width,style.outline_width,1.8,1.8)
    return zip(colors,radii,outlines,widths)
