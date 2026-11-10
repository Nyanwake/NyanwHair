"""NyanwHair Guided Surface Blender adapter. F zones own two ordered rails.

Curve directions live on face corners; density values belong to edges or points.
No control-element IDs are used. Live does not mutate Blender datablocks.
"""
import bpy
import bmesh
import math
import traceback
from dataclasses import replace
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty, EnumProperty, CollectionProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from bpy_extras import view3d_utils

from . import core,groups,palette

try:
    import gpu
    from gpu_extras.batch import batch_for_shader
except ImportError:
    gpu = None
    batch_for_shader = None


L_GROUP = 'nh35_group'
L_VERTEX_GROUP = 'nh35_vertex_group'
L_DETAIL = 'nh35_detail'
L_BACK_SIMPLE = 'nh35_back_simple'
L_BACK = 'nh35_back'
L_BACK_WIDTH = 'nh35_back_width'
L_BACK_ENABLED = 'nh35_back_enabled'
L_WIDTH = 'nh35_width'
L_BULGE = 'nh35_bulge'
L_BIAS = 'nh35_bias'
L_FLIP = 'nh35_flip'
L_BOUNDARY = 'nh35_boundary_corners'
L_CAP = 'nh35_cap_corner'  # BMLOOP, per-face direction, never a vertex/edge tag
L_DENSITY = 'nh35_density'
L_POINT_DENSITY = 'nh35_point_density'
L_POINT_RADIUS = 'nh35_point_radius'
L_LAYOUT_LENGTH = 'nh35_layout_length'
L_SMOOTH_START = 'nh35_smooth_start'
L_SMOOTH_END = 'nh35_smooth_end'
L_ORDER = 'nh35_zone_order'  # stable creation order, persisted in source faces

_COLOR_BLUE = (0.56, 0.714, 0.91, 1.0)  # #8FB6E8
_COLOR_TINT = (0.424, 0.812, 0.769)   # #6CCFC4
_CACHE = {}
_RUNNING = False
_DRAW_HANDLER = None
_POINT_HANDLER = None
_SYNCING_UI = False
_DEFAULT_BULGE = 0.08
_UNDOING = False
_CUT_PREVIEW = None
_KEYMAPS = []
_PENDING_PICK = None
_SOURCE_REVISION = 0


def is_guide(obj):
    return bool(obj and obj.type == 'MESH' and hasattr(obj, 'nh35') and obj.nh35.enabled)


def _mesh_bm(obj):
    if obj.mode == 'EDIT':
        return bmesh.from_edit_mesh(obj.data), False
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    return bm, True


def _active_face(bm, obj=None):
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    uid=groups.active_id(obj) if obj is not None else None
    allowed=lambda f:not f.hide and (uid is None or groups.face_group(f,bm,uid)==uid)
    history=bm.select_history.active
    if isinstance(history, bmesh.types.BMFace) and history.is_valid and history.select and allowed(history):
        return history
    candidates=[]
    if isinstance(history, bmesh.types.BMVert) and history.is_valid and history.select:
        candidates=[f for f in history.link_faces if allowed(f)]
    elif isinstance(history, bmesh.types.BMEdge) and history.is_valid and history.select:
        candidates=[f for f in history.link_faces if allowed(f)]
    if isinstance(history,(bmesh.types.BMVert,bmesh.types.BMEdge)) and history.is_valid and history.select and not candidates:
        return None
    if candidates:
        preferred = (_CACHE.get(obj.as_pointer(),{}).get('active',-1)
                     if obj is not None else -1)
        for f in candidates:
            if f.index == preferred:
                return f
        return max(candidates,key=lambda f:(sum(v.select for v in f.verts),sum(e.select for e in f.edges),-f.index))
    selected=[f for f in bm.faces if f.select and allowed(f)]
    if selected:
        preferred=(_CACHE.get(obj.as_pointer(),{}).get('active',-1)
                   if obj is not None else -1)
        return next((f for f in selected if f.index==preferred),selected[-1])
    if obj:
        preferred=_CACHE.get(obj.as_pointer(),{}).get('active',-1)
        if 0<=preferred<len(bm.faces) and allowed(bm.faces[preferred]): return bm.faces[preferred]
    visible=[f for f in bm.faces if allowed(f)]
    return visible[0] if len(visible)==1 else None


def _get_zone(f,bm):
    ints=bm.faces.layers.int
    floats=bm.faces.layers.float
    w=ints.get(L_WIDTH)
    b=floats.get(L_BULGE)
    bias=floats.get(L_BIAS)
    flip=ints.get(L_FLIP)
    order=ints.get(L_ORDER)
    return core.ZoneSettings(
        width=max(1,min(32,int(f[w]))) if w and f[w]>0 else 2,
        bulge=max(-3.0,min(3.0,float(f[b]))) if b else _DEFAULT_BULGE,
        bias=max(-2.0,min(2.0,float(f[bias]))) if bias else 0.0,
        flip=bool(f[flip]) if flip else False,
        order=int(f[order]) if order else 0,
        group=int(f[ints[L_GROUP]]) if ints.get(L_GROUP) else 0,
        detail=int(f[ints[L_DETAIL]]) if ints.get(L_DETAIL) else 0,
        back=float(f[floats[L_BACK]]) if floats.get(L_BACK) else 0.,
        back_simple=bool(f[ints[L_BACK_SIMPLE]]) if ints.get(L_BACK_SIMPLE) else False,
        back_width=int(f[ints[L_BACK_WIDTH]]) if ints.get(L_BACK_WIDTH) else 0,
        back_enabled=bool(f[ints[L_BACK_ENABLED]]) if ints.get(L_BACK_ENABLED) else None,
    )


def _selection_zone(bm, hovered, preferred=-1):
    """Cursor chooses among the owners of the actual native selection.

    A point/edge belonging to another zone always wins over a surface behind
    it. With no selected component, a surface click makes that zone active.
    """
    bm.faces.ensure_lookup_table(); bm.faces.index_update()
    active=bm.select_history.active
    candidates=[]
    if active is not None and active.is_valid and active.select:
        if isinstance(active,bmesh.types.BMFace):
            return active.index
        if isinstance(active,(bmesh.types.BMVert,bmesh.types.BMEdge)):
            candidates=[f for f in active.link_faces if f.is_valid and not f.hide]
            if not candidates:
                return -1  # loose construction geometry has no segment
    if candidates:
        ids={f.index for f in candidates}
        if hovered in ids: return hovered
        if preferred in ids: return preferred
        return max(candidates,key=lambda f:(sum(v.select for v in f.verts),-f.index)).index
    if hovered is not None and 0<=hovered<len(bm.faces):
        return hovered
    return preferred if 0<=preferred<len(bm.faces) else -1


def _ray_zone(cache,origin,direction):
    from . import decorations
    obj=bpy.context.edit_object;records=groups.all_caches(obj) if is_guide(obj) and cache.get('object_pointer')==obj.as_pointer() else [cache]
    nearest=None
    for record in records:
        result=decorations.preview(record,obj) if is_guide(obj) and record.get('object_pointer')==obj.as_pointer() else record['result']
        if not result.faces:continue
        if record.get('pick_result') is not result:
            record['pick_bvh']=BVHTree.FromPolygons(result.vertices,result.faces);record['pick_result']=result
        from mathutils import Matrix
        for mask,matrix in decorations.instance_matrices(result,Matrix.Identity(4)):
            inverse=matrix.inverted_safe();hit,normal,index,distance=record['pick_bvh'].ray_cast(inverse@Vector(origin),(inverse.to_3x3()@Vector(direction)).normalized())
            if index is not None:
                distance=(matrix@hit-Vector(origin)).length
                if nearest is None or distance<nearest[0]:nearest=(distance,record,result,index,mask)
    if nearest is None:return None
    distance,record,result,index,mask=nearest
    if record is not cache or mask or index in getattr(result,'replica_faces',()):return None
    return result.owners[index]


def _zone_key(plan):
    return ('order',plan.settings.order) if plan.settings.order>0 else ('boundary',core._boundary_key(plan.boundary))


def _zone_lookup(result):
    return {_zone_key(plan):plan.index for plan in result.plans_by_loop.values()}


def _selected_zone_indices(cache):
    if cache.get('zone_selection_explicit'):
        lookup=_zone_lookup(cache['result'])
        return {lookup[key] for key in cache.get('zone_selection',()) if key in lookup}
    active=cache.get('active',-1)
    return {active} if active>=0 else set()


def _native_selection_stamp(bm):
    for seq in (bm.verts,bm.edges,bm.faces):seq.index_update()
    active=bm.select_history.active
    kind='VERT' if isinstance(active,bmesh.types.BMVert) else 'EDGE' if isinstance(active,bmesh.types.BMEdge) else 'FACE'
    return (tuple(tuple(e.index for e in seq if e.select and not e.hide) for seq in (bm.verts,bm.edges,bm.faces)),
            (kind,active.index) if active is not None and active.is_valid else None)


def _update_zone_selection(bm,cache,hovered,extend=False,component=None):
    zone=(hovered if hovered is not None else -1) if component is False else _selection_zone(bm,hovered,cache.get('active',-1))
    if hovered is None and not any(v.select for v in bm.verts):zone=-1
    lookup=_zone_lookup(cache['result']);by_index={v:k for k,v in lookup.items()}
    selected=set(cache.get('zone_selection',())) if cache.get('zone_selection_explicit') else {by_index[i] for i in _selected_zone_indices(cache) if i in by_index}
    key=by_index.get(zone)
    if not extend:selected={key} if key is not None else set()
    elif key is not None:
        if component:selected.add(key)
        elif key in selected:selected.remove(key)
        else:selected.add(key)
    selected.intersection_update(lookup)
    indices={lookup[key] for key in selected}
    active=zone if zone in indices else (cache.get('active',-1) if cache.get('active',-1) in indices else min(indices,default=-1))
    cache['zone_selection']=frozenset(selected);cache['zone_selection_explicit']=True;cache['active']=active
    cache['native_selection_stamp']=_native_selection_stamp(bm)


def _zone_ui_faces(bm,obj):
    cache=_CACHE.get(obj.as_pointer(),{})
    stamp=_native_selection_stamp(bm)
    if _PENDING_PICK is None:
        previous=cache.get('native_selection_stamp')
        if previous is not None and previous!=stamp:cache['zone_selection_explicit']=False
        cache['native_selection_stamp']=stamp
        if bpy.context.tool_settings.mesh_select_mode[2]:
            uid=groups.active_id(obj)
            faces=[f for f in bm.faces if not f.hide and groups.face_group(f,bm,uid)==uid and all(v.select for v in f.verts)]
            lookup=_zone_lookup(cache['result']) if cache.get('result') else {}
            keys={i:key for key,i in lookup.items()}
            cache['zone_selection']=frozenset(keys[f.index] for f in faces if f.index in keys)
            cache['zone_selection_explicit']=True
            indices={f.index for f in faces}
            if cache.get('active',-1) not in indices:cache['active']=min(indices,default=-1)
            return faces
    if cache.get('zone_selection_explicit'):
        bm.faces.ensure_lookup_table();bm.faces.index_update()
        return [bm.faces[i] for i in sorted(_selected_zone_indices(cache)) if 0<=i<len(bm.faces)]
    face=_active_face(bm,obj)
    return [face] if face is not None else []


def _zone_ui_primary(bm,obj):
    faces=_zone_ui_faces(bm,obj)
    active=_CACHE.get(obj.as_pointer(),{}).get('active',-1)
    return next((f for f in faces if f.index==active),faces[-1] if faces else None)


def _near_control_component(ctx,obj,xy,cache):
    mode=ctx.tool_settings.mesh_select_mode
    if mode[2]:return False
    mouse=Vector(xy)
    if mode[0]:
        bm=bmesh.from_edit_mesh(obj.data)
        for vertex in bm.verts:
            if vertex.hide or groups.vertex_group(vertex,bm,groups.active_id(obj))!=groups.active_id(obj):continue
            world=obj.matrix_world@vertex.co
            screen=view3d_utils.location_3d_to_region_2d(ctx.region,ctx.region_data,world)
            if screen is not None and (screen-mouse).length<=9. and _point_visible(ctx,world):return True
    if mode[1]:
        segments_to_check=list(cache.get('rail_segments',{}).values())
        bm=bmesh.from_edit_mesh(obj.data)
        segments_to_check.extend([tuple(obj.matrix_world@edge.verts[0].co),tuple(obj.matrix_world@edge.verts[1].co)] for edge in bm.edges if not edge.hide)
        for segments in segments_to_check:
            for a,b in zip(segments[::2],segments[1::2]):
                start=Vector(a);end=Vector(b)
                p=view3d_utils.location_3d_to_region_2d(ctx.region,ctx.region_data,start)
                q=view3d_utils.location_3d_to_region_2d(ctx.region,ctx.region_data,end)
                if p is None or q is None:continue
                span=q-p;blend=max(0.,min(1.,(mouse-p).dot(span)/span.length_squared)) if span.length_squared>1e-10 else 0.
                if (mouse-(p+span*blend)).length<=7. and _point_visible(ctx,start.lerp(end,blend)):return True
    return False


def _finish_zone_pick():
    global _PENDING_PICK
    pending=_PENDING_PICK
    _PENDING_PICK=None
    if pending is None or not _RUNNING or _UNDOING: return None
    obj,hovered,topology,area,*modifiers=pending
    extend=bool(modifiers[0]) if modifiers else False
    try:
        if bpy.context.edit_object!=obj or not is_guide(obj): return None
        bm=bmesh.from_edit_mesh(obj.data)
        bm.verts.index_update(); bm.faces.index_update()
        if tuple(tuple(v.index for v in f.verts) for f in bm.faces)!=topology:
            return None
        cached=_CACHE.get(obj.as_pointer())
        if cached is None: return None
        component=bool(modifiers[1]) if len(modifiers)>1 else None
        _update_zone_selection(bm,cached,hovered,extend,component)
        if bpy.context.tool_settings.mesh_select_mode[2] and component is False:
            # A generated surface can curve away from its native cage face.
            # Keep face-mode selection in agreement with the picked segment.
            selected=_selected_zone_indices(cached)
            uid=groups.active_id(obj)
            for face in bm.faces:
                if groups.face_group(face,bm,uid)==uid:face.select_set(False)
            for edge in bm.edges:
                if not edge.hide:edge.select_set(False)
            for vertex in bm.verts:
                if not vertex.hide:vertex.select_set(False)
            bm.select_history.clear();bm.faces.ensure_lookup_table()
            for index in sorted(selected):bm.faces[index].select_set(True)
            active=cached.get('active',-1)
            if active in selected:bm.faces.active=bm.faces[active];bm.select_history.add(bm.faces[active])
            bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False)
            cached['native_selection_stamp']=_native_selection_stamp(bm)
        _sync_ui(obj)
        area.tag_redraw()
    except (ReferenceError,RuntimeError):
        pass
    return None


def _clear_zone_pick():
    global _PENDING_PICK
    _PENDING_PICK=None
    if bpy.app.timers.is_registered(_finish_zone_pick):
        bpy.app.timers.unregister(_finish_zone_pick)


class NYANW35_OT_pick_zone(Operator):
    bl_idname='nyanw35.pick_zone'
    bl_label='Активный сегмент под курсором'
    bl_options={'INTERNAL'}

    @classmethod
    def poll(cls,context):
        return bool(is_guide(context.edit_object) and context.area and
                    context.area.type=='VIEW_3D' and context.region and
                    context.region.type=='WINDOW' and context.region_data)

    def invoke(self,context,event):
        # Observe CLICK, the same event used by Blender's Select Box keymap.
        # Reading on PRESS raced the native selection committed on release.
        # The deferred read sees its result; it never edits BMesh selection.
        global _PENDING_PICK
        if event.ctrl or event.alt or event.oskey: return {'PASS_THROUGH'}
        obj=context.edit_object
        if not obj.nh35.show_preview: return {'PASS_THROUGH'}
        _rebuild(obj)
        cached=_CACHE.get(obj.as_pointer())
        if cached is None: return {'PASS_THROUGH'}
        if not cached.get('zone_selection_explicit'):
            lookup=_zone_lookup(cached['result'])
            cached['zone_selection']=frozenset(k for k,i in lookup.items() if i==cached.get('active',-1))
            cached['zone_selection_explicit']=True
        xy=(event.mouse_region_x,event.mouse_region_y)
        origin=view3d_utils.region_2d_to_origin_3d(context.region,context.region_data,xy)
        direction=view3d_utils.region_2d_to_vector_3d(context.region,context.region_data,xy)
        inverse=obj.matrix_world.inverted_safe()
        hovered=_ray_zone(cached,inverse@origin,(inverse.to_3x3()@direction).normalized())
        _PENDING_PICK=(obj,hovered,cached['topology'],context.area,bool(getattr(event,'shift',False)),_near_control_component(context,obj,xy,cached))
        if not bpy.app.timers.is_registered(_finish_zone_pick):
            bpy.app.timers.register(_finish_zone_pick,first_interval=0.)
        return {'PASS_THROUGH'}


def _selected_guide_edges(bm,result,vertex_mode):
    bm.verts.index_update()
    rails={edge for paths in result.face_paths.values() for path in paths
           for edge in core.physical_edges(path)}
    return tuple(sorted(core.edge_key(e.verts[0].index,e.verts[1].index)
                        for e in bm.edges if not e.hide and
                        (e.select or vertex_mode and all(v.select for v in e.verts)) and
                        core.edge_key(e.verts[0].index,e.verts[1].index) in rails))


def _selected_guide_batch(cache,bm,vertex_mode):
    keys=_selected_guide_edges(bm,cache['result'],vertex_mode)
    signature=(cache.get('gpu_signature'),keys)
    if cache.get('selected_signature')!=signature:
        coords=[p for edge in keys for p in cache.get('rail_segments',{}).get(edge,())]
        data={name:[value for edge in keys for value in cache.get('rail_attributes',{}).get(edge,{}).get(name,())] for name in ('pos','normalA','normalB','curveGap')}
        cache['selected_batch']=_stroke_batch(_stroke_shader(guides=True),'LINES',data) if coords else None
        cache['selected_signature']=signature
    return cache.get('selected_batch')


