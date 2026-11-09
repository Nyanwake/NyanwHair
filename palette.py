"""Small immutable viewport palettes; no scene, mesh or GPU resources."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Palette:
    guide: tuple
    selected: tuple
    wire: tuple
    points: tuple
    caps: tuple
    outline: tuple
    radii: tuple
    outline_width: float
    guide_width: float
    selected_width: float
    cap_width: float
    tint: tuple
    tint_strength: float

WARM=Palette(
    guide=(.69,.59,.54,1.),             # dusty rose / warm stone
    selected=(.91,.67,.51,.92),        # peach, kept distinct from beige
    wire=(.16,.13,.12),
    points=((.75,.69,.63,1.),(.92,.66,.49,1.),(.98,.83,.64,1.)),
    caps=((.59,.73,.62,1.),(.73,.61,.77,1.)),
    outline=(.15,.12,.11,1.),radii=(3.,4.,4.8,4.1,4.1),outline_width=.8,
    guide_width=1.7,selected_width=2.6,cap_width=2.7,
    tint=(.75,.43,.30),tint_strength=.11)

CLASSIC=Palette(
    guide=(.56,.714,.91,1.),selected=(1.,.74,.46,.72),wire=(.10,.13,.17),
    points=((.89,.95,1.,1.),(1.,.79,.44,1.),(1.,1.,1.,1.)),
    caps=((.52,.80,.62,1.),(.72,.59,.85,1.)),
    outline=(.08,.12,.18,1.),radii=(3.2,4.5,5.2,4.4,4.4),outline_width=1.1,
    guide_width=2.4,selected_width=3.4,cap_width=3.2,
    tint=(.424,.812,.769),tint_strength=.16)

def get(props):return CLASSIC if props.palette=='CLASSIC' else WARM

def tint_get(props):return props.get('tint',get(props).tint)
def tint_set(props,value):props['tint']=tuple(value)
def strength_get(props):return float(props.get('opacity',get(props).tint_strength))
def strength_set(props,value):props['opacity']=float(value)
def highlight(props):return tuple(props.tint),float(props.opacity)
