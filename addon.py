"""NyanwHair Guided Surface Blender adapter. F zones own two ordered rails.

Curve direction settings live on face corners, density is a user edge value.
No control-element IDs are used. Live does not mutate Blender datablocks.
"""
import bpy
import bmesh
import math
import traceback
from dataclasses import replace
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty, EnumProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from bpy_extras import view3d_utils

from . import core

try:
    import gpu
    from gpu_extras.batch import batch_for_shader
except ImportError:
    gpu = None
    batch_for_shader = None


L_WIDTH = 'nh35_width'
L_BULGE = 'nh35_bulge'
L_BIAS = 'nh35_bias'
L_FLIP = 'nh35_flip'
L_CAP = 'nh35_cap_corner'  # BMLOOP, per-face direction, never a vertex/edge tag
L_DENSITY = 'nh35_density'
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
    history=bm.select_history.active
    if isinstance(history, bmesh.types.BMFace) and history.is_valid and history.select:
        return history
    candidates=[]
    if isinstance(history, bmesh.types.BMVert) and history.is_valid and history.select:
        candidates=list(history.link_faces)
    elif isinstance(history, bmesh.types.BMEdge) and history.is_valid and history.select:
        candidates=list(history.link_faces)
    if isinstance(history,(bmesh.types.BMVert,bmesh.types.BMEdge)) and history.is_valid and history.select and not candidates:
        return None
    if candidates:
        preferred = (_CACHE.get(obj.as_pointer(),{}).get('active',-1)
                     if obj is not None else -1)
        for f in candidates:
            if f.index == preferred:
                return f
        return max(candidates,key=lambda f:(sum(v.select for v in f.verts),sum(e.select for e in f.edges),-f.index))
    selected=[f for f in bm.faces if f.select]
    if selected:
        preferred=(_CACHE.get(obj.as_pointer(),{}).get('active',-1)
                   if obj is not None else -1)
        return next((f for f in selected if f.index==preferred),selected[-1])
    if obj:
        preferred=_CACHE.get(obj.as_pointer(),{}).get('active',-1)
        if 0<=preferred<len(bm.faces): return bm.faces[preferred]
    return bm.faces[0] if len(bm.faces)==1 else None


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
    """Pick actual preview polygons; source n-gons are not the visible surface."""
    result=cache['result']
    if not result.faces: return None
    if cache.get('pick_result') is not result:
        cache['pick_bvh']=BVHTree.FromPolygons(result.vertices,result.faces)
        cache['pick_result']=result
    _,_,index,_=cache['pick_bvh'].ray_cast(Vector(origin),Vector(direction))
    return result.owners[index] if index is not None else None


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


def _update_zone_selection(bm,cache,hovered,extend=False,component=None):
    zone=hovered if component is False and hovered is not None else _selection_zone(bm,hovered,cache.get('active',-1))
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


def _zone_ui_faces(bm,obj):
    cache=_CACHE.get(obj.as_pointer(),{})
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
            if vertex.hide:continue
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
        # Snapshot preview selection on PRESS, before native selection changes.
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
        shader=gpu.shader.from_builtin('UNIFORM_COLOR')
        cache['selected_batch']=batch_for_shader(shader,'LINES',{'pos':coords}) if coords else None
        cache['selected_signature']=signature
    return cache.get('selected_batch')


def _read_caps(face, layer):
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


def _prepare_bmesh(bm, result=None):
    """Explicit actions only. Allocate the complete schema BEFORE element refs.

    CustomData allocation can invalidate BMesh Python wrappers even without
    a topology edit. Never call this while holding vertices/faces/loops.
    """
    for name,integer,default in ((L_WIDTH,True,2),(L_FLIP,True,0),(L_ORDER,True,0),
                                (L_BULGE,False,_DEFAULT_BULGE),(L_BIAS,False,0.)):
        layers=bm.faces.layers.int if integer else bm.faces.layers.float
        if layers.get(name) is None:
            layer=layers.new(name)
            for face in bm.faces: face[layer]=default
    if bm.loops.layers.int.get(L_CAP) is None:
        bm.loops.layers.int.new(L_CAP)
    if bm.edges.layers.float.get(L_DENSITY) is None:bm.edges.layers.float.new(L_DENSITY)
    for name in (L_SMOOTH_START,L_SMOOTH_END):
        if bm.loops.layers.float.get(name) is None:bm.loops.layers.float.new(name)
    bm.verts.index_update(); bm.faces.index_update()
    order_layer=bm.faces.layers.int[L_ORDER]
    next_order=max((int(f[order_layer]) for f in bm.faces),default=0)+1
    seen=set()
    for face in bm.faces:
        order=int(face[order_layer])
        if order<=0 or order in seen:
            order=next_order;next_order+=1;face[order_layer]=order
        seen.add(order)
    layer=bm.loops.layers.int[L_CAP]
    points=[tuple(v.co) for v in bm.verts]
    for face in bm.faces:
        if _read_caps(face,layer) is not None:
            continue
        boundary=tuple(v.index for v in face.verts)
        hint=result.plans_by_loop.get(boundary) if result else None
        try:
            plan=core.plan_face(points,boundary,face.index,_get_zone(face,bm),hint=hint)
        except ValueError:
            continue
        for loop in face.loops: loop[layer]=0
        loops=list(face.loops)
        for side,(kind,index) in enumerate((plan.start,plan.end)):
            loops[index][layer]=(1 if kind=='edge' else 3) if side==0 else (2 if kind=='edge' else 4)