def _read_legacy_caps(face, layer):
    """Only unique, topologically valid corner markers are authoritative."""
    if layer is None:
        return None
    starts=[]; ends=[]
    for i,loop in enumerate(face.loops):
        value=int(loop[layer])
        if value in (1,3): starts.append(('edge' if value==1 else 'point',i))
        if value in (2,4): ends.append(('edge' if value==2 else 'point',i))
    if len(starts)!=1 or len(ends)!=1:
        return None
    caps=(starts[0],ends[0])
    return caps if core.cap_paths(tuple(v.index for v in face.verts),*caps) else None


def _boundary_layer(bm):
    return bm.loops.layers.int.get(L_BOUNDARY) or bm.loops.layers.int.get(L_CAP)


def _read_caps(face,layer):
    if layer is None or layer.name!=L_BOUNDARY:return _read_legacy_caps(face,layer)
    loops=list(face.loops);n=len(loops);caps=[]
    for bit in (1,2):
        marked=[i for i,loop in enumerate(loops) if int(loop[layer])&bit]
        if not marked:caps.append(None)
        elif len(marked)==1:caps.append(('point',marked[0]))
        elif len(marked)==2:
            first=next((i for i in marked if (i+1)%n in marked),None)
            caps.append(('edge',first) if first is not None else ('invalid',0))
        else:caps.append(('invalid',0))
    return tuple(caps) if any(c is not None for c in caps) or any(int(loop[layer])&4 for loop in loops) else None


def _write_boundary_pair(face,bm,caps):
    _write_caps(face,bm.loops.layers.int[L_BOUNDARY],caps)
    _write_caps(face,bm.loops.layers.int[L_CAP],caps)


def _sync_legacy_face(face,bm):
    caps=_read_legacy_caps(face,bm.loops.layers.int.get(L_CAP))
    if caps is not None:_write_caps(face,bm.loops.layers.int[L_BOUNDARY],caps)


def _selected_cap(face):
    vertices=[v for v in face.verts if v.select]
    boundary=tuple(v for v in face.verts)
    if len(vertices)==1:return ('point',boundary.index(vertices[0]))
    if len(vertices)==2:
        for i,v in enumerate(boundary):
            if {v,boundary[(i+1)%len(boundary)]}==set(vertices):return ('edge',i)
    raise ValueError('Выдели одну точку или две соседние точки торца активного сегмента')


def _write_caps(face,layer,caps):
    loops=list(face.loops)
    for loop in loops:loop[layer]=4 if layer.name==L_BOUNDARY else 0
    for side,cap in enumerate(caps):
        if cap is None or cap[0] not in ('point','edge'):continue
        kind,i=cap
        if layer.name==L_BOUNDARY:
            loops[i][layer]|=1<<side
            if kind=='edge':loops[(i+1)%len(loops)][layer]|=1<<side
        else:loops[i][layer]=((1 if kind=='edge' else 3) if side==0 else (2 if kind=='edge' else 4))


class NYANW35_OT_refresh_layout(Operator):
    bl_idname='nyanw35.refresh_layout'
    bl_label='Обновить раскладку рядов'
    bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):return bool(is_guide(context.object))
    def execute(self,context):
        obj=context.object
        bm,owned=_mesh_bm(obj)
        try:
            _prepare_bmesh(bm);layer=bm.edges.layers.float[L_LAYOUT_LENGTH]
            uid=groups.active_id(obj)
            for edge in bm.edges:
                if any(groups.face_group(f,bm,uid)==uid for f in edge.link_faces) or not edge.link_faces and all(groups.vertex_group(v,bm,uid)==uid for v in edge.verts):edge[layer]=0.
            if owned:bm.to_mesh(obj.data);obj.data.update()
            else:bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False)
        finally:
            if owned:bm.free()
        _prepare_object(obj);_rebuild(obj,True)
        return {'FINISHED'}


class NYANW35_OT_set_cap(Operator):
    bl_idname='nyanw35.set_cap'
    bl_label='Назначить торец сегмента'
    bl_options={'REGISTER','UNDO','INTERNAL'}
    side: EnumProperty(items=[('END','Конец','Выбранная точка или ребро — конец сегмента'),('START','Начало','Выбранная точка или ребро — начало сегмента'),('AUTO','Автоматически','Заново определить оба торца по форме')],default='END')
    @classmethod
    def poll(cls,context):return bool(is_guide(context.edit_object))
    def execute(self,context):
        obj=context.edit_object;bm=bmesh.from_edit_mesh(obj.data)
        bm.verts.index_update();bm.faces.index_update()
        face=_zone_ui_primary(bm,obj) or _active_face(bm,obj)
        if face is None:
            self.report({'WARNING'},'Выбери активный сегмент');return {'CANCELLED'}
        zone=face.index
        def apply(stage):
            _prepare_bmesh(stage);stage.faces.ensure_lookup_table();stage.verts.index_update()
            face=stage.faces[zone]
            points=[tuple(v.co) for v in stage.verts];boundary=tuple(v.index for v in face.verts)
            if self.side=='AUTO':caps=core.auto_caps(points,boundary)
            else:
                selected=_selected_cap(face);index=1 if self.side=='END' else 0
                old=_read_caps(face,_boundary_layer(stage))
                opposite=old[1-index] if old else None
                if opposite is None or core.cap_paths(boundary,opposite,selected) is None:
                    try:candidates=core.auto_caps(points,boundary)
                    except ValueError:candidates=()
                    valid=[c for c in candidates if core.cap_paths(boundary,c,selected)]
                    if not valid:
                        valid=[(kind,i) for kind in ('edge','point') for i in range(len(boundary)) if core.cap_paths(boundary,(kind,i),selected)]
                    if not valid:raise ValueError('Этот торец не образует две непрерывные направляющие')
                    opposite=max(valid,key=lambda c:core.length(core.sub(core.cap_location(points,boundary,c),core.cap_location(points,boundary,selected))))
                caps=(opposite,selected) if index else (selected,opposite)
            if not core.cap_paths(boundary,*caps):raise ValueError('Не удалось задать два независимых торца')
            _write_boundary_pair(face,stage,caps)
        try:_edit_transaction(obj,apply)
        except (ValueError,RuntimeError) as exc:self.report({'WARNING'},str(exc));return {'CANCELLED'}
        _rebuild(obj,True)
        return {'FINISHED'}


def _prepare_bmesh(bm, result=None, group_id=None):
    """Explicit actions only. Allocate the complete schema BEFORE element refs.

    CustomData allocation can invalidate BMesh Python wrappers even without
    a topology edit. Never call this while holding vertices/faces/loops.
    """
    context_obj=bpy.context.edit_object or bpy.context.object
    current_group=group_id or (groups.active_id(context_obj) if is_guide(context_obj) else 1)
    migrate_back=bm.faces.layers.int.get(L_BACK_ENABLED) is None
    for name in (L_GROUP,L_DETAIL,L_BACK_SIMPLE,L_BACK_WIDTH,L_BACK_ENABLED):
        if bm.faces.layers.int.get(name) is None:bm.faces.layers.int.new(name)
    if bm.faces.layers.float.get(L_BACK) is None:bm.faces.layers.float.new(L_BACK)
    if bm.verts.layers.int.get(L_VERTEX_GROUP) is None:bm.verts.layers.int.new(L_VERTEX_GROUP)
    for name,integer,default in ((L_WIDTH,True,2),(L_FLIP,True,0),(L_ORDER,True,0),
                                (L_BULGE,False,_DEFAULT_BULGE),(L_BIAS,False,0.)):
        layers=bm.faces.layers.int if integer else bm.faces.layers.float
        if layers.get(name) is None:
            layer=layers.new(name)
            for face in bm.faces: face[layer]=default
    if bm.loops.layers.int.get(L_CAP) is None:
        bm.loops.layers.int.new(L_CAP)
    migrate=bm.loops.layers.int.get(L_BOUNDARY) is None
    if migrate:bm.loops.layers.int.new(L_BOUNDARY)
    for name in (L_DENSITY,L_LAYOUT_LENGTH):
        if bm.edges.layers.float.get(name) is None:bm.edges.layers.float.new(name)
    for name in (L_POINT_DENSITY,L_POINT_RADIUS):
        if bm.verts.layers.float.get(name) is None:bm.verts.layers.float.new(name)
    for name in (L_SMOOTH_START,L_SMOOTH_END):
        if bm.loops.layers.float.get(name) is None:bm.loops.layers.float.new(name)
    bm.verts.index_update(); bm.faces.index_update()
    group_layer=bm.faces.layers.int[L_GROUP]
    for face in bm.faces:
        if migrate_back:face[bm.faces.layers.int[L_BACK_ENABLED]]=int(face[bm.faces.layers.float[L_BACK]]>0.)
        if face[group_layer]<=0:face[group_layer]=current_group
    for vertex in bm.verts:
        if vertex[bm.verts.layers.int[L_VERTEX_GROUP]]<=0:
            vertex[bm.verts.layers.int[L_VERTEX_GROUP]]=groups.face_group(vertex.link_faces[0],bm,current_group) if vertex.link_faces else current_group
    order_layer=bm.faces.layers.int[L_ORDER]
    next_order=max((int(f[order_layer]) for f in bm.faces),default=0)+1
    seen=set()
    for face in bm.faces:
        order=int(face[order_layer])
        if order<=0 or order in seen:
            order=next_order;next_order+=1;face[order_layer]=order
            if not migrate:
                mask=bm.loops.layers.int[L_BOUNDARY]
                for loop in face.loops:loop[mask]=0
        seen.add(order)
    layer=bm.loops.layers.int[L_BOUNDARY]
    if migrate:
        legacy=bm.loops.layers.int[L_CAP]
        for face in bm.faces:
            loops=list(face.loops);caps=[]
            for flags in ((1,3),(2,4)):
                found=[('edge' if int(loop[legacy]) in (1,2) else 'point',i) for i,loop in enumerate(loops) if int(loop[legacy]) in flags]
                caps.append(found[0] if len(found)==1 else None)
            if any(c is not None for c in caps) or any(int(loop[legacy]) for loop in loops):_write_caps(face,layer,caps)
    points=[tuple(v.co) for v in bm.verts]
    for face in bm.faces:
        existing=_read_caps(face,layer)
        if existing is not None:
            if any(c is None for c in existing):
                boundary=tuple(v.index for v in face.verts);hint=result.plans_by_loop.get(boundary) if result else None
                try:existing=core.complete_caps(points,boundary,existing,hint)
                except ValueError:continue
                _write_boundary_pair(face,bm,existing)
            else:_write_caps(face,bm.loops.layers.int[L_CAP],existing)
            continue
        boundary=tuple(v.index for v in face.verts)
        hint=result.plans_by_loop.get(boundary) if result else None
        try:
            plan=core.plan_face(points,boundary,face.index,_get_zone(face,bm),hint=hint)
        except ValueError:
            continue
        _write_boundary_pair(face,bm,(plan.start,plan.end))


def _prepare_object(obj):
    groups.ensure(obj)
    bm,owned=_mesh_bm(obj)
    try:
        cached=_CACHE.get(obj.as_pointer())
        _prepare_bmesh(bm,cached['result'] if cached else None,groups.active_id(obj))
        if owned:
            bm.to_mesh(obj.data)
            obj.data.update()
        else:
            bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False)
    finally:
        if owned: bm.free()
    cached=_CACHE.get(obj.as_pointer())
    _rebuild(obj,True)
    cached=_CACHE.get(obj.as_pointer())
    if cached and cached.get('error_count')==0:
        lengths=cached['result'].layout_lengths
        bm,owned=_mesh_bm(obj)
        try:
            bm.verts.index_update();layer=bm.edges.layers.float[L_LAYOUT_LENGTH]
            for edge in bm.edges:
                if edge[layer]<=0.:edge[layer]=lengths.get(core.edge_key(*(v.index for v in edge.verts)),0.)
            if owned:bm.to_mesh(obj.data);obj.data.update()
            else:bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False)
        finally:
            if owned:bm.free()
    group=groups.settings(obj)
    if group is not None and group.reference_length<=0 and cached and cached.get('result') and cached['result'].faces:group.reference_length=cached['result'].reference_length
    obj.show_in_front=False
    if obj.nh35.detail_length<=0:
        _rebuild(obj,True)
        cached=_CACHE.get(obj.as_pointer())
        if cached and cached.get('result'):
            obj.nh35.detail_length=cached['result'].reference_length
    groups.remember(obj)


def _selection_state(bm):
    for seq in (bm.verts,bm.edges,bm.faces): seq.index_update()
    history=[]
    for elem in bm.select_history:
        kind=('VERT' if isinstance(elem,bmesh.types.BMVert) else
              'EDGE' if isinstance(elem,bmesh.types.BMEdge) else 'FACE')
        history.append((kind,elem.index))
    return history, bm.faces.active.index if bm.faces.active else -1, set(bm.select_mode)


def _restore_selection(bm,state):
    history,active,mode=state
    seqs={'VERT':bm.verts,'EDGE':bm.edges,'FACE':bm.faces}
    for seq in seqs.values(): seq.ensure_lookup_table(); seq.index_update()
    bm.select_mode=mode
    bm.select_history.clear()
    for kind,index in history:
        if 0<=index<len(seqs[kind]) and seqs[kind][index].select:
            bm.select_history.add(seqs[kind][index])
    bm.faces.active=bm.faces[active] if 0<=active<len(bm.faces) else None


def _edit_transaction(obj, operation):
    """Compute once on a copy; commit it, or restore the complete source mesh.

    Temporary Mesh IDs exist only within this explicit undoable user action.
    Live never calls this helper. No staging element wrapper escapes it.
    """
    if obj.data.shape_keys:
        raise ValueError('Для изменения топологии нужен каркас без Shape Keys')
    live=bmesh.from_edit_mesh(obj.data)
    stage=live.copy()
    pending=None; backup=None
    original_selection=_selection_state(live)
    try:
        value=operation(stage)
        staged_selection=_selection_state(stage)
        pending=bpy.data.meshes.new('.NyanwHair_Commit')
        stage.to_mesh(pending)
        backup=bpy.data.meshes.new('.NyanwHair_Rollback')
        live.to_mesh(backup)
        try:
            live.clear()
            live.from_mesh(pending)
            _restore_selection(live,staged_selection)
            live.normal_update()
            bmesh.update_edit_mesh(obj.data,loop_triangles=True,destructive=True)
        except Exception:
            live.clear()
            live.from_mesh(backup)
            _restore_selection(live,original_selection)
            live.normal_update()
            bmesh.update_edit_mesh(obj.data,loop_triangles=True,destructive=True)
            raise
        _CACHE.pop(obj.as_pointer(),None)
        return value
    finally:
        stage.free()
        for mesh in (pending,backup):
            if mesh is not None: bpy.data.meshes.remove(mesh)


def _set_zone(f,bm,field,value):
    name,is_integer={
        'width': (L_WIDTH,True),
        'bulge': (L_BULGE,False),
        'bias': (L_BIAS,False),
        'flip': (L_FLIP,True),
        'detail': (L_DETAIL,True),
        'back': (L_BACK,False),
        'back_simple': (L_BACK_SIMPLE,True),
        'back_width': (L_BACK_WIDTH,True),
        'back_enabled': (L_BACK_ENABLED,True),
    }[field]
    layers=bm.faces.layers.int if is_integer else bm.faces.layers.float
    layer=layers.get(name)
    if layer is None:
        raise RuntimeError('Схема атрибутов не подготовлена до получения BMFace')
    f[layer]=int(value) if is_integer else float(value)


def _directional_smoothing(bm,objects=False):
    start=bm.loops.layers.float.get(L_SMOOTH_START)
    end=bm.loops.layers.float.get(L_SMOOTH_END)
    if start is None or end is None:return {}
    order=bm.faces.layers.int.get(L_ORDER)
    bm.verts.index_update();bm.faces.index_update()
    output={}
    for edge in bm.edges:
        if not edge.link_loops:continue
        def priority(loop):
            value=int(loop.face[order]) if order else 0
            return (0,value,loop.face.index) if value>0 else (1,loop.face.index,0)
        loop=min(edge.link_loops,key=priority)
        a=loop.vert;b=loop.link_loop_next.vert
        for u,v,stored in ((a,b,loop[start]),(b,a,loop[end])):
            if stored!=0:
                strength=max(0.,min(1.,1.+float(stored)))
                if strength<1.:output[(u,v) if objects else (u.index,v.index)]=strength
    return output


def _write_direction(bm,a,b,strength):
    edge=bm.edges.get((a,b))
    if edge is None:return
    start=bm.loops.layers.float[L_SMOOTH_START];end=bm.loops.layers.float[L_SMOOTH_END]
    value=max(0.,min(1.,strength))-1.
    for loop in edge.link_loops:
        loop[start if loop.vert==a else end]=value


def _restore_directions(bm,records,split_edges=()):
    start=bm.loops.layers.float[L_SMOOTH_START];end=bm.loops.layers.float[L_SMOOTH_END]
    for face in bm.faces:
        for loop in face.loops:loop[start]=0.;loop[end]=0.
    for (a,b),strength in records.items():
        if a.is_valid and b.is_valid:_write_direction(bm,a,b,strength)
    for a,b,middle in split_edges:
        if (a,b) in records:_write_direction(bm,a,middle,records[a,b])
        if (b,a) in records:_write_direction(bm,b,middle,records[b,a])


def _smooth_targets(bm,result):
    active=bm.select_history.active
    if not isinstance(active,bmesh.types.BMVert) or not active.is_valid or not active.select or result is None:return []
    bm.verts.index_update()
    rails={edge for paths in result.face_paths.values() for path in paths for edge in core.physical_edges(path)}
    return [(active.index,edge.other_vert(active).index) for edge in active.link_edges
            if edge.other_vert(active).select and core.edge_key(active.index,edge.other_vert(active).index) in rails]


