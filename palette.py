"""Six display palettes, shared marker geometry; no scene or GPU resources."""
from dataclasses import dataclass,replace
from pathlib import Path
import json,colorsys,re

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

RADII=(4.5,5.8,6.6,5.3,5.3)
CAPS=(_rgba('#94D8AA'),_rgba('#CA9AE0'))
CAP_POINTS=(_pixel_rgba('#94D8AA'),_pixel_rgba('#CA9AE0'))

def _make(guide,selection,points,active,outline,wire,group,strength=.30):
    return Palette(group=_rgb(group),guide=_rgba(guide),selected=_rgba(selection),wire=_rgb(wire),
        points=(_pixel_rgba(points),_pixel_rgba(selection),_pixel_rgba(active)),caps=CAPS,cap_points=CAP_POINTS,
        outline=_pixel_rgba(outline),radii=RADII,outline_width=1.,
        guide_width=4.,selected_width=5.,cap_width=4.5,
        tint=_rgb(selection),tint_strength=strength)

def _load_library(path):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data,dict):raise ValueError('themes.json must contain an object')
    if data.get('version')!=1:raise ValueError('unsupported themes.json version')
    styles={};items=[];numbers=set()
    for entry in data['themes']:
        ident=entry['id'];number=entry['value']
        if not isinstance(ident,str) or not re.fullmatch('[A-Z][A-Z0-9_]*',ident) or ident in styles:raise ValueError('duplicate or invalid theme id')
        if type(number) is not int or not 0<=number<=2147483647 or number in numbers:raise ValueError('duplicate or invalid enum value')
        if not isinstance(entry['name'],str) or not entry['name']:raise ValueError('missing theme name')
        if not isinstance(entry.get('description',''),str):raise ValueError('invalid theme description')
        for key in ('surface','segment','guide','selection','point','outline'):
            if not isinstance(entry[key],str) or not re.fullmatch('#[0-9a-fA-F]{6}',entry[key]):raise ValueError('invalid '+key+' in '+ident)
        strength=entry.get('strength',.46)
        if not isinstance(strength,(float,int)) or not 0<=strength<=.65:raise ValueError('invalid selection strength')
        style=_make(entry['guide'],entry['selection'],entry['point'],'#FFFFFF',entry['outline'],'#000000',entry['surface'],strength)
        styles[ident]=replace(style,tint=_rgb(entry['segment']))
        items.append((ident,entry['name'],entry.get('description',''),number));numbers.add(number)
    # Existing enum numbers are part of the saved-file format.
    for ident,number in (('WARM',0),('CLASSIC',1),('RED',2),('PURPLE',3),('GREEN',4),('BLENDER',5)):
        if not any(e[0]==ident and e[3]==number for e in items):raise ValueError('missing or renumbered built-in theme '+ident)
    return styles,items


try:PALETTES,ITEMS=_load_library(Path(__file__).with_name('themes.json'))
except (OSError,ValueError,KeyError,TypeError) as exc:
    # An edited library must not prevent old guide objects from opening.
    print('[NyanwHair] themes.json: '+str(exc)+'; using fallback palette')
    fallback=_make('#A26151','#F9DDC0','#F0D7BC','#FFFFFF','#403631','#000000','#D6C1AA',.46)
    PALETTES={name:fallback for name in ('WARM','CLASSIC','RED','PURPLE','GREEN','BLENDER')}
    ITEMS=[(name,name,'Fallback: check themes.json',i) for i,name in enumerate(PALETTES)]
CLASSIC,WARM,RED,PURPLE,GREEN,BLENDER=(PALETTES[name] for name in ('CLASSIC','WARM','RED','PURPLE','GREEN','BLENDER'))


def wire_color(group):
    """Darker, more saturated group color; independent of geometry buffers."""
    rgb=tuple(12.92*c if c<=.0031308 else 1.055*max(0.,c)**(1/2.4)-.055 for c in group)
    h,s,v=colorsys.rgb_to_hsv(*rgb)
    rgb=colorsys.hsv_to_rgb(h,min(1.,s*1.4),v*.55)
    return tuple(c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4 for c in rgb)

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


def preview_color(props,color):
    """Fade cached overlay colours at draw time, without rebuilding geometry."""
    return (*color[:3],color[3]*(1.-props.transparency))


def markers(style):
    """Selected endpoint markers retain their semantic fill and gain an accent ring."""
    colors=(*style.points,*style.cap_points,*style.cap_points)
    radii=(*style.radii,style.radii[3]+.4,style.radii[4]+.4)
    outlines=(style.outline,style.outline,style.outline,style.outline,style.outline,style.points[1],style.points[1])
    widths=(style.outline_width,style.outline_width,1.6,style.outline_width,style.outline_width,1.8,1.8)
    return zip(colors,radii,outlines,widths)