def _prepare_object(obj):
    bm,owned=_mesh_bm(obj)
    try:
        cached=_CACHE.get(obj.as_pointer())
        _prepare_bmesh(bm,cached['result'] if cached else None)
        if owned:
            bm.to_mesh(obj.data)
            obj.data.update()
        else:
            bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False)
    finally:
        if owned: bm.free()
    obj.show_in_front=False
    if obj.nh35.detail_length<=0:
        _rebuild(obj,True)
        cached=_CACHE.get(obj.as_pointer())
        if cached and cached.get('result'):
            obj.nh35.detail_length=cached['result'].reference_length


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
    value: FloatProperty(name='Сглаживание конца',default=1.,min=0.,max=1.)
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


def _get_local_density(self):
    obj=bpy.context.edit_object
    if not is_guide(obj):return 1.
    bm=bmesh.from_edit_mesh(obj.data)
    keys=_density_targets(bm,_CACHE.get(obj.as_pointer(),{}).get('result'))
    values=_edge_density(bm)
    return sum(values.get(key,1.) for key in keys)/len(keys) if keys else 1.


def _set_local_density(self,value):
    if not _SYNCING_UI and is_guide(bpy.context.edit_object):
        bpy.ops.nyanw35.local_density('EXEC_DEFAULT',True,value=float(value))


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


def _capture(obj,include_smoothing=False,include_detail=False):
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
        cap_layer=bm.loops.layers.int.get(L_CAP)
        caps=[_read_caps(face,cap_layer) for face in bm.faces]
        # BMesh indices are only temporary array offsets within this snapshot.
        # Nothing is written on BMVert or BMEdge.
        selected=_active_face(bm,obj)
        active=selected.index if selected is not None else -1
        snapshot=(positions,zones,options,active,caps)
        if include_smoothing:snapshot+=(_directional_smoothing(bm),)
        if include_detail:snapshot+=(_edge_density(bm),)
        return snapshot
    finally:
        if owned: bm.free()


def _signature(positions,zones,options,rows,caps,detail_length=0.,smoothing=None,density=None):
    return (int(rows),round(detail_length,8),tuple(sorted((a,b,round(v,6)) for (a,b),v in (density or {}).items())),tuple(sorted((a,b,round(v,6)) for (a,b),v in (smoothing or {}).items())),
            tuple(tuple(round(x,6) for x in p) for p in positions),
            tuple(zones),tuple(caps),
            tuple((o.width,round(o.bulge,5),round(o.bias,5),o.flip,o.order) for o in options))


