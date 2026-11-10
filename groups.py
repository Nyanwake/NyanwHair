"""Face-owned hair groups; explicit isolation, cached background surfaces."""
from .i18n import tr
import bpy,bmesh,json
from bpy.types import PropertyGroup,Operator,UIList
from bpy.props import StringProperty,IntProperty,FloatProperty,FloatVectorProperty,BoolProperty

CACHE={}
DEFAULT_COLOR=(.8,.8,.8)
_RESTORING=False
_JOURNAL='nh35_groups_'

def active_id(obj):return max(1,int(obj.nh35.active_group))
def settings(obj,uid=None):return next((g for g in obj.nh35.groups if g.uid==(uid or active_id(obj))),None)
def ids(obj):return [g.uid for g in obj.nh35.groups] or [1]
def get_index(self):return next((i for i,g in enumerate(self.groups) if g.uid==self.active_group),0)
def set_index(self,value):
    if not _RESTORING and 0<=value<len(self.groups) and self.groups[value].uid!=self.active_group:
        bpy.ops.nyanw35.group('EXEC_DEFAULT',True,action='SELECT',uid=self.groups[value].uid)
def _custom_color(group):
    stored=group.get('color')
    if stored is None:return None
    # Older Undo journals sometimes materialized the original gray default.
    # A new explicit edit is tagged, including an intentional choice of gray.
    if not group.get('_nh35_color_custom',False) and all(abs(x-y)<1e-6 for x,y in zip(stored,DEFAULT_COLOR)):return None
    return stored

def color_get(group):
    stored=_custom_color(group)
    if stored is not None:return stored
    from . import palette
    obj=group.id_data
    return palette.get(obj.nh35).group if hasattr(obj,'nh35') else DEFAULT_COLOR

def color_set(group,value):
    group['color']=tuple(value);group['_nh35_color_custom']=True

def color(obj,uid):
    group=settings(obj,uid)
    if group is not None:return tuple(group.color)
    from . import palette
    return palette.get(obj.nh35).group

def changed(self,context):
    if _RESTORING:return
    obj=self.id_data
    if getattr(obj,'type',None)=='MESH':
        remember(obj)
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type=='VIEW_3D':area.tag_redraw()

class NH35HairGroup(PropertyGroup):
    uid:IntProperty(default=1)
    name:StringProperty(name='Name',default='Group',update=changed)
    color:FloatVectorProperty(name='Group colour',subtype='COLOR',size=3,default=DEFAULT_COLOR,min=0.,max=1.,get=color_get,set=color_set,update=changed)
    mirror_x:BoolProperty(name='Mirror X',default=False,update=changed,description="Linked mirror on the object's local X axis; edit the source half")
    mirror_y:BoolProperty(name='Mirror Y',default=False,update=changed,description="Linked mirror on the object's local Y axis; edit the source half")
    mirror_z:BoolProperty(name='Mirror Z',default=False,update=changed,description="Linked mirror on the object's local Z axis; edit the source half")
    reference_length:FloatProperty(default=0.,min=0.)

def ensure(obj):
    global _RESTORING
    if not obj.nh35.groups:
        _RESTORING=True
        try:group=obj.nh35.groups.add();group.uid=1;group.name=tr('Group 1');group.reference_length=obj.nh35.detail_length
        finally:_RESTORING=False
    for group in obj.nh35.groups:
        if group.uid==1 and group.name=='Основная':
            _RESTORING=True
            try:group.name=tr('Group 1')
            finally:_RESTORING=False
    if active_id(obj) not in ids(obj):obj.nh35.active_group=obj.nh35.groups[0].uid


def _state(obj):
    return [active_id(obj),[[g.uid,g.name,(list(_custom_color(g)) if _custom_color(g) is not None else None),[g.mirror_x,g.mirror_y,g.mirror_z],g.reference_length,_custom_color(g) is not None] for g in obj.nh35.groups]]


def remember(obj):
    """Mirror user metadata into edit-undo CustomData, never into element IDs.

    Blender's Edit Mesh undo does not restore Object PropertyGroups. Replicate
    the small snapshot on existing faces so ordinary cuts/deletes retain it.
    Allocate every string layer before obtaining any element references.
    """
    from . import addon as a
    if not a.is_guide(obj) or not obj.nh35.groups:return
    payload=json.dumps(_state(obj),ensure_ascii=False,separators=(',',':')).encode('utf8')
    chunks=[payload[i:i+240] for i in range(0,len(payload),240)]
    bm,owned=a._mesh_bm(obj)
    try:
        domain=bm.faces if len(bm.faces) else bm.verts
        if not len(domain):return
        layers=domain.layers.string
        for i in range(len(chunks)):
            name=_JOURNAL+str(i).zfill(4)
            if layers.get(name) is None:layers.new(name)
        fields=sorted((name,layers[name]) for name in layers.keys() if name.startswith(_JOURNAL))
        for element in domain:
            for i,(name,layer) in enumerate(fields):element[layer]=chunks[i] if i<len(chunks) else b''
        if owned:bm.to_mesh(obj.data);obj.data.update()
        else:bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False)
    finally:
        if owned:bm.free()


