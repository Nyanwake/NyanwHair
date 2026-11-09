"""Five display palettes, shared marker geometry; no scene or GPU resources."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Palette:
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

RADII=(4.,4.8,5.6,4.8,4.8)
CAPS=(_rgba('#94D8AA'),_rgba('#CA9AE0'))
CAP_POINTS=(_pixel_rgba('#94D8AA'),_pixel_rgba('#CA9AE0'))

def _make(guide,selection,points,active,outline,wire):
    return Palette(guide=_rgba(guide),selected=_rgba(selection),wire=_rgb(wire),
        points=(_pixel_rgba(points),_pixel_rgba(selection),_pixel_rgba(active)),caps=CAPS,cap_points=CAP_POINTS,
        outline=_pixel_rgba(outline),radii=RADII,outline_width=1.,
        guide_width=3.,selected_width=4.,cap_width=3.5,
        tint=_rgb(selection),tint_strength=.18)

# Keep the stored identifiers from 0.35.26 (WARM=0, CLASSIC=1).
CLASSIC=_make('#BFD9EF','#8ED8D0','#F2F0E7','#FFF0C7','#303B44','#343A40')
WARM=_make('#DDB09A','#BECFC7','#F7DFC9','#FFF0C5','#403631','#3D3531')
RED=_make('#E28C83','#F3C27F','#F6CEBB','#FFE8AF','#4D2B31','#472D30')
PURPLE=_make('#C7B8EB','#F0C49C','#E9E1F7','#FFF0C6','#3D3449','#3C3543')
GREEN=_make('#B4D6B3','#EBD29D','#E4EED7','#FFF2D1','#303D35','#343E36')

PALETTES={'CLASSIC':CLASSIC,'WARM':WARM,'RED':RED,'PURPLE':PURPLE,'GREEN':GREEN}
# Explicit enum numbers preserve old saved values even though the menu is reordered.
ITEMS=[('CLASSIC','Светлая','Светло-голубые направляющие и бирюзовое выделение',1),
       ('WARM','Тепло-зелёная','Песочная терракота, серо-зелёное выделение и кремовые точки',0),
       ('RED','Красная','Кораллово-красные направляющие, золотистое выделение и винный контур',2),
       ('PURPLE','Пастельная фиолетовая','Лавандовые направляющие и абрикосовое выделение',3),
       ('GREEN','Пастельная зелёная','Мягкие зелёные направляющие и кремово-золотистое выделение',4)]

def get(props):return PALETTES.get(props.palette,WARM)

# Legacy saved color is retained for file compatibility; themes own rendered color.
def tint_get(props):return props.get('tint',get(props).tint)
def tint_set(props,value):props['tint']=tuple(value)
def strength_get(props):return float(props.get('opacity',get(props).tint_strength))
def strength_set(props,value):props['opacity']=float(value)
def highlight(props):return get(props).tint,float(props.opacity)