def _rebuild(obj,force=False):
    """Read-only with regard to ALL Blender IDs, safe during Edit Mode/Undo.

    The result exists only in process-local Python memory.  The viewport
    draws it with GPU, and Bake materializes it through an explicit operator.
    """
    if _UNDOING or not is_guide(obj): return False
    if obj.data.is_editmode and obj.mode != 'EDIT': return False
    positions,zones,options,active,caps,smoothing,density=_capture(obj,True,True)
    key=obj.as_pointer()
    rows=obj.nh35.rows
    signature=_signature(positions,zones,options,rows,caps,obj.nh35.detail_length,smoothing,density)
    old=_CACHE.get(key)
    if old and old['signature']==signature and not force:
        if active>=0 and not old.get('zone_selection_explicit'): old['active']=active
        return False
    topology=tuple(zones)
    # Unpinned/new native faces use deterministic current geometry, never a
    # position/index guess from a previous cache. Explicit actions pin them.
    unpinned=sum(cap is None for cap in caps)
    for i,cap in enumerate(caps):
        if cap is None:
            try:
                plan=core.plan_face(positions,zones[i],i,options[i])
                caps[i]=(plan.start,plan.end)
            except ValueError:
                pass
    # Changing the number of longitudinal rows must permit re-sampling;
    # coordinate-only changes reuse the same face plan and mesh row counts.
    previous=(old['result'] if old and old['topology']==topology else None)
    try:
        result=core.generate(positions,zones,options,rows,previous=previous,caps=caps,reference_length=obj.nh35.detail_length,directional_smoothing=smoothing,local_density=density)
        warnings=result.warnings
        if warnings:
            status='; '.join('Зона %d: %s'%(i+1,msg)
                             for i,msg in sorted(warnings.items())[:3])
        else:
            status='%d зон, %d граней результата'%(len(zones),len(result.faces))
        selection={}
        if old and old.get('zone_selection_explicit'):
            lookup=_zone_lookup(result)
            keys=frozenset(k for k in old.get('zone_selection',()) if k in lookup)
            previous=next((_zone_key(p) for p in old['result'].plans_by_loop.values() if p.index==old.get('active',-1)),None)
            active=lookup.get(previous,min((lookup[k] for k in keys),default=-1))
            selection={'zone_selection':keys,'zone_selection_explicit':True}
        # Caches are not Blender IDs and never appear in the undo memfile.
        _CACHE[key]={**selection,
            'signature':signature,'topology':topology,'rows':rows,
            'positions':tuple(positions),'zones':tuple(zones),
            'result':result,'active':active if active>=0 else
                (old.get('active',-1) if old else -1),
            'status':status,'zone_count':len(zones),'error_count':len(warnings),
            'unpinned':unpinned,
        }
        return True
    except Exception as exc:
        _CACHE[key]={
            'signature':signature,'topology':topology,'rows':rows,
            'positions':tuple(positions),'zones':tuple(zones),
            'result':core.MeshResult(),'active':active,
            'status':'Ошибка построения: %s'%exc,
            'zone_count':len(zones),'error_count':-1,
        }
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
        if cached is not None: cached['signature']=None
        # The edit BMesh is changed only by this explicit user action.
        # Timer-driven Live never modifies a Blender datablock.
    return update


class NHGuidedProperties(PropertyGroup):
    enabled: BoolProperty(default=False)
    live: BoolProperty(name='Live',default=True)
    show_preview: BoolProperty(name='GPU-предпросмотр',default=True)
    rows: IntProperty(name='Детализация по длине',default=12,min=2,max=160,description='Общая плотность рядов: короткие пряди получают меньше шагов, длинные больше')
    detail_length: FloatProperty(default=0.,min=0.,options={'HIDDEN'})
    show_tint: BoolProperty(name='Подсветка зоны',default=True)
    tint: FloatVectorProperty(name='Цвет зоны',size=3,subtype='COLOR',min=0.0,max=1.0,default=_COLOR_TINT)
    opacity: FloatProperty(name='Непрозрачность',default=0.16,min=0,max=0.65,subtype='FACTOR')


class NHGuidedUISettings(PropertyGroup):
    local_density: FloatProperty(name='Множитель плотности',min=1.,max=8.,get=_get_local_density,set=_set_local_density,description='Количество отсчётов на выбранных рёбрах направляющих; форма кривой сохраняется')
    direction_smoothing: FloatProperty(name='Сглаживание конца',min=0.,max=1.,get=_get_direction_smoothing,set=_set_direction_smoothing,description='1 — автоматическая плавная касательная; 0 — направление к соседней точке. Только активный конец выбранных направлений')
    width: IntProperty(name='Шаги по ширине',min=1,max=32,default=2,update=_on_ui_change('width'))
    bulge: FloatProperty(name='Выпуклость',default=_DEFAULT_BULGE,min=-3,max=3,step=5,precision=3,update=_on_ui_change('bulge'))
    bias: FloatProperty(name='Распределение',default=0.0,min=-2,max=2,step=5,precision=3,update=_on_ui_change('bias'))
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
        self.report({'INFO'},'Управляющая грань создана. Tab: редактирование. F: новая зона.')
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
        _CACHE.pop(guide.as_pointer(),None)
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
        if not cached or not cached['result'].faces:
            self.report({'WARNING'},'Нет поверхности для запекания')
            return {'CANCELLED'}
        result=cached['result']
        me=bpy.data.meshes.new(src.name+'_BakedMesh')
        me.from_pydata(result.vertices,[],result.faces)
        me.update(calc_edges=True)
        if len(me.polygons)==len(result.owners):
            attr=me.attributes.new(name='NyanwHair Zone',type='INT',domain='FACE')
            attr.data.foreach_set('value',result.owners)
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
        bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False)
        _CACHE.pop(obj.as_pointer(),None)
        self.report({'INFO'},'Направление сохранено в грани. Перемещение точек его не изменяет.')
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
        for kind,index in (plan.start,plan.end):
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
    points=[tuple(v.co) for v in bm.verts]
    plans=list(result.plans_by_loop.values())
    evaluator=core.make_edge_evaluator(points,plans,result.directional_smoothing)
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
    return {'kind':'split','zone':zone,'t':station,'line':coords, 'screen_distance':best[0]}