def restore(obj):
    global _RESTORING
    from . import addon as a
    bm,owned=a._mesh_bm(obj)
    try:
        domain=bm.faces if len(bm.faces) else bm.verts
        names=sorted(name for name in domain.layers.string.keys() if name.startswith(_JOURNAL))
        if not names:return
        state=None
        for element in domain:
            raw=b''.join(element[domain.layers.string[name]] for name in names)
            if raw:
                try:state=json.loads(raw.decode('utf8'));break
                except (ValueError,UnicodeError):continue
        if state is None or state==_state(obj):return
    finally:
        if owned:bm.free()
    _RESTORING=True
    try:
        obj.nh35.groups.clear()
        for record in state[1]:
            uid,name,color,axes,reference=record[:5]
            custom=record[5] if len(record)>5 else color is not None and any(abs(x-y)>1e-6 for x,y in zip(color,DEFAULT_COLOR))
            group=obj.nh35.groups.add();group.uid=uid;group.name=name
            if color is not None and custom:group.color=color
            group.mirror_x,group.mirror_y,group.mirror_z=axes;group.reference_length=reference
        obj.nh35.active_group=state[0]
    finally:_RESTORING=False

def face_group(face,bm,fallback=1):
    from . import addon as a
    layer=bm.faces.layers.int.get(a.L_GROUP)
    return int(face[layer]) if layer and face[layer]>0 else fallback

def vertex_group(vertex,bm,fallback=1):
    from . import addon as a
    layer=bm.verts.layers.int.get(a.L_VERTEX_GROUP)
    if layer and vertex[layer]>0:return int(vertex[layer])
    return face_group(vertex.link_faces[0],bm,fallback) if vertex.link_faces else fallback

def isolate(bm,uid):
    from . import addon as a
    group=bm.verts.layers.int[a.L_VERTEX_GROUP]
    for face in bm.faces:
        hidden=face_group(face,bm,uid)!=uid
        if hidden:face.select_set(False)
        face.hide=hidden
    for edge in bm.edges:
        edge.hide=all(f.hide for f in edge.link_faces) if edge.link_faces else all(int(v[group]) not in (0,uid) for v in edge.verts)
        if edge.hide:edge.select_set(False)
    for vertex in bm.verts:
        if vertex.link_faces:vertex[group]=face_group(vertex.link_faces[0],bm,uid)
        elif vertex[group]<=0:vertex[group]=uid
        vertex.hide=int(vertex[group])!=uid
        if vertex.hide:vertex.select_set(False)
    for element in list(bm.select_history):
        if not element.is_valid or element.hide:bm.select_history.remove(element)

def separate(bm):
    """Duplicate shared control vertices by semantic group, keeping attributes."""
    from . import addon as a
    fl=bm.faces.layers.int[a.L_GROUP];vl=bm.verts.layers.int[a.L_VERTEX_GROUP]
    loose_edges={e for e in bm.edges if not e.link_faces};loose_vertices={v for v in bm.verts if not v.link_edges}
    aliases={};original={};replace_faces=[]
    for vertex in list(bm.verts):
        owners=sorted({int(f[fl]) for f in vertex.link_faces})
        if not owners:continue
        vertex[vl]=owners[0]
        for gid in owners:
            if gid==owners[0]:copy=vertex
            else:
                copy=bm.verts.new(vertex.co)
                for layers in (bm.verts.layers.float,bm.verts.layers.int):
                    for layer in layers.values():copy[layer]=vertex[layer]
                copy[vl]=gid;copy.select=vertex.select;copy.hide=vertex.hide
            aliases[vertex,gid]=copy;original[copy]=vertex
    def fields(domain):return [layer for layers in (domain.layers.float,domain.layers.int,domain.layers.string) for layer in layers.values()]
    face_layers=fields(bm.faces);loop_layers=fields(bm.loops);edge_layers=fields(bm.edges)
    edge_values={frozenset(edge.verts):[(layer,edge[layer]) for layer in edge_layers] for edge in bm.edges}
    for face in list(bm.faces):
        gid=int(face[fl]);verts=[aliases.get((v,gid),v) for v in face.verts]
        if all(one is two for one,two in zip(verts,face.verts)):continue
        data=[(layer,face[layer]) for layer in face_layers];corners={aliases.get((l.vert,gid),l.vert):[(layer,l[layer]) for layer in loop_layers] for l in face.loops}
        replace_faces.append((face,verts,data,corners,face.material_index,face.smooth,face.select,face.hide))
    for face,verts,data,corners,material,smooth,selected,hidden in replace_faces:
        bm.faces.remove(face);new=bm.faces.new(verts)
        for layer,value in data:new[layer]=value
        new.material_index=material;new.smooth=smooth;new.select=selected;new.hide=hidden
        for loop in new.loops:
            for layer,value in corners[loop.vert]:loop[layer]=value
        for edge in new.edges:
            key=frozenset(original.get(v,v) for v in edge.verts)
            for layer,value in edge_values.get(key,()):edge[layer]=value
    for edge in list(bm.edges):
        if not edge.link_faces and edge not in loose_edges:bm.edges.remove(edge)
    for vertex in list(bm.verts):
        if not vertex.link_edges and vertex not in loose_vertices:bm.verts.remove(vertex)