def _get_direction_smoothing(self):
    obj=bpy.context.edit_object
    if not is_guide(obj):return 1.
    bm=bmesh.from_edit_mesh(obj.data)
    result=_CACHE.get(obj.as_pointer(),{}).get('result')
    targets=_smooth_targets(bm,result);values=_directional_smoothing(bm)
    return sum(values.get(key,1.) for key in targets)/len(targets) if targets else 1.


def _set_direction_smoothing(self,value):
    if _SYNCING_UI:return
    obj=bpy.context.edit_object
    if is_guide(obj):bpy.ops.nyanw35.direction_smoothing('EXEC_DEFAULT',True,value=float(value))


class NYANW35_OT_direction_smoothing(Operator):
    bl_idname='nyanw35.direction_smoothing'
    bl_label='Сглаживание выбранных направлений'
    bl_options={'REGISTER','UNDO','UNDO_GROUPED'}
    value: FloatProperty(name='Сглаживание',default=1.,min=0.,max=1.)
    def execute(self,context):
        obj=context.edit_object
        if not is_guide(obj):return {'CANCELLED'}
        bm=bmesh.from_edit_mesh(obj.data)
        _prepare_bmesh(bm)
        _rebuild(obj,False)
        targets=_smooth_targets(bm,_CACHE.get(obj.as_pointer(),{}).get('result'))
        if not targets:return {'CANCELLED'}
        bm.verts.ensure_lookup_table()
        for a,b in targets:_write_direction(bm,bm.verts[a],bm.verts[b],self.value)
        bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False)
        _rebuild(obj,True)
        return {'FINISHED'}


def _layout_lengths(bm,objects=False):
    layer=bm.edges.layers.float.get(L_LAYOUT_LENGTH)
    if layer is None:return {}
    bm.verts.index_update()
    return {(tuple(e.verts) if objects else core.edge_key(*(v.index for v in e.verts))):float(e[layer]) for e in bm.edges if e[layer]>0.}


def _restore_layout(bm,records,splits=()):
    layer=bm.edges.layers.float[L_LAYOUT_LENGTH]
    for edge in bm.edges:edge[layer]=0.
    for (a,b),value in records.items():
        edge=bm.edges.get((a,b)) if a.is_valid and b.is_valid else None
        if edge is not None:edge[layer]=value
    lookup={frozenset(key):value for key,value in records.items()}
    for a,b,middle in splits:
        value=lookup.get(frozenset((a,b)),0.)
        lengths=[(v.co-middle.co).length for v in (a,b)];total=sum(lengths)
        for vertex,size in zip((a,b),lengths):
            edge=bm.edges.get((vertex,middle))
            if edge is not None:edge[layer]=value*size/max(core.EPS,total)


def _edge_density(bm,objects=False):
    layer=bm.edges.layers.float.get(L_DENSITY)
    if layer is None:return {}
    bm.verts.index_update()
    return {(tuple(edge.verts) if objects else core.edge_key(*(v.index for v in edge.verts))):
            max(1.,min(8.,1.+float(edge[layer]))) for edge in bm.edges if edge[layer]>0.}


def _restore_density(bm,records,split_edges=()):
    layer=bm.edges.layers.float[L_DENSITY]
    for edge in bm.edges:edge[layer]=0.
    for (a,b),factor in records.items():
        edge=bm.edges.get((a,b)) if a.is_valid and b.is_valid else None
        if edge is not None:edge[layer]=factor-1.
    lookup={frozenset(key):value for key,value in records.items()}
    for a,b,middle in split_edges:
        factor=lookup.get(frozenset((a,b)),1.)
        for endpoint in (a,b):
            edge=bm.edges.get((endpoint,middle))
            if edge is not None:edge[layer]=factor-1.


def _density_targets(bm,result):
    if result is None:return []
    return _selected_guide_edges(bm,result,bpy.context.tool_settings.mesh_select_mode[0])


def _point_density(bm):
    gain=bm.verts.layers.float.get(L_POINT_DENSITY);radius=bm.verts.layers.float.get(L_POINT_RADIUS)
    if gain is None:return {}
    bm.verts.index_update()
    return {v.index:(min(8.,1.+float(v[gain])),float(v[radius]) if radius and v[radius]>0. else .2)
            for v in bm.verts if v[gain]>0.}


def _point_density_target(bm,result):
    if result is None:return None
    active=bm.select_history.active
    if not isinstance(active,bmesh.types.BMVert) or not active.is_valid or not active.select:
        selected=[v for v in bm.verts if v.select]
        active=selected[0] if len(selected)==1 else None
    rails={v for paths in result.face_paths.values() for path in paths for v in path}
    return active if active is not None and active.index in rails else None


def _point_density_targets(bm,result):
    bm.verts.index_update()
    rails=({v for paths in result.face_paths.values() for path in paths for v in path} if result is not None
           else {v.index for v in bm.verts if v.link_faces})
    obj=bpy.context.edit_object;uid=groups.active_id(obj) if is_guide(obj) else None
    return [v for v in bm.verts if v.select and not v.hide and v.index in rails and
            (uid is None or groups.vertex_group(v,bm,uid)==uid)]


def _get_point_radius(self):
    obj=bpy.context.edit_object
    if not is_guide(obj):return 20.
    bm=bmesh.from_edit_mesh(obj.data);vertices=_point_density_targets(bm,_CACHE.get(obj.as_pointer(),{}).get('result'));layer=bm.verts.layers.float.get(L_POINT_RADIUS)
    return sum(float(v[layer])*100. if layer and v[layer]>0 else 20. for v in vertices)/len(vertices) if vertices else 20.


def _set_point_radius(self,value):
    if not _SYNCING_UI and is_guide(bpy.context.edit_object):bpy.ops.nyanw35.point_density('EXEC_DEFAULT',True,field='RADIUS',value=float(value))


class NYANW35_OT_point_density(Operator):
    bl_idname='nyanw35.point_density';bl_label='Плотность выбранных точек';bl_options={'REGISTER','UNDO','UNDO_GROUPED'}
    field:EnumProperty(items=[('FACTOR','Сила',''),('RADIUS','Радиус','')],default='FACTOR')
    value:FloatProperty(default=1.,min=.1,max=200.)
    def execute(self,context):
        obj=context.edit_object
        if not is_guide(obj):return {'CANCELLED'}
        bm=bmesh.from_edit_mesh(obj.data);_prepare_bmesh(bm);_rebuild(obj)
        vertices=_point_density_targets(bm,_CACHE.get(obj.as_pointer(),{}).get('result'))
        if not vertices:return {'CANCELLED'}
        layer=bm.verts.layers.float[L_POINT_RADIUS if self.field=='RADIUS' else L_POINT_DENSITY]
        value=max(.001,min(2.,self.value/100.)) if self.field=='RADIUS' else max(1.,min(8.,self.value))-1.
        for vertex in vertices:vertex[layer]=value
        bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False);_rebuild(obj,True)
        return {'FINISHED'}


class NYANW35_OT_density_reset(Operator):
    bl_idname='nyanw35.density_reset';bl_label='Сброс плотности';bl_options={'REGISTER','UNDO'}
    all_points:BoolProperty(default=False)
    def execute(self,context):
        obj=context.edit_object
        if not is_guide(obj):return {'CANCELLED'}
        bm=bmesh.from_edit_mesh(obj.data);_prepare_bmesh(bm);uid=groups.active_id(obj)
        vertices={v for v in bm.verts if groups.vertex_group(v,bm,uid)==uid and (self.all_points or v.select and not v.hide)}
        for vertex in vertices:
            vertex[bm.verts.layers.float[L_POINT_DENSITY]]=0.;vertex[bm.verts.layers.float[L_POINT_RADIUS]]=0.
        for edge in bm.edges:
            if set(edge.verts)<=vertices:edge[bm.edges.layers.float[L_DENSITY]]=0.
        bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False);_rebuild(obj,True)
        return {'FINISHED'}


def _get_local_density(self):
    obj=bpy.context.edit_object
    if not is_guide(obj):return 1.
    bm=bmesh.from_edit_mesh(obj.data);vertices=_point_density_targets(bm,_CACHE.get(obj.as_pointer(),{}).get('result'));values=_point_density(bm)
    return sum(values.get(v.index,(1.,.2))[0] for v in vertices)/len(vertices) if vertices else 1.


def _set_local_density(self,value):
    if not _SYNCING_UI and is_guide(bpy.context.edit_object):bpy.ops.nyanw35.point_density('EXEC_DEFAULT',True,field='FACTOR',value=float(value))


def _on_back_change(self,context):
    if _SYNCING_UI:return
    obj=context.edit_object
    if not is_guide(obj):return
    bm=bmesh.from_edit_mesh(obj.data);_prepare_bmesh(bm)
    for face in _zone_ui_faces(bm,obj):
        _set_zone(face,bm,'back_enabled',self.volume);_set_zone(face,bm,'back',self.back_depth)
    bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False)
    cached=_CACHE.get(obj.as_pointer())
    if cached:cached['source_dirty']=True

class NYANW35_OT_local_density(Operator):
    bl_idname='nyanw35.local_density'
    bl_label='Уплотнение выбранных участков'
    bl_options={'REGISTER','UNDO','UNDO_GROUPED'}
    value: FloatProperty(name='Множитель плотности',default=1.,min=1.,max=8.)
    def execute(self,context):
        obj=context.edit_object
        if not is_guide(obj):return {'CANCELLED'}
        bm=bmesh.from_edit_mesh(obj.data);_prepare_bmesh(bm);_rebuild(obj,False)
        keys=_density_targets(bm,_CACHE.get(obj.as_pointer(),{}).get('result'))
        if not keys:return {'CANCELLED'}
        bm.verts.ensure_lookup_table();layer=bm.edges.layers.float[L_DENSITY]
        for a,b in keys:
            edge=bm.edges.get((bm.verts[a],bm.verts[b]))
            if edge is not None:edge[layer]=self.value-1.
        bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False)
        _rebuild(obj,True)
        return {'FINISHED'}


def _capture(obj,include_smoothing=False,include_detail=False,include_points=False,include_layout=False):
    bm,owned=_mesh_bm(obj)
    try:
        bm.verts.ensure_lookup_table(); bm.verts.index_update()
        bm.faces.ensure_lookup_table(); bm.faces.index_update()
        positions=[tuple(float(x) for x in v.co) for v in bm.verts]
        zones=[tuple(v.index for v in f.verts) for f in bm.faces]
        options=[_get_zone(f,bm) for f in bm.faces]
        # Native F / duplicate can copy a neighbor's custom face attributes.
        # A copied creation ID must not let a new zone preempt older owners.
        seen_orders=set()
        for i,option in enumerate(options):
            if option.order>0:
                if option.order in seen_orders:options[i]=replace(option,order=0)
                seen_orders.add(option.order)
        options=[replace(o,group=groups.active_id(obj)) if o.group<=0 or o.order<=0 else o for o in options]
        cap_layer=_boundary_layer(bm)
        caps=[None if cap_layer is not None and cap_layer.name==L_BOUNDARY and option.order<=0 else _read_caps(face,cap_layer) for face,option in zip(bm.faces,options)]
        # BMesh indices are only temporary array offsets within this snapshot.
        # Capture never writes to source elements.
        selected=_active_face(bm,obj)
        active=selected.index if selected is not None else -1
        snapshot=(positions,zones,options,active,caps)
        if include_smoothing:snapshot+=(_directional_smoothing(bm),)
        if include_detail:snapshot+=(_edge_density(bm),)
        if include_points:snapshot+=(_point_density(bm),)
        if include_layout:snapshot+=(_layout_lengths(bm),)
        return snapshot
    finally:
        if owned: bm.free()


def _signature(positions,zones,options,rows,caps,detail_length=0.,smoothing=None,density=None,point_density=None,layout_lengths=None,regular_rows=False):
    return (bool(regular_rows),tuple(sorted((a,b,round(v,8)) for (a,b),v in (layout_lengths or {}).items())),tuple(sorted((i,round(f,6),round(r,6)) for i,(f,r) in (point_density or {}).items())),int(rows),round(detail_length,8),tuple(sorted((a,b,round(v,6)) for (a,b),v in (density or {}).items())),tuple(sorted((a,b,round(v,6)) for (a,b),v in (smoothing or {}).items())),
            tuple(tuple(round(x,6) for x in p) for p in positions),
            tuple(zones),tuple(caps),
            tuple((o.width,round(o.bulge,5),round(o.bias,5),o.flip,o.order,o.group,o.detail,round(o.back,5),o.back_enabled,o.back_width) for o in options))


def _rebuild(obj,force=False,lazy=False,group_id=None,_background=False):
    """Rebuild the active group; dormant groups retain their GPU-only result."""
    if _UNDOING or not is_guide(obj):return False
    if obj.data.is_editmode and obj.mode!='EDIT':return False
    uid=group_id or groups.active_id(obj);key=obj.as_pointer();stored=groups.CACHE.setdefault(key,{})
    old=stored.get(uid)
    if old is None and _CACHE.get(key,{}).get('group_id',1)==uid:old=_CACHE.get(key)
    if old is not None and not _background:_CACHE[key]=old
    group=groups.settings(obj,uid);reference=(group.reference_length if group else 0.) or (obj.nh35.detail_length if uid==1 else 0.)
    state=(obj.mode,int(obj.nh35.rows),float(reference),True,uid)
    if lazy and not force and old and old.get('signature') is not None and not old.get('source_dirty',True) and old.get('source_state')==state:return False
    positions,full_zones,full_options,active,full_caps,smoothing,density,point_density,layout_lengths=_capture(obj,True,True,True,True)
    indices=[i for i,o in enumerate(full_options) if o.group==uid]
    zones=[full_zones[i] for i in indices];options=[full_options[i] for i in indices];caps=[full_caps[i] for i in indices]
    used={v for loop in zones for v in loop}
    smoothing={e:v for e,v in smoothing.items() if set(e)<=used};density={e:v for e,v in density.items() if set(e)<=used}
    layout_lengths={e:v for e,v in layout_lengths.items() if set(e)<=used};point_density={v:p for v,p in point_density.items() if v in used}
    rows=obj.nh35.rows
    signature=_signature([positions[i] for i in sorted(used)],zones,options,rows,caps,reference,smoothing,density,point_density,layout_lengths,True)
    if old and old.get('signature')==signature and not force:
        old.update(revision=_SOURCE_REVISION,source_state=state,source_dirty=False)
        if active in indices and not old.get('zone_selection_explicit'):old['active']=active
        return False
    unpinned=sum(c is None for c in caps)
    for j,cap in enumerate(caps):
        if cap is None:
            try:
                p=core.plan_face(positions,zones[j],indices[j],options[j]);caps[j]=(p.start,p.end)
            except ValueError:pass
    previous=old['result'] if old and old.get('group_zones')==tuple(zones) and old.get('face_indices')==tuple(indices) else None
    try:
        prior_signature=old.get('signature') if old else None
        if prior_signature and prior_signature!=signature and prior_signature[:-1]==signature[:-1] and tuple(v[:7] for v in prior_signature[-1])==tuple(v[:7] for v in signature[-1]):
            # Back-only edits change metadata, not front geometry or curves.
            from copy import copy
            result=copy(old['result']);current=dict(zip(indices,options))
            result.plans_by_loop={loop:replace(plan,settings=current[plan.index]) for loop,plan in result.plans_by_loop.items()}
        else:
            result=core.generate(positions,zones,options,rows,previous=previous,caps=[core.neutral_caps(loop,c) for loop,c in zip(zones,caps)],reference_length=reference,directional_smoothing=smoothing,local_density=density,point_density=point_density,layout_lengths=layout_lengths,regular_rows=True,face_indices=indices)
        warnings=result.warnings;selection={}
        if old and old.get('zone_selection_explicit'):
            lookup=_zone_lookup(result);keys=frozenset(k for k in old.get('zone_selection',()) if k in lookup)
            selected=next((_zone_key(p) for p in old['result'].plans_by_loop.values() if p.index==old.get('active',-1)),None)
            active=lookup.get(selected,min((lookup[k] for k in keys),default=-1));selection={'zone_selection':keys,'zone_selection_explicit':True}
        if active not in indices:active=indices[0] if indices else -1
        reuse={k:old[k] for k in ('gpu_chunks','gpu_rails','gpu_rail_records','gpu_rail_signature','native_selection_stamp','back_result','normal_topology','gpu_layout','preview_normal_groups','preview_axis_sharp') if old and k in old}
        cached=dict(selection,**reuse,signature=signature,topology=tuple(full_zones),face_indices=tuple(indices),group_zones=tuple(zones),group_id=uid,object_pointer=key,
                    rows=rows,revision=_SOURCE_REVISION,source_state=state,source_dirty=False,data_pointer=obj.data.as_pointer(),positions=tuple(positions),zones=tuple(full_zones),
                    result=result,active=active,status='; '.join('Зона %d: %s'%(i+1,v) for i,v in list(sorted(warnings.items()))[:3]) if warnings else '%d зон, %d полигонов'%(len(zones),len(result.faces)),
                    zone_count=len(zones),error_count=len(warnings),unpinned=unpinned)
        stored[uid]=cached
        if not _background:
            _CACHE[key]=cached
            for other in groups.ids(obj):
                if other not in stored or obj.mode!='EDIT' and stored[other].get('source_dirty'):_rebuild(obj,True,group_id=other,_background=True)
        return True
    except Exception as exc:
        failed=dict(signature=signature,topology=tuple(full_zones),group_zones=tuple(zones),group_id=uid,object_pointer=key,rows=rows,revision=_SOURCE_REVISION,source_state=state,source_dirty=False,
                    data_pointer=obj.data.as_pointer(),positions=tuple(positions),zones=tuple(full_zones),result=core.MeshResult(),active=-1,status='Ошибка построения: %s'%exc,error_count=-1,zone_count=len(zones),unpinned=unpinned)
        stored[uid]=failed
        if not _background:_CACHE[key]=failed
        return False