def _split_on_bmesh(bm, zone, t, result, select_result=False):
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
    density_records=_edge_density(bm,objects=True)
    direction_records=_directional_smoothing(bm,objects=True)
    source = bm.faces[zone]
    plan = result.plans_by_loop.get(tuple(v.index for v in source.verts))
    if plan is None or core.cap_paths(plan.boundary, plan.start, plan.end) is None:
        raise ValueError('Нет действительной пары рабочих кривых')
    if not 0.001 < t < 0.999:
        raise ValueError('Разрез слишком близко к границе')
    settings = _get_zone(source, bm)
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
                                    raise ValueError('Разрез торца соседней зоны пока не поддерживается')
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
            endpoint=ends[k]
            if kind=='point':
                marker=next((l for l in child.loops if l.vert==endpoint),None)
                flag=3 if k==0 else 4
            else:
                marker=next((l for l in child.loops if endpoint in l.edge.verts and
                             (lv in l.edge.verts or rv in l.edge.verts)),None)
                flag=1 if k==0 else 2
            if marker is None:
                raise ValueError('Не удалось перенести торец дочерней зоны')
            marker[layer]=flag
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
    split_records=[(left,right,ends[i]) for i,(kind,left,right,edge) in enumerate(end_caps) if kind=='edge']
    _restore_directions(bm,direction_records,split_records)
    _restore_density(bm,density_records,split_records)
    return budget,left_width,right_width


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
    return new_vert


class NYANW35_OT_adjust(Operator):
    bl_idname='nyanw35.adjust'
    bl_label='NyanwHair: параметры активного локона'
    bl_options={'REGISTER','UNDO','INTERNAL'}
    field: EnumProperty(items=[('width','Width',''),('bulge','Bulge','')])
    direction: IntProperty(default=1)
    @classmethod
    def poll(cls,context): return bool(is_guide(context.edit_object))
    def execute(self,context):
        obj=context.edit_object
        bm=bmesh.from_edit_mesh(obj.data)
        _prepare_bmesh(bm)
        faces=_zone_ui_faces(bm,obj)
        if not faces:return {'CANCELLED'}
        changed=False
        for face in faces:
            data=_get_zone(face,bm)
            if self.field=='width':value=max(1,min(32,data.width+(1 if self.direction>0 else -1)))
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
                    self.report({'INFO'},'Опорная точка создана на сглаженной кривой')
                else:
                    result=_CACHE[self._obj.as_pointer()]['result']
                    budget,a,b=_split_control_face(self._obj,proposal['zone'],proposal['t'],result)
                    self.report({'INFO'},'Разделены две зоны: %d → %d + %d шагов по ширине'%(budget,a,b))
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


