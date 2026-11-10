"""Addon-local language selection; never changes Blender's UI language."""
import json
from pathlib import Path
try:
 import bpy
except ImportError:
 bpy=None
RU=json.loads(Path(__file__).with_name('translations.json').read_text(encoding='utf8'))
EN={value:key for key,value in RU.items()}
_specs=[]
def automatic_language():
 return 'RU' if bpy and bpy.context.preferences.view.use_translate_interface and bpy.context.preferences.view.language.startswith('ru') else 'EN'
def initialize():
 addon=bpy.context.preferences.addons.get(__package__)
 if addon:
  if not addon.preferences.is_property_set('language'):addon.preferences.language=automatic_language()
  else:apply_language()
def language():
 if bpy is None:return 'EN'
 addon=bpy.context.preferences.addons.get(__package__)
 try:
  if addon and addon.preferences.is_property_set('language'):return addon.preferences.language
 except (TypeError,AttributeError):pass
 return automatic_language()
def tr(text):
 if not text:return text
 english=EN.get(text,text)
 return RU.get(english,english) if language()=='RU' else english
class Layout:
 def __init__(self,layout):object.__setattr__(self,'_layout',layout)
 def __setattr__(self,key,value):setattr(self._layout,key,value)
 def __getattr__(self,key):
  value=getattr(self._layout,key)
  if not callable(value):return value
  def call(*args,**kw):
   if 'text' in kw:kw['text']=tr(kw['text']);kw['translate']=False
   elif key=='prop' and len(args)>=2:kw.update(text=tr(args[0].bl_rna.properties[args[1]].name),translate=False)
   out=value(*args,**kw)
   if isinstance(out,bpy.types.UILayout):return Layout(out)
   if key=='panel':return tuple(Layout(x) if x is not None else None for x in out)
   return out
  return call
def ui(layout):return layout if isinstance(layout,Layout) else Layout(layout)
def apply_language(self=None,context=None):
 # Replacing our RNA property definitions updates names/tooltips while keeping
 # their ID-property values, callbacks and identifiers intact.
 for cls,fields,labels in _specs:
  for key,(factory,options) in fields.items():
   kw=dict(options)
   for name in ('name','description'):
    if name in kw:kw[name]=tr(options[name])
   items=kw.get('items')
   if isinstance(items,(list,tuple)):
    names={'CLASSIC':'Blue','WARM':'Warm','RED':'Red','PURPLE':'Purple','GREEN':'Green','BLENDER':'Blender'} if key=='palette' else {}
    kw['items']=[(x[0],tr(names.get(x[0],x[1])),tr(names[x[0]]+' palette' if x[0] in names else x[2]),*x[3:]) for x in items]
   setattr(cls,key,factory(**kw))
  for key,value in labels.items():setattr(cls,key,tr(value))
 if bpy:
  for screen in getattr(bpy.data,'screens',()):
   for area in screen.areas:area.tag_redraw()
def prepare(classes):
 _specs.clear()
 for cls in classes:
  fields={}
  if issubclass(cls,bpy.types.PropertyGroup):
   fields={key:(value.function,dict(value.keywords)) for key,value in cls.__annotations__.items() if hasattr(value,'function')}
  labels={key:cls.__dict__[key] for key in ('bl_label','bl_description') if key in cls.__dict__}
  _specs.append((cls,fields,labels))
def clear():
 _specs.clear()
 if bpy.app.timers.is_registered(initialize):bpy.app.timers.unregister(initialize)
if bpy:
 class NHLanguagePreferences(bpy.types.AddonPreferences):
  bl_idname=__package__
  language:bpy.props.EnumProperty(name='Language',items=[('EN','EN','English',0),('RU','RU','Русский',1)],default='EN',update=apply_language)
  def draw(self,context):ui(self.layout).prop(self,'language',text=tr('Language'),expand=True)