def _sync_ui(obj):
    global _SYNCING_UI
    if obj is None or obj.mode!='EDIT': return
    if not bpy.context.window_manager: return
    bm,owned=_mesh_bm(obj)
    try:
        f=_zone_ui_primary(bm,obj)
        if f is None: return
        data=_get_zone(f,bm)
        cached=_CACHE.get(obj.as_pointer())
        if cached is not None: cached['active']=f.index
        ui=bpy.context.window_manager.nh35_ui
        _SYNCING_UI=True
        try:
            if ui.width!=data.width: ui.width=data.width
            detail=data.detail or obj.nh35.rows
            if ui.detail!=detail:ui.detail=detail
            enabled=data.back_enabled if data.back_enabled is not None else data.back>0
            if ui.volume!=enabled:ui.volume=enabled
            if ui.back_width!=(data.back_width or data.width):ui.back_width=data.back_width or data.width
            if enabled and abs(ui.back_depth-data.back)>1e-6:ui.back_depth=data.back
            if abs(ui.bulge-data.bulge)>1e-6: ui.bulge=data.bulge
            if abs(ui.bias-data.bias)>1e-6: ui.bias=data.bias
            if ui.flip!=data.flip: ui.flip=data.flip
        finally: _SYNCING_UI=False
    finally:
        if owned: bm.free()


def _on_ui_change(field):
    def update(self,context):
        if _SYNCING_UI: return
        obj=context.edit_object
        if not is_guide(obj): return
        bm=bmesh.from_edit_mesh(obj.data)
        _prepare_bmesh(bm)
        faces=_zone_ui_faces(bm,obj)
        if not faces:return
        try:
            for face in faces:_set_zone(face,bm,field,getattr(self,field))
        except ReferenceError:
            return
        bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False)
        cached=_CACHE.get(obj.as_pointer())
        if cached is not None:
            if field=='back_width':cached['source_dirty']=True
            else:cached['signature']=None
        # The edit BMesh is changed only by this explicit user action.
        # Timer-driven Live never modifies a Blender datablock.
    return update


class NHGuidedProperties(PropertyGroup):
    groups: CollectionProperty(type=groups.NH35HairGroup)
    group_index: IntProperty(get=groups.get_index,set=groups.set_index)
    active_group: IntProperty(default=1,min=1)
    palette: EnumProperty(name='Тема',items=palette.ITEMS,default='WARM')
    brightness: FloatProperty(name="Яркость",default=.8,min=.1,max=2.)
    enabled: BoolProperty(default=False)
    live: BoolProperty(name='Live',default=True)
    show_preview: BoolProperty(name='Поверхность',default=True)
    show_wire: BoolProperty(name='Сетка',default=True,description='Показать рёбра результата; направляющие остаются видимыми')
    wire_opacity: FloatProperty(name='Непрозрачность сетки',default=.22,min=0.,max=1.,subtype='FACTOR')
    smooth_preview: BoolProperty(name='Smooth + Sharp по направляющим',default=True,description='Плавное освещение внутри прядей с резкими границами по направляющим; сохраняется в независимом Mesh')
    generate_uv: BoolProperty(name='UV',default=True,description='Развёртка вдоль локонов, объединение плавных сегментов и Seam на границах островов; вычисляется только при создании Mesh')
    mirror_uv: BoolProperty(name='Mirror UV',default=True,description='UV отражённых локонов совпадают с оригиналом; выключите для отдельных островов отражений')
    uv_merge_angle: FloatProperty(name='Угол сшивания UV',default=math.radians(35.),min=0.,max=math.radians(75.),subtype='ANGLE',description='Максимальный угол между поверхностями и направлениями прядей для объединения UV; острова сохраняют направление сверху вниз')
    follow_matcap: BoolProperty(name='MatCap из вида',default=True,description='Устаревшее поле; освещение всегда следует 3D-виду',options={'HIDDEN'})
    regular_rows: BoolProperty(name='Ровные квадовые полосы',default=True,description='Устаревшее сохранённое свойство; инструменты используют ровные квадовые полосы')
    rows: IntProperty(name='Детализация по длине',default=12,min=2,max=160,description='Общая плотность рядов: короткие пряди получают меньше шагов, длинные больше')
    detail_length: FloatProperty(default=0.,min=0.,options={'HIDDEN'})
    show_tint: BoolProperty(name='Подсветка зоны',default=True)
    tint: FloatVectorProperty(name='Цвет зоны',size=3,subtype='COLOR',min=0.0,max=1.0,default=palette.WARM.tint,get=palette.tint_get,set=palette.tint_set,options={'HIDDEN'})
    opacity: FloatProperty(name='Непрозрачность',default=palette.WARM.tint_strength,min=0,max=0.65,subtype='FACTOR',get=palette.strength_get,set=palette.strength_set)


class NHGuidedUISettings(PropertyGroup):
    detail:IntProperty(name='Детализация',default=12,min=2,max=160,update=_on_ui_change('detail'),description='Плотность полигонов по длине выбранных сегментов; форма направляющих сохраняется')
    volume:BoolProperty(name='Объём',default=False,update=_on_back_change,description='Замкнутая обратная сторона с собственной шириной и выпуклостью')
    back_width:IntProperty(name='Ширина обратной стороны',default=2,min=2,max=32,update=_on_ui_change('back_width'),description='Количество колонок обратной стороны; лицевая сетка сохраняется')
    back_depth:FloatProperty(name='Выпуклость обратной стороны',default=.08,min=0.,max=3.,update=_on_back_change,description='Поперечный профиль обратной стороны; 0 — плоский профиль')
    density_mode: EnumProperty(name='Центр уплотнения',items=[('POINT','Точка','Плавное уплотнение вокруг активной точки'),('EDGE','Рёбра','Прежний множитель на выбранных рёбрах')],default='POINT')
    point_radius: FloatProperty(name='Радиус, % длины',min=.1,max=200.,get=_get_point_radius,set=_set_point_radius,description='Расстояние вдоль направляющих в процентах от опорной длины детализации; плавное затухание до обычной сетки')
    local_density: FloatProperty(name='Множитель плотности',min=1.,max=8.,get=_get_local_density,set=_set_local_density,description='Плотность вокруг всех выбранных точек; форма кривых сохраняется')
    direction_smoothing: FloatProperty(name='Сглаживание',min=0.,max=1.,get=_get_direction_smoothing,set=_set_direction_smoothing,description='Alt + колесо. 1 — плавная касательная; 0 — направление к соседней точке. Только активный конец выбранных направлений')
    width: IntProperty(name='Ширина',min=1,max=32,default=2,update=_on_ui_change('width'),description='Колонки поперёк сегмента. Shift + колесо — изменить у выбранных сегментов')
    bulge: FloatProperty(name='Выпуклость',default=_DEFAULT_BULGE,min=-3,max=3,step=5,precision=3,update=_on_ui_change('bulge'),description='Поперечная выпуклость. Ctrl + колесо — изменить у выбранных сегментов')
    bias: FloatProperty(name='Распределение',default=0.0,min=-2,max=2,step=5,precision=3,update=_on_ui_change('bias'),description='Распределение колонок по ширине; 0 — равномерно')
    flip: BoolProperty(name='Перевернуть',default=False,update=_on_ui_change('flip'))


class NYANW35_OT_create_guide(Operator):
    bl_idname='nyanw35.create_guide'
    bl_label='Создать каркас локонов'
    bl_description='Создать новый управляющий Mesh с одной гранью-зоной'
    bl_options={'REGISTER','UNDO'}

    def execute(self,context):
        me=bpy.data.meshes.new('NyanwHair Guide')
        # A real hair ribbon, not the old rectangle. The control polygon
        # contains a broad root, curved shoulders and a single pointed tip.
        # Only its face owns the direction markers; no vertex/edge tags.
        points=[(.08,0,0),(-.34,0,.75),(-.73,0,1.65),
                (-.72,0,2.48),(-.38,0,3.35),(.39,0,3.35),
                (.73,0,2.50),(.72,0,1.64),(.37,0,.75)]
        me.from_pydata(points,[],[tuple(range(len(points)))])
        me.update()
        bm=bmesh.new()
        try:
            bm.from_mesh(me)
            bm.verts.ensure_lookup_table(); bm.verts.index_update()
            bm.faces.ensure_lookup_table(); bm.faces.index_update()
            layer=bm.loops.layers.int.new(L_CAP)
            width=bm.faces.layers.int.new(L_WIDTH)
            face=bm.faces[0]
            face[width]=4
            # Polygon order: 4 -> 5 is the root cap; vertex 0 is the tip.
            for loop in face.loops:
                if loop.vert.index==4: loop[layer]=1
                elif loop.vert.index==0: loop[layer]=4
            bm.to_mesh(me)
        finally:
            bm.free()
        me.update()
        guide=bpy.data.objects.new('NyanwHair Guided Hair',me)
        context.collection.objects.link(guide)
        for o in context.selected_objects: o.select_set(False)
        guide.select_set(True)
        context.view_layer.objects.active=guide
        guide.nh35.enabled=True
        guide.nh35.show_preview=True
        guide.display_type='WIRE'
        guide.show_in_front=False
        _prepare_object(guide)
        _rebuild(guide,True)
        return {'FINISHED'}


class NYANW35_OT_activate(Operator):
    bl_idname='nyanw35.activate'
    bl_label='Сделать выбранный Mesh каркасом'
    bl_description='Использовать все существующие грани выбранного Mesh как зоны локонов'
    bl_options={'REGISTER','UNDO'}

    @classmethod
    def poll(cls,context):
        return bool(context.object and context.object.type=='MESH' and context.object.mode=='OBJECT')

    def execute(self,context):
        guide=context.object
        guide.nh35.enabled=True
        guide.nh35.show_preview=True
        guide.display_type='WIRE'
        guide.show_in_front=False
        _prepare_object(guide)
        _rebuild(guide,True)
        return {'FINISHED'}


class NYANW35_OT_disable(Operator):
    bl_idname='nyanw35.disable'
    bl_label='Отключить на каркасе'
    bl_description='Отключить аддон на выбранном Mesh и удалить лишь его генерируемый результат'
    bl_options={'REGISTER','UNDO'}

    def execute(self,context):
        guide=context.object
        if not is_guide(guide): return {'CANCELLED'}
        guide.nh35.enabled=False
        guide.display_type='SOLID'
        _CACHE.pop(guide.as_pointer(),None);groups.CACHE.pop(guide.as_pointer(),None)
        return {'FINISHED'}


class NYANW35_OT_demo(Operator):
    bl_idname='nyanw35.demo'
    bl_label='Создать две смежные зоны'
    bl_description='Мини-сцена для проверки общего шва и Merge'
    bl_options={'REGISTER','UNDO'}

    def execute(self,context):
        # Demo presents two adjacent hair ribbons with tapered ends and
        # several control supports along their longitudinal contours.
        pts=[(-.49,0,0),(-.05,0,0),(.34,0,0),
             (-1.04,0,1.40),(.04,0,1.40),(1.00,0,1.40),
             (-.70,0,2.6),(.05,0,2.6),(.78,0,2.6),
             (-.46,0,3.40),(.12,0,3.40),(.65,0,3.40)]
        loops=[(0,1,4,7,10,9,6,3),(1,2,5,8,11,10,7,4)]
        me=bpy.data.meshes.new('NyanwHair Demo Guide')
        me.from_pydata(pts,[],loops)
        me.update()
        # Both test zones have explicit root and end cap edges. They will not
        # reinterpret their direction when a side support point is inserted.
        bm=bmesh.new()
        try:
            bm.from_mesh(me)
            bm.faces.ensure_lookup_table(); bm.faces.index_update()
            cap_layer=bm.loops.layers.int.new(L_CAP)
            width_layer=bm.faces.layers.int.new(L_WIDTH)
            for face in bm.faces:
                corners=list(face.loops)
                corners[4][cap_layer]=1  # root, wide end
                corners[0][cap_layer]=2  # narrow end
                face[width_layer]=3
            bm.to_mesh(me)
        finally:
            bm.free()
        me.update()
        guide=bpy.data.objects.new('NyanwHair Demo Guide',me)
        context.collection.objects.link(guide)
        for o in context.selected_objects: o.select_set(False)
        guide.select_set(True)
        context.view_layer.objects.active=guide
        guide.nh35.enabled=True
        guide.nh35.show_preview=True
        guide.display_type='WIRE'; guide.show_in_front=False
        _prepare_object(guide)
        _rebuild(guide,True)
        return {'FINISHED'}


class NYANW35_OT_rebuild(Operator):
    bl_idname='nyanw35.rebuild'
    bl_label='Перестроить'
    bl_description='Закрепить направления новых зон и пересчитать каркас'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        obj=context.object
        if not is_guide(obj): return {'CANCELLED'}
        _prepare_object(obj)
        _rebuild(obj,True)
        return {'FINISHED'}


class NYANW35_OT_bake(Operator):
    bl_idname='nyanw35.bake'
    bl_label='Сделать независимый Mesh'
    bl_description='Создать обычную копию результата без связи с NyanwHair'
    bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):
        return bool(is_guide(context.object) and context.object.mode=='OBJECT')

    def execute(self,context):
        src=context.object
        if not is_guide(src): return {'CANCELLED'}
        _rebuild(src,False)
        cached=_CACHE.get(src.as_pointer())
        from . import decorations
        result=core.MeshResult();face_groups=[];uv_sources=[]
        for uid in groups.ids(src):
            _rebuild(src,True,group_id=uid,_background=True)
            surface=decorations.display(groups.CACHE[src.as_pointer()][uid],src);offset=len(result.vertices)
            result.vertices.extend(surface.vertices);result.vertex_keys.extend(surface.vertex_keys);result.faces.extend(tuple(v+offset for v in f) for f in surface.faces);result.owners.extend(surface.owners);result.rail_samples.update(surface.rail_samples)
            result.extra_sharp_edges=set(getattr(result,'extra_sharp_edges',()))|{core.edge_key(a+offset,b+offset) for a,b in surface.extra_sharp_edges}
            face_groups.extend([uid]*len(surface.faces))
            if surface.faces:uv_sources.append((uid,surface))
        _CACHE[src.as_pointer()]=groups.CACHE[src.as_pointer()][groups.active_id(src)]
        if not result.faces:
            self.report({'WARNING'},'Нет поверхности для запекания')
            return {'CANCELLED'}
        uv_data=None
        if src.nh35.generate_uv:
            from . import uv
            try:uv_data=uv.generate(uv_sources,src.matrix_world,angle=src.nh35.uv_merge_angle,mirror_uv=src.nh35.mirror_uv)
            except (ValueError,KeyError,IndexError) as exc:
                self.report({'WARNING'},str(exc)+'; UV можно отключить под кнопкой «Создать Mesh»')
                return {'CANCELLED'}
        me=bpy.data.meshes.new(src.name+'_BakedMesh')
        me.from_pydata(result.vertices,[],result.faces)
        me.update(calc_edges=True)
        from . import appearance
        sharp=appearance.sharp_edges(result)
        me.polygons.foreach_set('use_smooth',[bool(src.nh35.smooth_preview)]*len(me.polygons))
        attribute=me.attributes.new(name='sharp_edge',type='BOOLEAN',domain='EDGE')
        attribute.data.foreach_set('value',[core.edge_key(*edge.vertices) in sharp for edge in me.edges])
        me.update()
        if len(me.polygons)==len(result.owners):
            attr=me.attributes.new(name='NyanwHair Zone',type='INT',domain='FACE')
            attr.data.foreach_set('value',result.owners)
        group_attr=me.attributes.new(name='NyanwHair Group',type='INT',domain='FACE');group_attr.data.foreach_set('value',face_groups)
        if uv_data is not None:
            try:uv.apply(me,uv_data[0],uv_data[1])
            except (ValueError,RuntimeError) as exc:
                bpy.data.meshes.remove(me)
                self.report({'WARNING'},'UV: '+str(exc))
                return {'CANCELLED'}
        bake=bpy.data.objects.new(src.name+'_Baked',me)
        if src.users_collection: src.users_collection[0].objects.link(bake)
        else: context.scene.collection.objects.link(bake)
        bake.matrix_world=src.matrix_world.copy()
        bake.hide_select=False
        # The baked object occupies precisely the same surface, so drawing
        # its GPU preview on top would create z-fighting.  This user-invoked
        # operator is an ordinary undoable Blender action.
        src.nh35.show_preview=False
        self.report({'INFO'},'Независимый Mesh создан: '+bake.name)
        return {'FINISHED'}


class NYANW35_OT_pin_rails(Operator):
    bl_idname='nyanw35.pin_rails'
    bl_label='Закрепить две рабочие кривые'
    bl_description=('Выдели два торцевых ребра активной грани. '
                    'Если не выделены, закрепить текущий авторасчёт. '
                    'Метки хранятся на углах грани, не на вершинах.')
    bl_options={'REGISTER','UNDO'}

    @classmethod
    def poll(cls,context):
        return bool(is_guide(context.edit_object))

    def execute(self,context):
        obj=context.edit_object
        bm=bmesh.from_edit_mesh(obj.data)
        _prepare_bmesh(bm)
        f=_active_face(bm,obj)
        if f is None:
            self.report({'WARNING'},'Выбери управляющую грань')
            return {'CANCELLED'}
        bm.verts.index_update()
        bm.faces.index_update()
        loops=list(f.loops)
        selected=[i for i,l in enumerate(loops) if l.edge.select]
        if len(selected)==2:
            choices=(('edge',selected[0]),('edge',selected[1]))
        else:
            points=[tuple(v.co) for v in bm.verts]
            boundary=[v.index for v in f.verts]
            try:
                cached=_CACHE.get(obj.as_pointer())
                prior=(cached['result'].plans_by_loop.get(tuple(boundary))
                       if cached is not None else None)
                plan=core.plan_face(points,boundary,f.index,_get_zone(f,bm),hint=prior)
                choices=(plan.start,plan.end)
            except ValueError as exc:
                self.report({'WARNING'},str(exc))
                return {'CANCELLED'}
        if not core.cap_paths([v.index for v in f.verts],*choices):
            self.report({'WARNING'},'Нужны противоположные торцы одной зоны')
            return {'CANCELLED'}
        layer=bm.loops.layers.int.get(L_CAP)
        if layer is None: raise RuntimeError('Не подготовлены метки торцов')
        for loop in loops: loop[layer]=0
        for which,(kind,index) in enumerate(choices):
            loops[index][layer]=(1 if kind=='edge' else 3) if which==0 else (2 if kind=='edge' else 4)
        _sync_legacy_face(f,bm)
        bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False)
        _CACHE.pop(obj.as_pointer(),None)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Native control-cage edits. Live is strictly read-only; these operators are