class NYANW35_PT_main(Panel):
    bl_label='NyanwHair v0.35.15'
    bl_idname='NYANW35_PT_main'
    bl_space_type='VIEW_3D'
    bl_region_type='UI'
    bl_category='NyanwHair'

    def draw(self,context):
        layout=self.layout
        obj=context.object
        if not is_guide(obj):
            layout.operator('nyanw35.create_guide',icon='MESH_GRID')
            layout.operator('nyanw35.demo',icon='DUPLICATE')
            if obj and obj.type=='MESH' and obj.mode=='OBJECT':
                layout.operator('nyanw35.activate',icon='CHECKMARK')
            layout.label(text='F задаёт зону; две кривые задают форму',icon='INFO')
            return
        props=obj.nh35
        selected_count=len(_selected_zone_indices(_CACHE.get(obj.as_pointer(),{})))
        if selected_count>1:layout.label(text='Выбрано сегментов: %d'%selected_count,icon='FACESEL')
        row=layout.row(align=True)
        row.prop(props,'live',text='Live',toggle=True,icon='PLAY')
        row.operator('nyanw35.rebuild',text='',icon='FILE_REFRESH')
        layout.prop(props,'rows')
        layout.prop(props,'show_preview')
        box=layout.box()
        box.label(text='Зона F + две рабочие кривые',icon='FACESEL')
        if obj.mode!='EDIT':
            box.label(text='Tab: Edit Mode, F: создать грань')
        else:
            box.label(text='F / M / V / Shift+D: обычный Blender')
            ui=context.window_manager.nh35_ui
            parameters=box.column()
            parameters.enabled=bool(_zone_ui_faces(bmesh.from_edit_mesh(obj.data),obj))
            parameters.prop(ui,'width')
            parameters.prop(ui,'bulge')
            parameters.prop(ui,'bias')
            parameters.prop(ui,'flip')
            smooth=box.box()
            smooth.label(text='Сглаживание направлений',icon='CURVE_BEZCURVE')
            mesh=bmesh.from_edit_mesh(obj.data)
            targets=_smooth_targets(mesh,_CACHE.get(obj.as_pointer(),{}).get('result'))
            controls=smooth.row(align=True);controls.enabled=bool(targets)
            controls.prop(ui,'direction_smoothing',slider=True)
            reset=controls.operator('nyanw35.direction_smoothing',text='Авто');reset.value=1.
            if targets:
                smooth.label(text='Выбрано направлений: %d'%len(targets))
                weights=_directional_smoothing(mesh)
                values=[weights.get(key,1.) for key in targets]
                if max(values)-min(values)>1e-5:smooth.label(text='Разные значения',icon='INFO')
            smooth.label(text='Нужную точку выдели последней')
            smooth.label(text='Соседние выбранные точки задают направления')
            density_box=box.box()
            density_box.label(text='Локальная плотность',icon='MOD_SUBSURF')
            density_keys=_density_targets(mesh,_CACHE.get(obj.as_pointer(),{}).get('result'))
            density_controls=density_box.column(align=True);density_controls.enabled=bool(density_keys)
            density_controls.prop(ui,'local_density')
            row=density_controls.row(align=True)
            for label,value in [('×2',2.),('×4',4.),('Сброс',1.)]:
                op=row.operator('nyanw35.local_density',text=label);op.value=value
            density_box.label(text='Выдели рёбра нужного участка')
            action=box.operator('nyanw35.hair_loop',text='Hair Loop (Ctrl+R)',icon='MOD_SUBSURF')
            action.mode='AUTO'
            box.label(text='Shift+колесо: ширина; Ctrl+колесо: выпуклость')
            box.label(text='Параметры принадлежат управляющей грани')
            box.operator('nyanw35.pin_rails',text='Закрепить направление кривых',icon='CURVE_BEZCURVE')
        box=layout.box()
        box.label(text='Подсветка',icon='SHADING_SOLID')
        box.prop(props,'show_tint')
        if props.show_tint:
            box.prop(props,'tint')
            box.prop(props,'opacity')
        layout.label(text='Live без фоновой записи в Mesh',icon='INFO')
        layout.operator('nyanw35.bake',icon='OUTLINER_OB_MESH')
        layout.operator('nyanw35.disable',icon='X')
        cached=_CACHE.get(obj.as_pointer(),{})
        if cached.get('unpinned',0):
            layout.label(text='Новые зоны: закрепи направление или нажми Перестроить',icon='INFO')
        error_count=cached.get('error_count',0)
        if error_count>0:
            layout.label(text='Ошибки зон: %d'%error_count,icon='ERROR')
        elif error_count<0:
            layout.label(text='Ошибка генератора',icon='ERROR')
        layout.label(text=cached.get('status','Ожидание первого расчёта')[:120],icon='INFO')
        layout.label(text='Два выбранных торцевых ребра: задать направление',icon='DOT')


_STROKE_SHADER=None


def _stroke_shader():
    global _STROKE_SHADER
    if _STROKE_SHADER is None:
        info=gpu.types.GPUShaderCreateInfo()
        info.push_constant('MAT4','viewProjectionMatrix')
        info.push_constant('VEC4','color')
        info.push_constant('VEC4','projectionData')
        info.vertex_in(0,'VEC3','pos')
        info.fragment_out(0,'VEC4','fragColor')
        info.vertex_source("""void main(){
            gl_Position=viewProjectionMatrix*vec4(pos,1.0);
            float w=max(abs(gl_Position.w),0.000001);
            float pixelWorld=2.0*w/(abs(projectionData.x)*projectionData.w);
            float gradient=abs(projectionData.y*gl_Position.w-gl_Position.z*projectionData.z)/(w*w);
            gl_Position.z-=max(0.0000001,0.75*pixelWorld*gradient)*gl_Position.w;
        }""")
        info.fragment_source("void main(){fragColor=color;}")
        _STROKE_SHADER=gpu.shader.create_from_info(info)
    _STROKE_SHADER.bind()
    projection=gpu.matrix.get_projection_matrix()
    _STROKE_SHADER.uniform_float('viewProjectionMatrix',projection@gpu.matrix.get_model_view_matrix())
    _STROKE_SHADER.uniform_float('projectionData',(projection[1][1],projection[2][2],projection[3][2],max(1,gpu.state.viewport_get()[3])))
    return _STROKE_SHADER