def all_caches(obj):
    from . import addon as a
    current=a._CACHE.get(obj.as_pointer());stored=CACHE.get(obj.as_pointer(),{})
    return [stored[uid] for uid in ids(obj) if uid in stored] or ([current] if current else [])

class NYANW35_OT_group(Operator):
    bl_idname='nyanw35.group';bl_label='Hair groups';bl_options={'REGISTER','UNDO'}
    action:StringProperty(default='SELECT')
    uid:IntProperty(default=1)
    @classmethod
    def description(cls,context,properties):
        return {'SELECT':tr('Switch the editable group; other groups display only their surfaces'),
                'ADD':tr('Add an empty independent group to this object'),
                'MOVE':tr('Move selected segments into this group, separating shared points'),
                'REMOVE':tr('Remove the active group, keeping its segments in a neighbouring group')}.get(properties.action,tr('Hair groups'))
    def execute(self,context):
        from . import addon as a
        obj=context.object
        if not a.is_guide(obj):return {'CANCELLED'}
        ensure(obj);previous=active_id(obj);a._prepare_object(obj)
        if self.action=='ADD':
            group=obj.nh35.groups.add();group.uid=max(g.uid for g in obj.nh35.groups)+1;group.name=tr('Group %d')%group.uid;self.uid=group.uid
        elif self.action=='REMOVE':
            if len(obj.nh35.groups)<2:return {'CANCELLED'}
            self.uid=next(g.uid for g in obj.nh35.groups if g.uid!=previous)
        if self.uid not in ids(obj):return {'CANCELLED'}
        if self.action in ('MOVE','REMOVE'):
            if obj.mode!='EDIT':self.report({'WARNING'},tr('Moving and deleting groups requires Edit Mode'));return {'CANCELLED'}
            bm=bmesh.from_edit_mesh(obj.data);chosen={f.index for f in a._zone_ui_faces(bm,obj)}
            def operation(stage):
                a._prepare_bmesh(stage);stage.faces.index_update();layer=stage.faces.layers.int[a.L_GROUP]
                for face in stage.faces:
                    if (int(face[layer])==previous if self.action=='REMOVE' else face.index in chosen):face[layer]=self.uid
                separate(stage);isolate(stage,self.uid if self.action=='REMOVE' else previous)
            a._edit_transaction(obj,operation)
            if self.action=='REMOVE':obj.nh35.groups.remove(next(i for i,g in enumerate(obj.nh35.groups) if g.uid==previous));CACHE.get(obj.as_pointer(),{}).pop(previous,None)
            else:a._rebuild(obj,True);a._rebuild(obj,True,group_id=self.uid,_background=True);return {'FINISHED'}
        obj.nh35.active_group=self.uid
        bm,owned=a._mesh_bm(obj)
        try:
            isolate(bm,self.uid)
            if owned:bm.to_mesh(obj.data);obj.data.update()
            else:bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False)
        finally:
            if owned:bm.free()
        remember(obj)
        a._clear_zone_pick();a._CACHE.pop(obj.as_pointer(),None);a._rebuild(obj,True);a._sync_ui(obj)
        return {'FINISHED'}

class NYANW35_OT_group_starter(Operator):
    bl_idname='nyanw35.group_starter';bl_label='Create strand in group';bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        from . import addon as a,editing
        obj=context.edit_object
        if not a.is_guide(obj):return {'CANCELLED'}
        uid=active_id(obj)
        def operation(bm):
            a._prepare_bmesh(bm)
            verts=[bm.verts.new(p) for p in ((-.4,0,0),(.4,0,0),(.45,0,1),(.2,0,2),(-.2,0,2),(-.45,0,1))]
            face=bm.faces.new(verts);face[bm.faces.layers.int[a.L_GROUP]]=uid
            face[bm.faces.layers.int[a.L_ORDER]]=max((f[bm.faces.layers.int[a.L_ORDER]] for f in bm.faces),default=0)+1
            for v in verts:v[bm.verts.layers.int[a.L_VERTEX_GROUP]]=uid
            a._write_boundary_pair(face,bm,(('edge',0),('edge',3)));editing._select(bm,verts);isolate(bm,uid)
        a._edit_transaction(obj,operation);a._prepare_object(obj);a._rebuild(obj,True)
        return {'FINISHED'}

class NYANW35_UL_groups(UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index,flt_flag=0):
        row=layout.row(align=True)
        row.prop(item,'name',text='',emboss=False,icon='GROUP')
        color=row.row(align=True);color.ui_units_x=1.2;color.prop(item,'color',text='')
        if item.uid!=data.active_group:
            op=row.operator('nyanw35.group',text='',icon='FORWARD');op.action='MOVE';op.uid=item.uid

CLASSES=(NH35HairGroup,NYANW35_UL_groups,NYANW35_OT_group,NYANW35_OT_group_starter)