# explicit user actions and therefore participate in Blender's normal Undo.
# ---------------------------------------------------------------------------

def _screen_edge(context, mx, my, a, b):
    region=context.region; space=context.region_data
    av=view3d_utils.location_3d_to_region_2d(region,space,a)
    bv=view3d_utils.location_3d_to_region_2d(region,space,b)
    if av is None or bv is None: return (1e9,0.5)
    vec=bv-av
    t=max(0.0,min(1.0,((Vector((mx,my))-av).dot(vec))/max(1e-8,vec.length_squared)))
    return ((av+vec*t-Vector((mx,my))).length,t)


def _cap_edge(face,a,b):
    for e in face.edges:
        if a in e.verts and b in e.verts: return e
    return None


def _cap_records(bm,faces,result):
    """Capture cap endpoints as LOCAL BMesh vertex references before editing.

    BMLoop attributes can be duplicated/reassigned by edge_split.  Capturing
    the precise two cap endpoints lets us restore them from the resulting
    *valid* face, without reading a BMFace removed by BMesh operations.
    """
    records=[]
    for face in faces:
        if not face.is_valid: continue
        plan=result.plans_by_loop.get(tuple(v.index for v in face.verts))
        if plan is None: continue
        loops=list(face.loops)
        caps=[]
        for kind,index in (_read_caps(face,_boundary_layer(bm)) or (plan.start,plan.end)):
            one=loops[index].vert
            two=loops[(index+1)%len(loops)].vert if kind=='edge' else None
            caps.append((kind,one,two))
        records.append((frozenset(face.verts),tuple(caps)))
    return records


def _restore_cap_records(bm,records,modified_vertex=None,exclude=()):
    """Restore face-corner direction after an edge split or a face split.

    A support point cannot change the end caps.  A face split must preserve
    the caps on its unaffected neighbors.  Restrict candidate faces to ones
    containing the captured cap endpoints, not to stale face indices.
    """
    layer=bm.loops.layers.int.get(L_CAP)
    if layer is None: raise RuntimeError('Не подготовлены метки торцов')
    excluded=set(exclude)
    faces=(list(modified_vertex.link_faces) if modified_vertex is not None
           else list(bm.faces))
    used=set()
    for old_vertices,caps in records:
        possibilities=[]
        for face in faces:
            if not face.is_valid or face in excluded or face in used: continue
            loops=list(face.loops)
            positions=[]
            for kind,a,b in caps:
                if not a.is_valid or (b is not None and not b.is_valid): break
                if kind=='point':
                    found=next((i for i,l in enumerate(loops) if l.vert is a),None)
                else:
                    found=next((i for i,l in enumerate(loops)
                                if (l.vert is a and l.link_loop_next.vert is b) or
                                   (l.vert is b and l.link_loop_next.vert is a)),None)
                if found is None: break
                positions.append(found)
            if len(positions)==2:
                overlap=len(old_vertices.intersection(face.verts))
                possibilities.append((overlap,face.index,face,positions,loops))
        if not possibilities:
            raise ValueError('Не удалось сохранить направление соседней зоны')
        _,_,face,positions,loops=max(possibilities,key=lambda x:(x[0],-x[1]))
        for loop in loops: loop[layer]=0
        for side,((kind,_,_),index) in enumerate(zip(caps,positions)):
            loops[index][layer]=(1 if kind=='edge' else 3) if side==0 else (2 if kind=='edge' else 4)
        _sync_legacy_face(face,bm)
        used.add(face)


def _pick_smooth_anchor(context,obj,mx,my,result):
    """Screen-pick the ACTUAL smoothed control curve, not a straight BMEdge.

    Returns source BMEdge indices plus the exact 3D point on the spline.  The
    eventual vertex still belongs to that real Blender edge and is moved to
    the picked curve position immediately after bmesh.utils.edge_split.
    """
    bm=bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table(); bm.verts.index_update()
    bm.edges.ensure_lookup_table(); bm.edges.index_update()
    plans=list(result.plans_by_loop.values())
    evaluator=result.edge_evaluator
    if evaluator is None:
        evaluator=core.make_edge_evaluator([tuple(v.co) for v in bm.verts],plans,result.directional_smoothing)
    source_edges={core.edge_key(*tuple(v.index for v in e.verts)):e.index
                  for e in bm.edges}
    world=obj.matrix_world
    best=(float('inf'),None)
    seen=set()
    for plan in plans:
        for path in (plan.left,plan.right):
            for a,b in zip(path,path[1:]):
                key=core.edge_key(a,b)
                if key in seen or key not in source_edges: continue
                seen.add(key)
                samples=[world@Vector(evaluator(a,b,j/24.)) for j in range(25)]
                for j in range(24):
                    d,frac=_screen_edge(context,mx,my,samples[j],samples[j+1])
                    t=max(.001,min(.999,(j+frac)/24.))
                    if d<best[0] and .025<t<.975:
                        point=Vector(evaluator(a,b,t))
                        best=(d,{'kind':'anchor','edge_index':source_edges[key],
                                 'ends':(a,b),'t':t,'point_local':tuple(point),
                                 'line':[world@point], 'zone':None, 'screen_distance':d})
    return best[1] if best[0]<=9.0 else None


def _pick_cut_line(context,obj,mx,my,result):
    """Only existing longitudinal output edges are eligible for a split.

    No face raycast: its occasional failure near thin generated edges caused
    the previous preview to disappear despite a visible line under the mouse.
    The 2D point-to-segment distance is a strict proximity gate.  A click in
    an empty part of the face does not define a cut and never triggers the
    native Repeat Last command.
    """
    matrix=obj.matrix_world
    best=(float('inf'),float('inf'),None)
    view_origin=view3d_utils.region_2d_to_origin_3d(
        context.region,context.region_data,(mx,my))
    for zone,lines in result.cut_lines.items():
        for station,line in lines.items():
            coords=[matrix@Vector(p) for p in line]
            for a,b in zip(coords,coords[1:]):
                distance,_=_screen_edge(context,mx,my,a,b)
                if distance>10.0:continue
                # Stable depth tie-breaking for overlapping zones. This is
                # a preference only, not an excuse to pick interior pixels.
                depth=min((a-view_origin).length,(b-view_origin).length)
                if (distance,depth)<best[:2]:
                    best=(distance,depth,(zone,station,coords))
    if best[2] is None:return None
    zone,station,coords=best[2]
    from . import cuts
    lines=[coords]
    try:
        for other,line in cuts.preview(result,zone,station).items():
            if other!=zone:lines.append([matrix@Vector(p) for p in line])
    except ValueError:pass
    return {'kind':'split','zone':zone,'t':station,'line':coords,'lines':lines,'screen_distance':best[0]}


def _split_single_bmesh(bm, zone, t, result, select_result=False):
    """Split one face-zone using *native* BMesh topology operations.

    1. Subdivide its two transverse end caps (or use a pointed tip).
    2. Split the polygon with ONE native face_split(coords=...), which creates
       the entire internal rail (including its support vertices) at once.

    The old BMFace may be invalidated by face_split. Nothing retains or
    accesses it afterwards: children are located from fresh mesh topology.
    This function runs on a BMesh copy before the edit BMesh is changed.
    """
    _prepare_bmesh(bm,result)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.ensure_lookup_table()
    bm.edges.index_update()
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    if not 0 <= zone < len(bm.faces):
        raise ValueError('Зона больше не существует')
    original_vertices=set(bm.verts)
    layout_records=_layout_lengths(bm,objects=True)
    density_records=_edge_density(bm,objects=True)
    direction_records=_directional_smoothing(bm,objects=True)
    source = bm.faces[zone]
    plan = result.plans_by_loop.get(tuple(v.index for v in source.verts))
    if plan is None or core.cap_paths(plan.boundary, plan.start, plan.end) is None:
        raise ValueError('Нет действительной пары рабочих кривых')
    if not 0.001 < t < 0.999:
        raise ValueError('Разрез слишком близко к границе')
    settings = _get_zone(source, bm)
    raw_caps=_read_caps(source,_boundary_layer(bm))
    cap_slots=[raw_caps.index(cap) for cap in (plan.start,plan.end)] if raw_caps else [0,1]
    side_b = {plan.right[0],plan.right[-1]}
    side_index = next((vid for vid in plan.left if vid not in side_b),None)
    if side_index is None:
        raise ValueError('Нельзя определить левую сторону')
    side_vertex = bm.verts[side_index]
    profile = core.sample_cut_guide(result, zone, t,
                  max(2,min(12,max(len(plan.left),len(plan.right))-1)))
    if len(profile) < 3 or any(not all(math.isfinite(float(v)) for v in p)
                               for p in profile):
        raise ValueError('Неправильная геометрия линии разреза')

    # Record the source end caps and affected neighbours before ANY BMesh
    # topology edit. The face itself is never referenced after face_split.
    end_caps=[]
    affected=set()
    for k, cap in enumerate((plan.start,plan.end)):
        lv=bm.verts[plan.left[0 if k==0 else -1]]
        rv=bm.verts[plan.right[0 if k==0 else -1]]
        edge=None
        if cap[0]=='point':
            if lv!=rv: raise ValueError('Неправильный точечный торец')
        else:
            edge=_cap_edge(source,lv,rv)
            if edge is None: raise ValueError('Не найдено торцевое ребро')
            for adjacent in edge.link_faces:
                if adjacent!=source:
                    affected.add(adjacent)
                    # Current face-cap representation denotes a ONE-EDGE
                    # cap, not a chain. Splitting a neighbour's own cap
                    # would change its boundary meaning, so reject BEFORE
                    # modifying any live geometry. No spatial guessing.
                    their=result.plans_by_loop.get(tuple(v.index for v in adjacent.verts))
                    if their is not None:
                        for desc in (their.start,their.end):
                            if desc[0]=='edge':
                                i=desc[1]; boundary=their.boundary
                                if {boundary[i],boundary[(i+1)%len(boundary)]} == {lv.index,rv.index}:
                                    raise ValueError('Общий торец требует согласованного разреза соседних зон')
        end_caps.append((cap[0],lv,rv,edge))
    if end_caps[0][3] is not None and end_caps[0][3]==end_caps[1][3]:
        raise ValueError('Два торца совпадают')
    neighbor_caps=_cap_records(bm,affected,result)
    if len(neighbor_caps)!=len(affected):
        raise ValueError('Нельзя надёжно сохранить направление соседней зоны')

    ends=[]
    for k,(kind,left,right,edge) in enumerate(end_caps):
        if kind=='point':
            ends.append(left)
        else:
            _,new_vert=bmesh.utils.edge_split(edge,left,t)
            new_vert.co=Vector(profile[0 if k==0 else -1])
            new_vert[bm.verts.layers.float[L_POINT_DENSITY]]=0.
            new_vert[bm.verts.layers.float[L_POINT_RADIUS]]=0.
            ends.append(new_vert)
    start,finish=ends
    if start==finish: raise ValueError('Оба конца разреза совпадают')
    # After cap edge splits, re-discover the face. Its former Python wrapper
    # is not relied on by any subsequent operation.
    candidates=[f for f in bm.faces if f.is_valid and start in f.verts and finish in f.verts]
    if len(candidates)!=1:
        raise ValueError('Невозможно однозначно найти управляющую грань')
    target=candidates[0]
    if _cap_edge(target,start,finish) is not None:
        raise ValueError('Разрез лежит на существующем ребре')
    # face_split(coords=...) creates the entire internal rail in ONE native
    # BMesh mutation. The previous implementation subdivided the resulting
    # seam N times with edge_split. Every split could reconstruct both incident
    # BMFaces; retaining their Python wrappers was fundamentally unsafe.
    # With this call we never need the old `target` or any intermediate face.
    created=bmesh.utils.face_split(target,start,finish,
                                   coords=tuple(tuple(p) for p in profile[1:-1]))
    if created is None:
        raise ValueError('Blender не смог разделить грань')
    # `target` and `created[0]` MUST NOT be used after the mutation. Determine
    # the two children by topology from the CURRENT mesh, not by face indices.
    children=tuple(f for f in bm.faces if f.is_valid and
                   start in f.verts and finish in f.verts)
    if len(children)!=2:
        raise ValueError('У нового ребра должно быть ровно две грани')
    left_child=next((f for f in children if side_vertex in f.verts),None)
    if left_child is None or all(f==left_child for f in children):
        raise ValueError('Не удалось определить стороны разреза')

    # Neighbours can lose duplicated BMLoop labels when their guide boundary
    # is subdivided. Restore only exact saved endpoints on surviving faces.
    bm.faces.index_update()
    _restore_cap_records(bm,neighbor_caps,exclude=children)
    layer=bm.loops.layers.int.get(L_CAP)
    if layer is None:raise RuntimeError('Не подготовлены метки торцов')
    budget=result.effective_widths.get(zone,settings.width)
    left_width,right_width=core.width_budget_split(budget,t)
    # As in the earlier loft tool, a narrower daughter needs less bulge.
    # Copying the parent's full bulge onto both halves inflates the surface.
    grid=result.surface_sections[zone]
    column=min(len(grid[0])-2,int(t*(len(grid[0])-1)))
    blend=t*(len(grid[0])-1)-column
    v=core.bias_cross_position(t,settings.bias)
    factors=[[],[]]
    for row in grid:
        a=row[0]; b=row[-1]
        cut=core.mix(row[column],row[column+1],blend)
        size=core.length(core.sub(b,a))
        for side,distance,fraction in ((0,core.length(core.sub(cut,a)),v),
                                       (1,core.length(core.sub(b,cut)),1.-v)):
            if distance>core.EPS:
                factors[side].append(size*fraction*fraction/distance)
    gains=[sorted(values)[len(values)//2] if values else .5 for values in factors]
    order_layer=bm.faces.layers.int[L_ORDER]
    new_order=max(int(f[order_layer]) for f in bm.faces)+1
    for child in children:
        child[order_layer]=settings.order if child==left_child else new_order
        for loop in child.loops:
            loop[layer]=0
        for k,(kind,lv,rv,_) in enumerate(end_caps):
            endpoint=ends[k];slot=cap_slots[k]
            if kind=='point':
                marker=next((l for l in child.loops if l.vert==endpoint),None)
                flag=3 if slot==0 else 4
            else:
                marker=next((l for l in child.loops if endpoint in l.edge.verts and
                             (lv in l.edge.verts or rv in l.edge.verts)),None)
                flag=1 if slot==0 else 2
            if marker is None:
                raise ValueError('Не удалось перенести торец дочерней зоны')
            marker[layer]=flag
        _sync_legacy_face(child,bm)
        _set_zone(child,bm,'width',left_width if child==left_child else right_width)
        _set_zone(child,bm,'bulge',settings.bulge*gains[0 if child==left_child else 1])
        _set_zone(child,bm,'bias',settings.bias)
        _set_zone(child,bm,'flip',settings.flip)

    # Check the same topological contract that the preview generator uses.
    bm.verts.index_update()
    bm.faces.index_update()
    points=[tuple(float(v) for v in vertex.co) for vertex in bm.verts]
    for child in children:
        flags={}
        for index,loop in enumerate(child.loops):
            value=int(loop[layer])
            if value in (1,2,3,4):flags[value]=(index,value)
        a=flags.get(1) or flags.get(3)
        b=flags.get(2) or flags.get(4)
        if a is None or b is None:
            raise ValueError('Новая зона потеряла торцевые метки')
        caps=((('edge' if a[1]==1 else 'point'),a[0]),
              (('edge' if b[1]==2 else 'point'),b[0]))
        core.plan_face(points,tuple(v.index for v in child.verts),child.index,
                       _get_zone(child,bm),caps=caps)
    if select_result:
        bm.select_history.clear()
        for f in bm.faces:f.select_set(False)
        left_child.select_set(True)
        bm.faces.active=left_child
        bm.select_history.add(left_child)
    for vertex in bm.verts:
        if vertex not in original_vertices:
            vertex[bm.verts.layers.float[L_POINT_DENSITY]]=0.
            vertex[bm.verts.layers.float[L_POINT_RADIUS]]=0.
    split_records=[(left,right,ends[i]) for i,(kind,left,right,edge) in enumerate(end_caps) if kind=='edge']
    _restore_directions(bm,direction_records,split_records)
    _restore_density(bm,density_records,split_records)
    _restore_layout(bm,layout_records,split_records)
    return budget,left_width,right_width


def _split_on_bmesh(bm,zone,t,result,select_result=False):
    from . import cuts
    if len(cuts.network(result,zone,t))>1:return cuts.split(bm,zone,t,result,select_result)
    return _split_single_bmesh(bm,zone,t,result,select_result)


def _split_control_face(obj, zone, t, result):
    return _edit_transaction(obj, lambda stage:
        _split_on_bmesh(stage,zone,t,result,select_result=True))


def _insert_anchor_bmesh(bm,proposal,result,select_result=False):
    """Create one source vertex at the visible smooth curve position.

    The operation is preflighted on a BMesh COPY first.  No BMFace reference
    from before edge_split is ever accessed after the mutation.  Preserving
    the two cap definitions prevents a newly added rail support from silently
    rotating the whole segment.
    """
    _prepare_bmesh(bm,result)
    bm.verts.ensure_lookup_table(); bm.verts.index_update()
    bm.edges.ensure_lookup_table(); bm.edges.index_update()
    bm.faces.ensure_lookup_table(); bm.faces.index_update()
    idx=proposal['edge_index']
    if not 0<=idx<len(bm.edges):raise ValueError('Исходное ребро исчезло')
    edge=bm.edges[idx]
    if not edge.is_valid or {v.index for v in edge.verts}!=set(proposal['ends']):
        raise ValueError('Исходное ребро изменилось')
    layout_records=_layout_lengths(bm,objects=True)
    density_records=_edge_density(bm,objects=True)
    direction_records=_directional_smoothing(bm,objects=True)
    original_ends=tuple(edge.verts)
    original_faces=list(edge.link_faces)
    if not original_faces:
        raise ValueError('Выбранное ребро не принадлежит зоне')
    records=_cap_records(bm,original_faces,result)
    if len(records)!=len(original_faces):
        raise ValueError('Не удалось определить прежние торцы зон')
    anchor=next((v for v in edge.verts if v.index==proposal['ends'][0]),None)
    if anchor is None:raise ValueError('Начальная вершина ребра недоступна')
    _,new_vert=bmesh.utils.edge_split(edge,anchor,proposal['t'])
    new_vert.co=Vector(proposal['point_local'])
    new_vert[bm.verts.layers.float[L_POINT_DENSITY]]=0.
    new_vert[bm.verts.layers.float[L_POINT_RADIUS]]=0.
    bm.verts.index_update(); bm.faces.index_update()
    _restore_cap_records(bm,records,modified_vertex=new_vert)
    # Verify that the saved face-corner caps still describe two continuous
    # rails.  A bad BMesh split is rejected on the preflight COPY.
    layer=bm.loops.layers.int.get(L_CAP)
    bm.verts.ensure_lookup_table(); bm.verts.index_update()
    points=[tuple(float(c) for c in vert.co) for vert in bm.verts]
    for face in new_vert.link_faces:
        if not face.is_valid: raise ValueError('Грань исчезла при создании опоры')
        marks={}
        for i,loop in enumerate(face.loops):
            flag=int(loop[layer])
            if flag in (1,2,3,4):
                marks[flag]=(i,flag)
        a=marks.get(1) or marks.get(3)
        b=marks.get(2) or marks.get(4)
        if a is None or b is None:
            raise ValueError('Опорная точка повредила направление зоны')
        caps=((('edge' if a[1]==1 else 'point'),a[0]),
              (('edge' if b[1]==2 else 'point'),b[0]))
        if core.cap_paths(tuple(v.index for v in face.verts),*caps) is None:
            raise ValueError('Опорная точка изменила рабочие кривые')
    if select_result:
        bm.select_history.clear()
        for vert in bm.verts:vert.select_set(False)
        new_vert.select_set(True)
        bm.select_history.add(new_vert)
    _restore_directions(bm,direction_records,[(original_ends[0],original_ends[1],new_vert)])
    _restore_density(bm,density_records,[(original_ends[0],original_ends[1],new_vert)])
    _restore_layout(bm,layout_records,[(original_ends[0],original_ends[1],new_vert)])
    return new_vert


class NYANW35_OT_adjust(Operator):
    bl_idname='nyanw35.adjust'
    bl_label='NyanwHair: параметры активного локона'
    bl_options={'REGISTER','UNDO','INTERNAL'}
    field: EnumProperty(items=[('width','Width',''),('bulge','Bulge',''),('smooth','Smoothing',''),('detail','Detail','')])
    direction: IntProperty(default=1)
    @classmethod
    def poll(cls,context): return bool(is_guide(context.edit_object))
    def execute(self,context):
        obj=context.edit_object
        bm=bmesh.from_edit_mesh(obj.data)
        _prepare_bmesh(bm)
        if self.field=='smooth':
            targets=_smooth_targets(bm,_CACHE.get(obj.as_pointer(),{}).get('result'))
            if not targets:return {'CANCELLED'}
            weights=_directional_smoothing(bm);value=round(max(0.,min(1.,sum(weights.get(k,1.) for k in targets)/len(targets)+.1*self.direction)),2)
            return bpy.ops.nyanw35.direction_smoothing('EXEC_DEFAULT',True,value=value)
        faces=_zone_ui_faces(bm,obj)
        if not faces:return {'CANCELLED'}
        changed=False
        for face in faces:
            data=_get_zone(face,bm)
            if self.field=='width':value=max(1,min(32,data.width+(1 if self.direction>0 else -1)))
            elif self.field=='detail':value=max(2,min(160,(data.detail or obj.nh35.rows)+(1 if self.direction>0 else -1)))
            else:value=round(max(-3.,min(3.,data.bulge+.03*(1 if self.direction>0 else -1))),4)
            if value!=getattr(data,self.field):_set_zone(face,bm,self.field,value);changed=True
        if not changed:return {'CANCELLED'}
        bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False)
        cached=_CACHE.get(obj.as_pointer())
        if cached: cached['signature']=None
        _sync_ui(obj)
        return {'FINISHED'}


class NYANW35_OT_hair_loop(Operator):
    bl_idname='nyanw35.hair_loop'
    bl_label='Hair Loop: направляющая или опора'
    bl_description=('Ctrl+R: разрез по продольной линии сетки либо опорная точка на рабочей кривой')
    bl_options={'REGISTER','UNDO','BLOCKING'}
    mode: EnumProperty(items=[('AUTO','Контекстный Hair Loop',''),
                             ('CUT','Разделить зону',''),
                             ('POINT','Опорная точка','')],default='AUTO')

    @classmethod
    def poll(cls,context):
        return bool(is_guide(context.edit_object) and context.area and context.area.type=='VIEW_3D')

    def _locate(self,context,event):
        obj=context.edit_object
        _rebuild(obj)
        cache=_CACHE.get(obj.as_pointer())
        if cache is None: return None
        result=cache['result']
        mx,my=event.mouse_region_x,event.mouse_region_y
        if self.mode=='POINT':
            return _pick_smooth_anchor(context,obj,mx,my,result)
        if self.mode=='CUT':
            return _pick_cut_line(context,obj,mx,my,result)
        # One key, two explicit interaction targets. Source guide wins only
        # when the cursor is directly on its visible smoothed spline; the
        # generated longitudinal line otherwise defines the cut.
        anchor = _pick_smooth_anchor(context,obj,mx,my,result)
        split = _pick_cut_line(context,obj,mx,my,result)
        if anchor is not None and (split is None or anchor['screen_distance'] <= split['screen_distance'] - 1.0):
            return anchor
        return split if split is not None else anchor

    def invoke(self,context,event):
        global _CUT_PREVIEW
        self._obj=context.edit_object
        self._proposal=self._locate(context,event)
        # Always consume the shortcut even without an eligible line.
        # Do NOT pass through to Blender's own Ctrl+R Loop Cut on a guide.
        # Shift+R is not registered here and remains Blender's Repeat Last.
        _CUT_PREVIEW=self._proposal
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self,context,event):
        global _CUT_PREVIEW
        if not is_guide(self._obj) or context.edit_object != self._obj:
            _CUT_PREVIEW=None; return {'CANCELLED'}
        if event.type in {'ESC','RIGHTMOUSE'}:
            _CUT_PREVIEW=None; context.area.tag_redraw()
            return {'CANCELLED'}
        if event.type=='MOUSEMOVE':
            self._proposal=self._locate(context,event)
            _CUT_PREVIEW=self._proposal
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type in {'LEFTMOUSE','RET','NUMPAD_ENTER'} and event.value=='PRESS':
            proposal=self._proposal
            _CUT_PREVIEW=None
            if proposal is None: return {'CANCELLED'}
            try:
                if proposal['kind']=='anchor':
                    result=_CACHE[self._obj.as_pointer()]['result']
                    def insert(stage):
                        _insert_anchor_bmesh(stage,proposal,result,select_result=True)
                    _edit_transaction(self._obj,insert)
                else:
                    result=_CACHE[self._obj.as_pointer()]['result']
                    _split_control_face(self._obj,proposal['zone'],proposal['t'],result)
            except Exception as exc:
                # Keep the full traceback for genuine BMesh/API regressions.
                # A short operator message alone hides the failing callsite.
                print('[NyanwHair] Ctrl+R failed:',repr(proposal),flush=True)
                traceback.print_exc()
                self.report({'WARNING'},'Hair Loop: '+str(exc))
                return {'CANCELLED'}
            context.area.tag_redraw()
            return {'FINISHED'}
        return {'RUNNING_MODAL'}