def _gpu_batches(cache,guide,shader):
    """Cache GPU uploads by result identity and object transform.

    No Blender IDs are modified here.  GPU resources are ephemeral.
    """
    result=cache['result']
    transform=tuple(float(x) for row in guide.matrix_world for x in row)
    selected=set()
    if guide==bpy.context.edit_object and guide.nh35.show_tint and guide.nh35.opacity>0:
        selected=_selected_zone_indices(cache)
    active=cache.get('active',-1)
    tint=(tuple(sorted(selected)),active,tuple(guide.nh35.tint),float(guide.nh35.opacity))
    signature=(id(result),transform,tint)
    if cache.get('gpu_signature')==signature: return cache['gpu_batches']
    matrix=guide.matrix_world
    coords=[tuple(matrix @ Vector(v)) for v in result.vertices]
    tris=[]; colors=[]; edges=set()
    guide_lines=[]
    rail_segments={}
    evaluate=core.make_edge_evaluator(cache['positions'],list(result.plans_by_loop.values()),result.directional_smoothing)
    for paths in result.face_paths.values():
        for path in paths:
            for a,b in zip(path,path[1:]):
                key=core.edge_key(a,b)
                if key in rail_segments: continue
                curve=[tuple(matrix@Vector(evaluate(a,b,j/24.))) for j in range(25)]
                segments=[p for pair in zip(curve,curve[1:]) for p in pair]
                rail_segments[key]=segments
                guide_lines.extend(segments)
    cache['rail_segments']=rail_segments
    light=Vector((.31,-.43,.85)).normalized()
    for poly,owner in zip(result.faces,result.owners):
        if len(poly)<3: continue
        a,b,c=(Vector(coords[poly[i]]) for i in (0,1,2))
        n=(b-a).cross(c-a)
        if n.length_squared>1.0e-15: n.normalize()
        else: n=Vector((0,0,1))
        intensity=.70+.26*abs(n.dot(light))
        base=(.74*intensity,.79*intensity,.87*intensity)
        if owner in selected:
            alpha=guide.nh35.opacity*(1. if owner==active else .65)
            base=tuple(v*(1.-alpha)+color*alpha for v,color in zip(base,guide.nh35.tint))
        face_color=(*base,1.0)
        for i in range(1,len(poly)-1):
            for v in (poly[0],poly[i],poly[i+1]):
                tris.append(coords[v]); colors.append(face_color)
        for i,a in enumerate(poly):
            b=poly[(i+1)%len(poly)]
            if a!=b: edges.add(tuple(sorted((a,b))))
    solid=batch_for_shader(shader,'TRIS',{'pos':tris,'color':colors}) if tris else None
    wire_shader=_stroke_shader()
    lines=[coords[j] for e in sorted(edges) for j in e]
    wire=batch_for_shader(wire_shader,'LINES',{'pos':lines}) if lines else None
    rails=batch_for_shader(wire_shader,'LINES',{'pos':guide_lines}) if guide_lines else None
    cache['gpu_signature']=signature
    cache['gpu_batches']=(solid,wire,rails)
    return solid,wire,rails


def _xray_enabled(ctx):
    space=ctx.space_data
    if space is None or space.type!='VIEW_3D':return False
    shading=space.shading
    return (shading.show_xray_wireframe if shading.type=='WIREFRAME' else
            shading.show_xray if shading.type=='SOLID' else False)


def _point_visible(ctx,world):
    """POST_PIXEL markers use the same occlusion intent as 3D strokes."""
    if _xray_enabled(ctx):return True
    screen=view3d_utils.location_3d_to_region_2d(ctx.region,ctx.region_data,world)
    if screen is None:return False
    origin=view3d_utils.region_2d_to_origin_3d(ctx.region,ctx.region_data,screen)
    delta=world-origin;distance=delta.length
    if distance<1e-8:return True
    direction=delta/distance
    # One-pixel tolerance keeps a source point visible on its own sampled
    # curved boundary without exposing points through an overlapping sheet.
    neighbor=view3d_utils.region_2d_to_location_3d(ctx.region,ctx.region_data,(screen.x+1,screen.y),world)
    tolerance=max(1e-5,(neighbor-world).length*.75)
    for guide in ctx.scene.objects:
        if not is_guide(guide) or not guide.visible_get(view_layer=ctx.view_layer) or not guide.nh35.show_preview:continue
        cached=_CACHE.get(guide.as_pointer())
        if not cached or not cached['result'].faces:continue
        result=cached['result']
        if cached.get('pick_result') is not result:
            cached['pick_bvh']=BVHTree.FromPolygons(result.vertices,result.faces)
            cached['pick_result']=result
        inverse=guide.matrix_world.inverted_safe()
        hit,_,_,_=cached['pick_bvh'].ray_cast(inverse@origin,(inverse.to_3x3()@direction).normalized())
        if hit is not None and (guide.matrix_world@hit-origin).length<distance-tolerance:return False
    # Native source faces are wire guides, so skip them while checking
    # ordinary scene occluders (head, other meshes and baked surfaces).
    cursor=origin.copy();depsgraph=ctx.evaluated_depsgraph_get()
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
        try: _rebuild(active)
        except (ReferenceError,RuntimeError): pass
    overlay_depth='NONE' if _xray_enabled(ctx) else 'LESS_EQUAL'
    try:
        shader=gpu.shader.from_builtin('SMOOTH_COLOR')
        previous_depth=gpu.state.depth_test_get()
        previous_mask=gpu.state.depth_mask_get()
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(True)
        for obj in ctx.scene.objects:
            if not is_guide(obj) or not obj.visible_get(view_layer=ctx.view_layer) or not obj.nh35.show_preview: continue
            cached=_CACHE.get(obj.as_pointer())
            if not cached: continue
            result=cached['result']
            if not result.faces: continue
            solid,wire,rails=_gpu_batches(cached,obj,shader)
            if solid:
                gpu.state.depth_mask_set(True)
                shader.bind()
                solid.draw(shader)
            gpu.state.depth_mask_set(False)
            if wire:
                # Mesh strokes obey the viewport's X-ray toggle.
                gpu.state.depth_test_set(overlay_depth)
                line_shader=_stroke_shader()
                line_shader.bind()
                line_shader.uniform_float('color',(.10,.13,.17,.42))
                wire.draw(line_shader)
                gpu.state.depth_test_set('LESS_EQUAL')
            if rails:
                # Smooth curves show the actual guiding shape, not only
                # the straight native Blender control edges.
                gpu.state.depth_test_set(overlay_depth)
                guide_shader=_stroke_shader()
                guide_shader.bind()
                guide_shader.uniform_float('color',_COLOR_BLUE)
                gpu.state.line_width_set(2.4)
                rails.draw(guide_shader)
                gpu.state.line_width_set(1.0)
                gpu.state.depth_test_set('LESS_EQUAL')
            if obj==ctx.edit_object:
                bm=bmesh.from_edit_mesh(obj.data)
                selected=_selected_guide_batch(cached,bm,ctx.tool_settings.mesh_select_mode[0])
                if selected:
                    selected_shader=_stroke_shader()
                    selected_shader.bind()
                    selected_shader.uniform_float('color',(1.,.74,.46,.72))
                    gpu.state.depth_test_set(overlay_depth)
                    gpu.state.line_width_set(3.4)
                    selected.draw(selected_shader)
                    gpu.state.line_width_set(1.)
                    gpu.state.depth_test_set('LESS_EQUAL')
        if _CUT_PREVIEW:
            preview=_CUT_PREVIEW
            coords=preview['line']
            if coords:
                shader_line=_stroke_shader()
                shader_line.bind()
                shader_line.uniform_float('color',(.95,.77,.41,1.0))
                gpu.state.depth_test_set(overlay_depth)
                if len(coords)>=2:
                    batch=batch_for_shader(shader_line,'LINE_STRIP',{'pos':[tuple(p) for p in coords]})
                    gpu.state.line_width_set(3.0)
                    batch.draw(shader_line)
                # Anchor marker belongs to the POST_PIXEL overlay instead.
                # 3D point size is unreliable at steep viewing angles.
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
    if not is_guide(obj) or ctx.region_data is None or ctx.region is None:
        return
    try:
        bm = bmesh.from_edit_mesh(obj.data)
        active = bm.select_history.active
        groups = [[], [], []]
        for vert in bm.verts:
            world=obj.matrix_world @ vert.co
            if not _point_visible(ctx,world):continue
            pos = view3d_utils.location_3d_to_region_2d(
                ctx.region, ctx.region_data, world)
            if pos is None or not (0 <= pos.x <= ctx.region.width and
                                   0 <= pos.y <= ctx.region.height):
                continue
            group = 2 if vert is active and vert.select else 1 if vert.select else 0
            groups[group].append((pos.x, pos.y))
        old_mask = gpu.state.depth_mask_get()
        old_depth = gpu.state.depth_test_get()
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        gpu.state.depth_mask_set(False)
        def circles(points, radius):
            triangles=[]
            n=12
            for x,y in points:
                for i in range(n):
                    a=2*math.pi*i/n
                    b=2*math.pi*(i+1)/n
                    triangles.extend(((x,y),
                        (x+radius*math.cos(a),y+radius*math.sin(a)),
                        (x+radius*math.cos(b),y+radius*math.sin(b))))
            return triangles
        for coords, color, radius in zip(groups,
                ((.89,.95,1.,1.),(1.,.79,.44,1.),(1.,1.,1.,1.)),
                (3.2,4.5,5.2)):
            if not coords:
                continue
            outline = batch_for_shader(shader,'TRIS',{'pos':circles(coords,radius+1.1)})
            shade = batch_for_shader(shader,'TRIS',{'pos':circles(coords,radius)})
            shader.bind()
            shader.uniform_float('color',(.08,.12,.18,1.0))
            outline.draw(shader)
            shader.uniform_float('color',color)
            shade.draw(shader)
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
        obj=bpy.context.edit_object
        if is_guide(obj): _sync_ui(obj)
        changed=False
        for guide in scene.objects:
            if is_guide(guide) and guide.nh35.live:
                changed=_rebuild(guide) or changed
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
def _on_load(_dummy):
    _clear_zone_pick()
    global _CUT_PREVIEW
    _CACHE.clear()
    _CUT_PREVIEW=None