def _draw_extra_panels(layout,context,obj,cached,edit):
    props=obj.nh35
    header,body=layout.panel('nyanw_appearance',default_closed=True);header.label(text='Внешний вид',icon='SHADING_SOLID')
    if body is not None:
        if edit:body.prop(props,'palette',text='Тема')
        body.prop(props,'smooth_preview',text='Smooth + Sharp');body.prop(props,'brightness',text='Яркость',slider=True)
        row=body.row(align=True);row.prop(props,'show_wire',text='Сетка');column=row.column();column.enabled=props.show_wire;column.prop(props,'wire_opacity',text='',slider=True)
        if edit:body.prop(props,'show_tint',text='Подсветка сегмента')
        if edit and props.show_tint:
            body.prop(props,'opacity',text='Сила выделения',slider=True)
        if cached.get('appearance_status'):body.label(text=cached['appearance_status'],icon='INFO')
    header,body=layout.panel('nyanw_tools',default_closed=True);header.label(text='Дополнительно',icon='PREFERENCES')
    if body is not None:
        if props.generate_uv:body.prop(props,'uv_merge_angle',text='Угол сшивания UV')
        if edit:
            body.prop(context.window_manager.nh35_ui,'flip',text='Перевернуть профиль');body.operator('nyanw35.refresh_layout',text='Обновить раскладку рядов',icon='FILE_REFRESH')
        body.operator('nyanw35.disable',text='Отключить на объекте',icon='X')


class NYANW35_PT_main(Panel):
    bl_label='NyanwHair v0.35.39'
    bl_idname='NYANW35_PT_main'
    bl_space_type='VIEW_3D'
    bl_region_type='UI'
    bl_category='NyanwHair'

    def draw(self,context):
        layout=self.layout;obj=context.object
        if not is_guide(obj):
            layout.operator('nyanw35.create_guide',text='Создать новый локон',icon='CURVE_BEZCURVE')
            if obj and obj.type=='MESH' and obj.mode=='OBJECT':layout.operator('nyanw35.activate',text='Использовать выбранный Mesh',icon='CHECKMARK')
            return
        props=obj.nh35;cached=_CACHE.get(obj.as_pointer(),{});result=cached.get('result');ui=context.window_manager.nh35_ui
        if obj.mode!='EDIT':
            layout.operator('nyanw35.bake',text='Создать Mesh',icon='OUTLINER_OB_MESH')
            row=layout.row(align=True);row.prop(props,'generate_uv',text='UV')
            option=row.row(align=True);option.enabled=props.generate_uv and any(group.mirror_x or group.mirror_y or group.mirror_z for group in props.groups)
            option.prop(props,'mirror_uv',text='Mirror UV')
            layout.operator('nyanw35.create_guide',text='Создать новый локон',icon='CURVE_BEZCURVE')
            _draw_extra_panels(layout,context,obj,cached,False)
            return
        row=layout.row(align=True);row.prop(props,'live',text='Live',toggle=True,icon='PLAY');row.prop(props,'show_preview',text='',icon='HIDE_OFF');row.prop(context.space_data.overlay,'show_overlays',text='',icon='OVERLAY');row.operator('nyanw35.rebuild',text='',icon='FILE_REFRESH')
        header,body=layout.panel('nyanw_groups',default_closed=True);header.label(text='Группы волос',icon='OUTLINER_COLLECTION')
        if body is not None:
            body.template_list('NYANW35_UL_groups','',props,'groups',props,'group_index',rows=max(2,min(4,len(props.groups))))
            row=body.row(align=True);row.operator('nyanw35.group',text='',icon='ADD').action='ADD';remove=row.row(align=True);remove.enabled=len(props.groups)>1;remove.operator('nyanw35.group',text='',icon='REMOVE').action='REMOVE'
            active=groups.settings(obj)
            if active is not None:
                split=body.split(factor=.32,align=True);split.column(align=True).label(text='Mirror',icon='MOD_MIRROR');axes=split.row(align=True)
                for axis in 'xyz':axes.prop(active,'mirror_'+axis,text=axis.upper(),toggle=True)
            else:body.label(text='Нажмите ↻ для подготовки групп')
        bm=bmesh.from_edit_mesh(obj.data)
        layout.separator();layout.label(text='Настройки сегмента',icon='FACESEL')
        parameters=layout.column(align=True);parameters.enabled=bool(_zone_ui_faces(bm,obj))
        parameters.prop(ui,'detail',text='Детализация');parameters.prop(ui,'width',text='Ширина');parameters.prop(ui,'bulge',text='Выпуклость');parameters.prop(ui,'bias',text='Распределение')
        parameters.separator();parameters.prop(ui,'volume',text='Объём',icon='MOD_SOLIDIFY')
        if ui.volume:
            parameters.prop(ui,'back_width',text='Обратная: ширина');parameters.prop(ui,'back_depth',text='Обратная: выпуклость')
        if not cached.get('zone_count',0):layout.operator('nyanw35.group_starter',text='Создать локон в группе',icon='ADD')
        layout.separator();layout.label(text='Края сегмента',icon='EDGESEL')
        ends=layout.column(align=True);ends.enabled=bool(_zone_ui_faces(bm,obj))
        for slot,label in [('FIRST','Начало'),('SECOND','Конец')]:
            row=ends.row(align=True);op=row.operator('nyanw35.boundary',text=label);op.slot=slot;op.clear=False
            op=row.operator('nyanw35.boundary',text='',icon='X');op.slot=slot;op.clear=True
        layout.separator();layout.label(text='Точки',icon='VERTEXSEL')
        row=layout.row(align=True);row.enabled=bool(_smooth_targets(bm,result));row.prop(ui,'direction_smoothing',text='Сглаживание',slider=True);row.operator('nyanw35.direction_smoothing',text='',icon='LOOP_BACK').value=1.
        layout.separator(factor=.4);layout.label(text='Локальная плотность',icon='MOD_SUBSURF')
        vertices=_point_density_targets(bm,result)
        column=layout.column(align=True);column.enabled=bool(vertices);column.prop(ui,'local_density',text='Плотность');column.prop(ui,'point_radius',text='Радиус, %')
        row=layout.row(align=True);selected=row.row(align=True);selected.enabled=bool(vertices);selected.operator('nyanw35.density_reset',text='Сброс').all_points=False
        row.operator('nyanw35.density_reset',text='Сброс все').all_points=True
        _draw_extra_panels(layout,context,obj,cached,True)
        errors=cached.get('error_count',0)
        if errors:layout.label(text='Ошибок зон: %d'%errors,icon='ERROR');layout.label(text=cached.get('status','')[:100])
        elif result:layout.label(text='%d / %d сегм. · %d полигонов'%(len(_zone_ui_faces(bm,obj)),cached.get('zone_count',0),len(cached.get('preview_result',result).faces)*len(getattr(cached.get('preview_result',result),'preview_masks',(0,)))))


_CIRCLE_OFFSETS=tuple(p for i in range(12) for p in ((0.,0.),(math.cos(2*math.pi*i/12),math.sin(2*math.pi*i/12)),(math.cos(2*math.pi*(i+1)/12),math.sin(2*math.pi*(i+1)/12))))
_STROKE_SHADER=None


def _stroke_batch(shader,kind,attributes):
    from . import strokes
    return strokes.batch(shader,kind,attributes)