@persistent
def _on_undo_pre(_dummy):
    _clear_zone_pick()
    global _UNDOING,_CUT_PREVIEW
    _UNDOING=True
    _CUT_PREVIEW=None
    _CACHE.clear()


@persistent
def _on_undo_post(_dummy):
    global _UNDOING
    _CACHE.clear()
    _UNDOING=False


_CLASSES=(NHGuidedProperties,NHGuidedUISettings,NYANW35_OT_direction_smoothing,NYANW35_OT_local_density,
          NYANW35_OT_create_guide,NYANW35_OT_activate,NYANW35_OT_disable,
          NYANW35_OT_demo,NYANW35_OT_rebuild,NYANW35_OT_bake,
          NYANW35_OT_pin_rails, NYANW35_OT_adjust,NYANW35_OT_pick_zone,
          NYANW35_OT_hair_loop,NYANW35_PT_main)


def _register_keys():
    kc=bpy.context.window_manager.keyconfigs.addon
    if kc is None: return
    km=kc.keymaps.new(name='Mesh',space_type='EMPTY')
    click=km.keymap_items.new('nyanw35.pick_zone','LEFTMOUSE','PRESS',any=True)
    _KEYMAPS.append((km,click))
    # Add-on Mesh keymap takes precedence over Blender's default Mesh Ctrl+R
    # only when poll() accepts a NyanwHair guide. Shift+R is untouched.
    kmi=km.keymap_items.new('nyanw35.hair_loop','R','PRESS',ctrl=True)
    kmi.properties.mode='AUTO'
    _KEYMAPS.append((km,kmi))
    for field,mod in (('width','shift'),('bulge','ctrl')):
        for button,sign in (('WHEELUPMOUSE',1),('WHEELDOWNMOUSE',-1)):
            modifiers={mod:True}
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
    for handler_name,handler in (('load_post',_on_load),
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
    for handler_name,handler in (('load_post',_on_load),
                                 ('undo_pre',_on_undo_pre),
                                 ('undo_post',_on_undo_post),
                                 ('redo_pre',_on_undo_pre),
                                 ('redo_post',_on_undo_post)):
        handlers=getattr(bpy.app.handlers,handler_name)
        if handler in handlers: handlers.remove(handler)
    del bpy.types.WindowManager.nh35_ui
    del bpy.types.Object.nh35
    for cls in reversed(_CLASSES): bpy.utils.unregister_class(cls)
    _CACHE.clear()