def _stroke_shader(width=1.,guides=False,normal=(0.,0.,0.),max_clearance=0.):
    """Screen-space capsules with stable projection-depth derivatives."""
    global _STROKE_SHADER
    if _STROKE_SHADER is None:_STROKE_SHADER={}
    if guides not in _STROKE_SHADER:
        info=gpu.types.GPUShaderCreateInfo()
        interface=gpu.types.GPUStageInterfaceInfo('nyanwhair_capsule_aa')
        interface.flat('VEC4','screenSegment');info.vertex_out(interface)
        info.typedef_source('struct StrokeViewport { vec4 rect; };')
        info.uniform_buf(0,'StrokeViewport','strokeViewport')
        info.push_constant('MAT4','viewProjectionMatrix')
        info.push_constant('VEC4','strokeData')
        info.push_constant('VEC4','color')
        info.push_constant('VEC4','projectionData')
        info.vertex_in(0,'VEC3','pos');info.vertex_in(1,'VEC3','other');info.vertex_in(2,'VEC2','corner')
        if guides:
            info.vertex_in(3,'VEC3','normalA');info.vertex_in(4,'VEC3','normalB');info.vertex_in(5,'VEC4','curveGap')
        info.fragment_out(0,'VEC4','fragColor')
        normal_code='vec3 a=normalA;vec3 b=normalB;float incline=max(slope(a),slope(b));vec3 delta=curveGap.xyz;float gap=max(gapDepth(a,delta),gapDepth(b,delta))+curveGap.w*(1.0+incline);' if guides else 'float incline=strokeData.x;float gap=0.0;'
        info.vertex_source("""float slope(vec3 n){
            float size=length(n);
            float facing=abs(dot(n,strokeData.xyz));
            return size<0.000001?1.0:min(8.0,sqrt(max(0.0,size*size-facing*facing))/max(facing,0.12*size));
        }
        float gapDepth(vec3 n,vec3 delta){
            float size=length(n);
            return size<0.000001?abs(dot(delta,strokeData.xyz)):abs(dot(n,delta))/max(abs(dot(n,strokeData.xyz)),0.12*size);
        }
        void main(){
            vec4 current=viewProjectionMatrix*vec4(pos,1.0);
            vec4 adjacent=viewProjectionMatrix*vec4(other,1.0);
            float d0=current.z+current.w,d1=adjacent.z+adjacent.w;
            if(d0<0.0 && d1<0.0){gl_Position=vec4(0.0,0.0,2.0,1.0);screenSegment=vec4(0.0);return;}
            if(d0<0.0)current=mix(current,adjacent,d0/(d0-d1));
            else if(d1<0.0)adjacent=mix(adjacent,current,d1/(d1-d0));
            if(current.w<=0.0000001 || adjacent.w<=0.0000001){gl_Position=vec4(0.0,0.0,2.0,1.0);screenSegment=vec4(0.0);return;}
            vec2 p=(current.xy/current.w*0.5+0.5)*strokeViewport.rect.zw+strokeViewport.rect.xy;
            vec2 q=(adjacent.xy/adjacent.w*0.5+0.5)*strokeViewport.rect.zw+strokeViewport.rect.xy;
            vec2 start=corner.y<0.5?p:q,end=corner.y<0.5?q:p;
            screenSegment=vec4(start,end);
            vec2 direction=end-start;float len=length(direction);
            direction=len>0.00001?direction/len:vec2(1.0,0.0);
            vec2 perpendicular=vec2(-direction.y,direction.x);
            float extent=0.5*strokeData.w+1.0;
            vec2 offset=(perpendicular*corner.x+direction*(2.0*corner.y-1.0))*extent;
            gl_Position=current;
            float w=max(abs(current.w),0.000001);
            float pixelWorld=2.0*w/max(projectionData.x,0.000001);
            // The projection determinant is computed on the CPU. Subtracting
            // two large depth terms here loses the near-plane contribution.
            float gradient=projectionData.y/(w*w);
        """+normal_code+"""
            float padding=pixelWorld*(1.0+(0.5*strokeData.w+1.0)*incline);
            if(projectionData.w>0.0)padding=min(padding,projectionData.w);
            float minimum=projectionData.w>0.0?min(0.00000024,projectionData.w*gradient):0.00000024;
            gl_Position.z-=max(minimum,(gap+padding)*gradient)*gl_Position.w;
            gl_Position.xy+=offset*2.0/strokeViewport.rect.zw*gl_Position.w;
        }""")
        info.fragment_source("""void main(){
            vec2 segment=screenSegment.zw-screenSegment.xy;
            vec2 delta=gl_FragCoord.xy-screenSegment.xy;
            float t=clamp(dot(delta,segment)/max(dot(segment,segment),0.0000001),0.0,1.0);
            vec2 offset=delta-t*segment;float distance=length(offset);
            float aa=max(1.0,(abs(segment.x)+abs(segment.y))/max(length(segment),0.000001));
            if((t<=0.0 || t>=1.0) && distance>0.000001)aa=max(1.0,(abs(offset.x)+abs(offset.y))/distance);
            float coverage=clamp((0.5*strokeData.w+0.5*aa-distance)/aa,0.0,min(1.0,strokeData.w/aa));
            if(coverage<=0.0)discard;
            fragColor=vec4(color.rgb,color.a*coverage);
        }""")
        _STROKE_SHADER[guides]=(gpu.shader.create_from_info(info),gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT',4,[0.,0.,1.,1.])),None)
    shader,viewport_buffer,previous_viewport=_STROKE_SHADER[guides];shader.bind()
    viewport=tuple(float(v) for v in gpu.state.viewport_get())
    if previous_viewport!=viewport:
        viewport_buffer.update(gpu.types.Buffer('FLOAT',4,viewport))
        _STROKE_SHADER[guides]=(shader,viewport_buffer,viewport)
    shader.uniform_block('strokeViewport',viewport_buffer)
    projection=gpu.matrix.get_projection_matrix();view=gpu.matrix.get_model_view_matrix()
    shader.uniform_float('viewProjectionMatrix',projection@view)
    determinant=abs(float(projection[2][2])*float(projection[3][3])-float(projection[2][3])*float(projection[3][2]))
    shader.uniform_float('projectionData',(abs(projection[1][1])*max(1,viewport[3]),determinant,0.,max_clearance))
    direction=Vector(tuple(view[2])[:3]).normalized()
    if guides:data=(*direction,width)
    else:
        vector=Vector(normal);size=vector.length;facing=abs(vector.dot(direction))
        incline=min(8.,math.sqrt(max(0.,size*size-facing*facing))/max(facing,.12*size)) if size>1e-6 else 1.
        data=(incline,0.,0.,width)
    shader.uniform_float('strokeData',data)
    return shader


class _SurfaceBatch:
    """Two winding/index orders share the same vertex attributes."""
    def __init__(self,content,indices,reflected):
        fmt=gpu.types.GPUVertFormat()
        for name,size in (('pos',3),('normal',3),('color',4),('base',3)):fmt.attr_add(id=name,comp_type='F32',len=size,fetch_mode='FLOAT')
        vbo=gpu.types.GPUVertBuf(fmt,len(content['pos']))
        for name,data in content.items():vbo.attr_fill(name,data)
        self.normal=gpu.types.GPUBatch(type='TRIS',buf=vbo,elem=gpu.types.GPUIndexBuf(type='TRIS',seq=indices))
        self.reflected=gpu.types.GPUBatch(type='TRIS',buf=vbo,elem=gpu.types.GPUIndexBuf(type='TRIS',seq=reflected)) if reflected is not None else self.normal
    def draw(self,shader,mask=0):
        (self.reflected if mask.bit_count()%2 else self.normal).draw(shader)


class _WireBatch:
    def __init__(self,batches):self.batches=tuple(batches)
    def __bool__(self):return bool(self.batches)
    def draw(self,shader,mask=0):
        for plane,batch in self.batches:
            if not plane&mask:batch.draw(shader)


class _BatchGroup:
    def __init__(self,batches):self.batches=tuple(b for b in batches if b is not None)
    def __bool__(self):return bool(self.batches)
    def draw(self,shader,mask=0):
        for batch in self.batches:
            if isinstance(batch,(_SurfaceBatch,_WireBatch)):batch.draw(shader,mask)
            else:batch.draw(shader)


def _gpu_mesh_records(cache,result,smooth):
    """Mesh-only records survive selection, color and object transform edits."""
    signature=(id(result),bool(smooth))
    if cache.get('gpu_mesh_signature')==signature:return cache['gpu_mesh_records']
    from . import appearance
    normals=appearance.corner_normals(result,smooth,cache.get('normal_topology'))
    from . import decorations
    normals=decorations.preview_normals(result,smooth,normals,cache)
    cache['normal_topology']=getattr(result,'normal_topology',None)
    plans={p.index:p for p in result.plans_by_loop.values()}
    boundary_keys=tuple(key[1] if key[0]=='shell' else key for key in result.vertex_keys)
    boundary=tuple(i for i,key in enumerate(boundary_keys) if key[0] in ('v','s'))
    front_count=len(cache['result'].faces)
    layout_signature=(tuple(result.faces),tuple(result.owners),boundary,front_count,
        tuple(sorted((owner,p.settings.order) for owner,p in plans.items())))
    saved=cache.get('gpu_layout')
    if saved and saved[0]==layout_signature:layout=saved[1]
    else:
        chunks={};edge_groups={}
        for fi,(face,owner) in enumerate(zip(result.faces,result.owners)):
            plan=plans[owner];key=(plan.settings.order if plan.settings.order>0 else owner)//4
            chunks.setdefault(key,[]).append(fi)
            for a,b in zip(face,face[1:]+face[:1]):
                if a!=b:edge_groups.setdefault(core.edge_key(a,b),key)
        owned_edges={key:[] for key in chunks}
        for edge,key in edge_groups.items():owned_edges[key].append(edge)
        layout=[]
        for key,indices in chunks.items():
            owners=tuple(sorted({result.owners[fi] for fi in indices}))
            vertices=tuple(sorted({v for fi in indices for v in result.faces[fi]}))
            faces_key=(tuple((result.owners[fi],result.faces[fi]) for fi in indices),vertices)
            edges=tuple(owned_edges[key]);keys=edges
            layout.append((key,tuple(indices),owners,vertices,faces_key,edges,keys))
        cache['gpu_layout']=(layout_signature,tuple(layout))
        boundary=set(boundary)
        cache['gpu_boundary_corners']=tuple((vertex,owner,fi,corner) for fi,(face,owner) in enumerate(zip(result.faces[:front_count],result.owners[:front_count])) for corner,vertex in enumerate(face) if vertex in boundary)
    records=[]
    for key,indices,owners,vertices,faces_key,edges,keys in layout:
        faces=tuple((result.faces[fi],result.owners[fi],normals[fi]) for fi in indices)
        geometry=(faces_key,tuple(result.vertices[v] for v in vertices),tuple(normals[fi] for fi in indices))
        records.append((key,faces,owners,geometry,edges,keys))
    boundary_normals={}
    for vertex,owner,fi,corner in cache.get('gpu_boundary_corners',()):
        key=(boundary_keys[vertex],owner);value=normals[fi][corner]
        if key in boundary_normals:boundary_normals[key]=core.add(boundary_normals[key],value)
        else:boundary_normals[key]=value
    cache['gpu_boundary_normals']={key:core.norm(value) for key,value in boundary_normals.items()}
    cache['gpu_mesh_signature']=signature;cache['gpu_mesh_records']=tuple(records)
    return cache['gpu_mesh_records']


def _gpu_batches(cache,guide,shader):
    """Retain small GPU chunks across edits; all zones remain visible."""
    from . import decorations
    result=decorations.preview(cache,guide);matrix=guide.matrix_world
    tint_color,tint_strength=palette.highlight(guide.nh35)
    transform=tuple(float(x) for row in matrix for x in row)
    selected=(_selected_zone_indices(cache) if guide==bpy.context.edit_object and cache.get('group_id',1)==groups.active_id(guide) and _helpers_visible(bpy.context) and guide.nh35.show_tint and tint_strength>0 else set())
    active=cache.get('active',-1)
    tint=(tuple(sorted(selected)),active,tint_color,float(tint_strength))
    base_color=groups.color(guide,cache.get('group_id',1))
    overlays=cache.get('group_id',1)==groups.active_id(guide) and _helpers_visible(bpy.context)
    signature=(id(result),transform,tint,bool(guide.nh35.smooth_preview),base_color,overlays)
    if cache.get('gpu_signature')==signature:return cache['gpu_batches']
    old=cache.get('gpu_chunks',{});current={};coords={};normal_coords={}
    old_wires=cache.get('gpu_wire_chunks',{});wire_chunks={}
    records=_gpu_mesh_records(cache,result,guide.nh35.smooth_preview)
    normal_matrix=matrix.to_3x3().inverted_safe().transposed()
    def world(index):
        if index not in coords:coords[index]=tuple(matrix@Vector(result.vertices[index]))
        return coords[index]
    def world_normal(normal):
        if normal not in normal_coords:normal_coords[normal]=tuple((normal_matrix@Vector(normal)).normalized())
        return normal_coords[normal]
    wire_shader=_stroke_shader()
    for key,faces,owners,geometry,owned_edges,edge_key in records:
        colors_key=tuple((owner,tint_strength*(1. if owner==active else .65) if owner in selected else 0.) for owner in owners)
        stamp=(transform,geometry,edge_key,colors_key,tint_color,base_color,overlays)
        saved=old.get(key)
        if saved and saved[0]==stamp:
            current[key]=saved
            if key in old_wires:wire_chunks[key]=old_wires[key]
            continue
        tris=[];colors=[];vertex_normals=[];indices=[];reflected=[]
        instanced=len(getattr(result,'preview_masks',(0,)))>1
        for face,owner,normals in faces:
            alpha=tint_strength*(1. if owner==active else .65) if owner in selected else 0.
            color=(*tint_color,alpha);offset=len(tris)
            if len(face)>4:
                from .topology import _triangulate
                corner_for={v:i for i,v in enumerate(face)}
                triangles=[tuple(corner_for[v] for v in tri) for tri in _triangulate(result.vertices,face)]
                reverse=[tuple(corner_for[v] for v in tri) for tri in _triangulate(result.vertices,face[::-1])] if instanced else ()
            else:
                triangles=[(0,i,i+1) for i in range(1,len(face)-1)]
                reverse=[(len(face)-1,i,i-1) for i in range(len(face)-2,0,-1)] if instanced else ()
            indices.extend(tuple(offset+i for i in tri) for tri in triangles)
            reflected.extend(tuple(offset+i for i in tri) for tri in reverse)
            for corner,v in enumerate(face):
                tris.append(world(v));colors.append(color);vertex_normals.append(world_normal(normals[corner]))
        solid=_SurfaceBatch({'pos':tris,'normal':vertex_normals,'color':colors,'base':[base_color]*len(tris)},indices,reflected if instanced else None) if tris else None
        axes=getattr(result,'preview_axes',())
        wire_stamp=(transform,geometry,edge_key,overlays,axes);saved_wire=old_wires.get(key)
        if saved_wire and saved_wire[0]==wire_stamp:wire=saved_wire[1]
        else:
            lines={}
            for edge in owned_edges if overlays else ():
                plane=sum(1<<axis for axis,on in enumerate(axes) if on and all(abs(result.vertices[v][axis])<1e-7 for v in edge))
                lines.setdefault(plane,[]).extend(world(v) for v in edge)
            wire=_WireBatch((plane,_stroke_batch(wire_shader,'LINES',{'pos':coords})) for plane,coords in sorted(lines.items())) if lines else None
        wire_chunks[key]=(wire_stamp,wire)
        current[key]=(stamp,solid,wire)
    # Rail batches are independent of mesh density and segment tint.
    evaluate=result.edge_evaluator
    if overlays and evaluate is None:
        evaluate=core.make_edge_evaluator(cache['positions'],list(result.plans_by_loop.values()),result.directional_smoothing)
    from . import strokes
    rail_records,rail_segments,rail_attributes=strokes.rails(cache,result,evaluate,matrix,normal_matrix,overlays)
    rail_signature=tuple((key,record[0]) for key,record in sorted(rail_records.items()))
    rails=cache.get('gpu_rails')
    if cache.get('gpu_rail_signature')!=rail_signature:
        attributes={name:[value for key in sorted(rail_records) for value in rail_records[key][1][name]] for name in ('pos','normalA','normalB','curveGap')}
        rails=_stroke_batch(_stroke_shader(guides=True),'LINES',attributes) if attributes['pos'] else None
    solid=_BatchGroup(item[1] for item in current.values());wire=_BatchGroup(item[2] for item in current.values())
    cache.update(gpu_signature=signature,gpu_batches=(solid,wire,rails),gpu_chunks=current,gpu_wire_chunks=wire_chunks,
        gpu_rails=rails,gpu_rail_records=rail_records,gpu_rail_signature=rail_signature,rail_segments=rail_segments,rail_attributes=rail_attributes)
    return solid,wire,rails


def _helpers_visible(ctx):
    return not ctx.space_data or ctx.space_data.type!='VIEW_3D' or ctx.space_data.overlay.show_overlays


def _xray_enabled(ctx):
    space=ctx.space_data
    if space is None or space.type!='VIEW_3D':return False
    shading=space.shading
    return (shading.show_xray_wireframe if shading.type=='WIREFRAME' else
            shading.show_xray if shading.type=='SOLID' else False)


def _occlusion_state(ctx):
    from . import decorations
    guides=[]
    for guide in ctx.scene.objects:
        if not is_guide(guide) or not guide.visible_get(view_layer=ctx.view_layer) or not guide.nh35.show_preview:continue
        for cached in groups.all_caches(guide):
            result=decorations.preview(cached,guide)
            if not result.faces:continue
            if cached.get('pick_result') is not result:
                cached['pick_bvh']=BVHTree.FromPolygons(result.vertices,result.faces);cached['pick_result']=result
            for mask,reflection in decorations.instance_matrices(result,guide.matrix_world):
                matrix=reflection@guide.matrix_world;guides.append((cached['pick_bvh'],matrix.inverted_safe(),matrix))
    return guides,ctx.evaluated_depsgraph_get()


def _point_visible(ctx,world,state=None,screen=None):
    """POST_PIXEL markers use the same occlusion intent as 3D strokes."""
    if _xray_enabled(ctx):return True
    if state is None:state=_occlusion_state(ctx)
    if screen is None:screen=view3d_utils.location_3d_to_region_2d(ctx.region,ctx.region_data,world)
    if screen is None:return False
    origin=view3d_utils.region_2d_to_origin_3d(ctx.region,ctx.region_data,screen)
    delta=world-origin;distance=delta.length
    if distance<1e-8:return True
    direction=delta/distance
    # One-pixel tolerance keeps a source point visible on its own sampled
    # curved boundary without exposing points through an overlapping sheet.
    neighbor=view3d_utils.region_2d_to_location_3d(ctx.region,ctx.region_data,(screen.x+1,screen.y),world)
    tolerance=max(1e-5,(neighbor-world).length*.75)
    for bvh,inverse,matrix in state[0]:
        hit,_,_,_=bvh.ray_cast(inverse@origin,(inverse.to_3x3()@direction).normalized())
        if hit is not None and (matrix@hit-origin).length<distance-tolerance:return False
    # Native source faces are wire guides, so skip them while checking
    # ordinary scene occluders (head, other meshes and baked surfaces).
    cursor=origin.copy();depsgraph=state[1]
    for _ in range(32):
        remaining=(world-cursor).length-tolerance
        if remaining<=0:break
        hit,location,_,_,obj,_=ctx.scene.ray_cast(depsgraph,cursor,direction,distance=remaining)
        if not hit:break
        original=obj.original if hasattr(obj,'original') else obj
        if not is_guide(original) and original.visible_get(view_layer=ctx.view_layer):return False
        cursor=location+direction*max(tolerance*.1,1e-6)
    return True


def _draw_preview():
    if gpu is None or _UNDOING or not _RUNNING: return
    ctx=bpy.context
    if not ctx.scene or not ctx.region_data: return
    # Synchronize once per viewport redraw, not only on the timer tick.
    # This is strictly read-only for Blender data and avoids dragging lag.
    active=ctx.edit_object
    if is_guide(active) and active.nh35.live:
        try: _rebuild(active,lazy=True)
        except (ReferenceError,RuntimeError): pass
    overlay_depth='NONE' if _xray_enabled(ctx) else 'LESS_EQUAL'
    try:
        from . import appearance
        shader=appearance.shader()
        previous_depth=gpu.state.depth_test_get()
        previous_mask=gpu.state.depth_mask_get()
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(True)
        for obj in ctx.scene.objects:
            if not is_guide(obj) or not obj.visible_get(view_layer=ctx.view_layer) or not obj.nh35.show_preview: continue
            style=palette.get(obj.nh35)
            for cached in groups.all_caches(obj):
                result=cached['result']
                if not result.faces: continue
                solid,wire,rails=_gpu_batches(cached,obj,shader)
                from . import strokes
                overlays=cached.get('group_id',1)==groups.active_id(obj) and _helpers_visible(ctx)
                stroke_scale,depth_limit=strokes.view_style(cached,obj,ctx) if overlays else (1.,0.)
                from . import decorations
                display=decorations.preview(cached,obj)
                original_view=gpu.matrix.get_model_view_matrix()
                instances=decorations.instance_matrices(display,obj.matrix_world)
                if solid:
                    gpu.state.depth_mask_set(True)
                    for mask,reflection in instances:
                        with gpu.matrix.push_pop():
                            if mask:gpu.matrix.multiply_matrix(reflection)
                            shader,status=appearance.bind(ctx,True,obj.nh35.brightness)
                            if mask:
                                normal_matrix=reflection.to_3x3().inverted_safe().transposed()
                                shader.uniform_float('viewRotation',original_view.to_3x3()@normal_matrix)
                            cached['appearance_status']=status;solid.draw(shader,mask)
                gpu.state.depth_mask_set(False)
                if overlays and wire and obj.nh35.show_wire and obj.nh35.wire_opacity>0:
                    gpu.state.depth_test_set(overlay_depth)
                    for mask,reflection in instances:
                        with gpu.matrix.push_pop():
                            if mask:gpu.matrix.multiply_matrix(reflection)
                            line_shader=_stroke_shader(stroke_scale,max_clearance=depth_limit);line_shader.bind()
                            line_shader.uniform_float('color',(*palette.wire_color(groups.color(obj,cached.get('group_id',1))),obj.nh35.wire_opacity))
                            wire.draw(line_shader,mask)
                    gpu.state.depth_test_set('LESS_EQUAL')
                if overlays and rails:
                    # Smooth curves show the actual guiding shape, not only
                    # the straight native Blender control edges.
                    gpu.state.depth_test_set(overlay_depth)
                    guide_shader=_stroke_shader(style.guide_width*stroke_scale,guides=True,max_clearance=depth_limit)
                    guide_shader.bind()
                    guide_shader.uniform_float('color',style.guide)
                    rails.draw(guide_shader)
                    gpu.state.line_width_set(1.0)
                    gpu.state.depth_test_set('LESS_EQUAL')
                if overlays and obj==ctx.edit_object:
                    bm=bmesh.from_edit_mesh(obj.data)
                    selected=_selected_guide_batch(cached,bm,ctx.tool_settings.mesh_select_mode[0])
                    if selected:
                        selected_shader=_stroke_shader(style.selected_width*stroke_scale,guides=True,max_clearance=depth_limit)
                        selected_shader.bind()
                        selected_shader.uniform_float('color',style.selected)
                        gpu.state.depth_test_set(overlay_depth)
                        selected.draw(selected_shader)
                        gpu.state.line_width_set(1.)
                        gpu.state.depth_test_set('LESS_EQUAL')
                    from . import controls
                    for side,coords in controls.caps(bm,obj,cached)[0]:
                        if len(coords)<2:continue
                        primary=_zone_ui_primary(bm,obj);plan=cached['result'].plans_by_loop.get(tuple(v.index for v in primary.verts)) if primary else None
                        normal=(obj.matrix_world.to_3x3().inverted_safe().transposed()@Vector(plan.normal)).normalized() if plan else Vector()
                        endpoint_shader=_stroke_shader(style.cap_width*stroke_scale,normal=normal,max_clearance=depth_limit);endpoint_shader.bind();endpoint_shader.uniform_float('color',style.caps[side])
                        gpu.state.depth_test_set(overlay_depth)
                        _stroke_batch(endpoint_shader,'LINE_STRIP',{'pos':coords}).draw(endpoint_shader)
                    gpu.state.line_width_set(1.);gpu.state.depth_test_set('LESS_EQUAL')
        if _CUT_PREVIEW and _helpers_visible(ctx):
            preview=_CUT_PREVIEW
            from . import strokes
            cut_scale,cut_limit=strokes.view_style(_CACHE.get(active.as_pointer(),{}),active,ctx) if is_guide(active) else (1.,0.)
            for coords in preview.get('lines',(preview['line'],)):
                if not coords:continue
                shader_line=_stroke_shader(3.*cut_scale,max_clearance=cut_limit);shader_line.bind()
                shader_line.uniform_float('color',(.95,.77,.41,1.0));gpu.state.depth_test_set(overlay_depth)
                if len(coords)>=2:
                    batch=_stroke_batch(shader_line,'LINE_STRIP',{'pos':[tuple(p) for p in coords]})
                    batch.draw(shader_line)
                # Anchor markers belong to the POST_PIXEL overlay.
    except (ReferenceError,RuntimeError,ValueError,TypeError):
        # Object can be invalidated by scene replacement or undo.
        pass
    finally:
        gpu.state.depth_mask_set(locals().get('previous_mask',False))
        gpu.state.depth_test_set(locals().get('previous_depth','NONE'))
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')


def _draw_control_points():
    """Pixel-space control markers with viewport-dependent occlusion.

    POST_VIEW points were unreliable with different 3D draw order / point
    clipping in Blender 5.2. POST_PIXEL triangles remain plainly visible.
    """
    if gpu is None or _UNDOING or not _RUNNING:
        return
    ctx = bpy.context
    obj = ctx.edit_object
    if not _helpers_visible(ctx):return
    if not is_guide(obj) or ctx.region_data is None or ctx.region is None:
        return
    try:
        bm = bmesh.from_edit_mesh(obj.data)
        style=palette.get(obj.nh35)
        active = bm.select_history.active
        cache=_CACHE.get(obj.as_pointer(),{})
        selection=tuple((v.select,v.hide,v is active) for v in bm.verts)
        view=(tuple(v for row in ctx.region_data.perspective_matrix for v in row),ctx.region.width,ctx.region.height)
        from . import decorations,strokes
        point_scale=.6+.4*max(0.,min(1.,(strokes.view_style(cache,obj,ctx,pixel=True)[0]-.2)/.8))
        previews=tuple((o.as_pointer(),tuple(id(decorations.preview(entry,o)) for entry in groups.all_caches(o)),tuple(v for row in o.matrix_world for v in row),o.visible_get(view_layer=ctx.view_layer),o.nh35.show_preview) for o in ctx.scene.objects if is_guide(o))
        signature=(_SOURCE_REVISION,id(cache.get('result')),view,selection,previews,_xray_enabled(ctx),ctx.view_layer.as_pointer(),obj.nh35.palette,style)
        saved=cache.setdefault('point_draw_cache',{});region_key=ctx.region.as_pointer()
        record=saved.get(region_key)
        from . import controls
        cap_lines,cap_vertices=controls.caps(bm,obj,cache)
        signature=signature+(tuple(sorted(cap_vertices.items())),point_scale)
        point_sets=[[],[],[],[],[],[],[]]
        if record is None or record[0]!=signature:
            occlusion=_occlusion_state(ctx)
            for vert in bm.verts:
                if vert.hide or groups.vertex_group(vert,bm,groups.active_id(obj))!=groups.active_id(obj):continue
                world=obj.matrix_world@vert.co
                pos=view3d_utils.location_3d_to_region_2d(ctx.region,ctx.region_data,world)
                if pos is None or not (0<=pos.x<=ctx.region.width and 0<=pos.y<=ctx.region.height):continue
                if not _point_visible(ctx,world,occlusion,pos):continue
                if vert is active and vert.select:group=2
                elif vert.index in cap_vertices:group=(5 if vert.select else 3)+cap_vertices[vert.index]
                else:group=1 if vert.select else 0
                point_sets[group].append((pos.x,pos.y))
        old_mask = gpu.state.depth_mask_get()
        old_depth = gpu.state.depth_test_get()
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        gpu.state.depth_mask_set(False)
        def circles(points,radius):
            return [(x+dx*radius,y+dy*radius) for x,y in points for dx,dy in _CIRCLE_OFFSETS]
        if record is None or record[0]!=signature:
            batches=[]
            for coords,(color,radius,outline_color,outline_width) in zip(point_sets,palette.markers(style)):
                if coords:
                    radius*=point_scale;outline_width=max(.65,outline_width*point_scale)
                    outline=batch_for_shader(shader,'TRIS',{'pos':circles(coords,radius+outline_width)})
                    shade=batch_for_shader(shader,'TRIS',{'pos':circles(coords,radius)})
                    batches.append((outline,shade,color,outline_color))
            record=(signature,batches);saved[region_key]=record
            if len(saved)>8:saved.clear();saved[region_key]=record
        for outline,shade,color,outline_color in record[1]:
            shader.bind();shader.uniform_float('color',outline_color);outline.draw(shader)
            shader.uniform_float('color',color);shade.draw(shader)
        if _CUT_PREVIEW is not None and _CUT_PREVIEW.get('kind')=='anchor':
            # Explicit 2D marker, 7-pixel radius. It remains visible on top
            # of source vertices, preview lines, and the generated surface.
            preview=_CUT_PREVIEW.get('line',[])
            if preview and _point_visible(ctx,preview[0]):
                marker=view3d_utils.location_3d_to_region_2d(ctx.region,ctx.region_data,preview[0])
                if marker is not None:
                    position=[(marker.x,marker.y)]
                    ring=batch_for_shader(shader,'TRIS',{'pos':circles(position,7.4)})
                    fill=batch_for_shader(shader,'TRIS',{'pos':circles(position,5.5)})
                    dot=batch_for_shader(shader,'TRIS',{'pos':circles(position,1.8)})
                    shader.bind()
                    shader.uniform_float('color',(.08,.10,.16,1.0)); ring.draw(shader)
                    shader.uniform_float('color',(1.,.83,.37,1.0)); fill.draw(shader)
                    shader.uniform_float('color',(.12,.18,.23,1.0)); dot.draw(shader)
    except (ReferenceError, RuntimeError, ValueError, TypeError):
        pass
    finally:
        if 'old_mask' in locals():
            gpu.state.depth_mask_set(old_mask)
        if 'old_depth' in locals():
            gpu.state.depth_test_set(old_depth)
        gpu.state.blend_set('NONE')


def _ticker():
    if not _RUNNING: return None
    if _UNDOING: return 0.05
    try:
        scene=bpy.context.scene
        if scene is None: return 0.05
        changed=False
        obj=bpy.context.edit_object
        if is_guide(obj):
            cache=_CACHE.get(obj.as_pointer(),{})
            before=(cache.get('active'),cache.get('zone_selection'),cache.get('zone_selection_explicit'))
            _sync_ui(obj)
            changed=before!=(cache.get('active'),cache.get('zone_selection'),cache.get('zone_selection_explicit'))
        for guide in scene.objects:
            if is_guide(guide) and guide.nh35.live:
                changed=_rebuild(guide,lazy=True) or changed
        # GPU-only preview: no object creation/removal, no mesh swapping,
        # no property writes in the periodic update.
        if changed:
            for screen in bpy.data.screens:
                for area in screen.areas:
                    if area.type=='VIEW_3D': area.tag_redraw()
    except (ReferenceError,RuntimeError):
        pass
    return 0.033


@persistent
def _on_scene_update(_scene,_depsgraph):
    global _SOURCE_REVISION
    _SOURCE_REVISION+=1
    for update in _depsgraph.updates:
        source=getattr(update.id,'original',update.id)
        if isinstance(source,bpy.types.Object):
            cached=_CACHE.get(source.as_pointer())
            if cached and (update.is_updated_geometry or source.mode=='EDIT'):cached['source_dirty']=True
            if update.is_updated_geometry:
                for entry in groups.CACHE.get(source.as_pointer(),{}).values():entry['source_dirty']=True
        elif isinstance(source,bpy.types.Mesh):
            pointer=source.as_pointer()
            for entries in groups.CACHE.values():
                for cached in entries.values():
                    if cached.get('data_pointer')==pointer:cached['source_dirty']=True


@persistent
def _on_load(_dummy):
    editing.cancel_all(False)
    _clear_zone_pick()
    global _CUT_PREVIEW
    _CACHE.clear();groups.CACHE.clear()
    _CUT_PREVIEW=None


@persistent
def _on_undo_pre(_dummy):
    editing.cancel_all(False)
    _clear_zone_pick()
    global _UNDOING,_CUT_PREVIEW
    _UNDOING=True
    _CUT_PREVIEW=None
    _CACHE.clear();groups.CACHE.clear()


@persistent
def _on_undo_post(_dummy):
    global _UNDOING
    _CACHE.clear();groups.CACHE.clear()
    _UNDOING=False
    for obj in bpy.data.objects:
        if is_guide(obj):groups.restore(obj)


from . import editing

_CLASSES=groups.CLASSES+(NHGuidedProperties,NHGuidedUISettings,NYANW35_OT_direction_smoothing,NYANW35_OT_local_density,NYANW35_OT_point_density,NYANW35_OT_density_reset,NYANW35_OT_set_cap,NYANW35_OT_refresh_layout,
          NYANW35_OT_create_guide,NYANW35_OT_activate,NYANW35_OT_disable,
          NYANW35_OT_demo,NYANW35_OT_rebuild,NYANW35_OT_bake,
          NYANW35_OT_pin_rails, NYANW35_OT_adjust,NYANW35_OT_pick_zone,
          NYANW35_OT_hair_loop,NYANW35_PT_main)+editing.CLASSES


def _register_keys():
    kc=bpy.context.window_manager.keyconfigs.addon
    if kc is None: return
    km=kc.keymaps.new(name='Mesh',space_type='EMPTY')
    click=km.keymap_items.new('nyanw35.pick_zone','LEFTMOUSE','CLICK',any=True)
    _KEYMAPS.append((km,click))
    for name,key in [('nyanw35.extrude','E'),('nyanw35.merge_menu','M')]:
        item=km.keymap_items.new(name,key,'PRESS');_KEYMAPS.append((km,item))
    # Add-on Mesh keymap takes precedence over Blender's default Mesh Ctrl+R
    # only when poll() accepts a NyanwHair guide. Shift+R is untouched.
    kmi=km.keymap_items.new('nyanw35.hair_loop','R','PRESS',ctrl=True)
    kmi.properties.mode='AUTO'
    _KEYMAPS.append((km,kmi))
    for field,modifiers in (('detail',{'ctrl':True,'shift':True}),('width',{'shift':True}),('bulge',{'ctrl':True}),('smooth',{'alt':True})):
        for button,sign in (('WHEELUPMOUSE',1),('WHEELDOWNMOUSE',-1)):
            kmi=km.keymap_items.new('nyanw35.adjust',button,'PRESS',**modifiers)
            kmi.properties.field=field
            kmi.properties.direction=sign
            _KEYMAPS.append((km,kmi))


def _unregister_keys():
    for km,kmi in reversed(_KEYMAPS):
        try: km.keymap_items.remove(kmi)
        except ReferenceError: pass
    _KEYMAPS.clear()


def register():
    global _RUNNING,_DRAW_HANDLER,_POINT_HANDLER
    for cls in _CLASSES: bpy.utils.register_class(cls)
    bpy.types.Object.nh35=PointerProperty(type=NHGuidedProperties)
    bpy.types.WindowManager.nh35_ui=PointerProperty(type=NHGuidedUISettings)
    for handler_name,handler in (('depsgraph_update_post',_on_scene_update),('load_post',_on_load),
                                 ('undo_pre',_on_undo_pre),
                                 ('undo_post',_on_undo_post),
                                 ('redo_pre',_on_undo_pre),
                                 ('redo_post',_on_undo_post)):
        handlers=getattr(bpy.app.handlers,handler_name)
        if handler not in handlers: handlers.append(handler)
    _RUNNING=True
    _register_keys()
    if not bpy.app.timers.is_registered(_ticker):
        bpy.app.timers.register(_ticker,first_interval=0.05,persistent=True)
    if gpu is not None and not bpy.app.background:
        _DRAW_HANDLER=bpy.types.SpaceView3D.draw_handler_add(_draw_preview,(),'WINDOW','POST_VIEW')
        _POINT_HANDLER=bpy.types.SpaceView3D.draw_handler_add(_draw_control_points,(),'WINDOW','POST_PIXEL')


def unregister():
    global _RUNNING,_DRAW_HANDLER,_POINT_HANDLER,_CUT_PREVIEW,_STROKE_SHADER
    editing.cancel_all()
    from . import appearance
    appearance.clear()
    _STROKE_SHADER=None
    _RUNNING=False
    _clear_zone_pick()
    _CUT_PREVIEW=None
    _unregister_keys()
    if _DRAW_HANDLER is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_DRAW_HANDLER,'WINDOW')
        _DRAW_HANDLER=None
    if _POINT_HANDLER is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_POINT_HANDLER,'WINDOW')
        _POINT_HANDLER=None
    if bpy.app.timers.is_registered(_ticker):
        bpy.app.timers.unregister(_ticker)
    for handler_name,handler in (('depsgraph_update_post',_on_scene_update),('load_post',_on_load),
                                 ('undo_pre',_on_undo_pre),
                                 ('undo_post',_on_undo_post),
                                 ('redo_pre',_on_undo_pre),
                                 ('redo_post',_on_undo_post)):
        handlers=getattr(bpy.app.handlers,handler_name)
        if handler in handlers: handlers.remove(handler)
    del bpy.types.WindowManager.nh35_ui
    del bpy.types.Object.nh35
    for cls in reversed(_CLASSES): bpy.utils.unregister_class(cls)
    _CACHE.clear();groups.CACHE.clear()
