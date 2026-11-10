"""Explicit, transactional boundary, merge and extrusion tools."""
from .i18n import tr, ui as translated_layout
import bpy,bmesh
from bpy.types import Operator,Menu
from bpy.props import EnumProperty,FloatProperty,FloatVectorProperty,IntProperty
from mathutils import Vector
from bpy_extras import view3d_utils
from . import addon as a


def boundary_vertices(face,bm):
    layer=bm.loops.layers.int[a.L_BOUNDARY]
    return tuple(frozenset(loop.vert for loop in face.loops if int(loop[layer])&bit) for bit in (1,2))


def assign_vertices(face,bm,slots):
    bm.verts.index_update()
    loops=list(face.loops);caps=[]
    for vertices in slots:
        marked=[i for i,l in enumerate(loops) if l.vert in vertices]
        if not marked:caps.append(None)
        elif len(marked)==1:caps.append(('point',marked[0]))
        elif len(marked)==2:
            index=next((i for i in marked if (i+1)%len(loops) in marked),None)
            if index is None:raise ValueError(tr('Boundary is no longer continuous after this operation'))
            caps.append(('edge',index))
        else:raise ValueError(tr('This operation requires a boundary with several edges'))
    if all(c is not None for c in caps) and not a.core.cap_paths(tuple(v.index for v in face.verts),*caps):
        raise ValueError(tr('Two boundaries of one segment coincide or intersect'))
    a._write_boundary_pair(face,bm,caps)


def _select(bm,vertices):
    for face in bm.faces:face.select_set(False)
    for edge in bm.edges:edge.select_set(False)
    for vertex in bm.verts:vertex.select_set(False)
    bm.select_history.clear()
    for vertex in vertices:vertex.select_set(True);bm.select_history.add(vertex)
    for edge in bm.edges:
        if all(v in vertices for v in edge.verts):edge.select_set(True)


class NYANW35_OT_boundary(Operator):
    bl_idname='nyanw35.boundary'
    bl_label='Segment boundary'
    bl_options={'REGISTER','UNDO'}
    slot:EnumProperty(items=[('AUTO','Free boundary',''),('FIRST','Boundary 1',''),('SECOND','Boundary 2','')],default='AUTO')
    clear: bpy.props.BoolProperty(default=False,options={'SKIP_SAVE'})
    @classmethod
    def poll(cls,context):return bool(a.is_guide(context.edit_object))
    @classmethod
    def description(cls,context,properties):
        return (tr('Detect this boundary automatically while preserving the opposite boundary and surface') if properties.clear else tr('Assign a selected point or edge as the segment boundary'))
    def execute(self,context):
        obj=context.edit_object;live=bmesh.from_edit_mesh(obj.data);live.faces.index_update()
        face=a._zone_ui_primary(live,obj) or a._active_face(live,obj)
        if face is None:return {'CANCELLED'}
        zone=face.index
        def operation(bm):
            a._prepare_bmesh(bm);bm.faces.ensure_lookup_table();bm.verts.index_update();face=bm.faces[zone]
            slots=list(boundary_vertices(face,bm));selected=frozenset(v for v in face.verts if v.select)
            if self.slot=='AUTO':
                overlap=[i for i,s in enumerate(slots) if s and s&selected]
                empty=[i for i,s in enumerate(slots) if not s]
                if len(overlap)==1:index=overlap[0]
                elif len(empty)==1:index=empty[0]
                elif len(empty)==2:index=0
                else:raise ValueError(tr('Both boundaries are set: choose Boundary 1 or Boundary 2 to replace one'))
            else:index=0 if self.slot=='FIRST' else 1
            if self.clear:
                raw=list(a._read_caps(face,a._boundary_layer(bm)) or (None,None));raw[index]=None
                pair=a.core.complete_caps([tuple(v.co) for v in bm.verts],tuple(v.index for v in face.verts),raw)
                a._write_boundary_pair(face,bm,pair);return
            else:
                cap=a._selected_cap(face)
                i=cap[1];loops=list(face.loops)
                slots[index]=frozenset((loops[i].vert,)) if cap[0]=='point' else frozenset((loops[i].vert,loops[(i+1)%len(loops)].vert))
            assign_vertices(face,bm,slots)
        try:a._edit_transaction(obj,operation)
        except (ValueError,RuntimeError,ReferenceError) as exc:self.report({'WARNING'},str(exc));return {'CANCELLED'}
        a._rebuild(obj,True);return {'FINISHED'}


def _mapped(records,mapping,maximum=False):
    result={};aliases={}
    for (one,two),value in records.items():
        key=(mapping.get(one,one),mapping.get(two,two))
        if key[0] is key[1]:continue
        if maximum:
            key=aliases.setdefault(frozenset(key),key);result[key]=max(result.get(key,value),value)
        else:result.setdefault(key,value)
    return result


def merge_stage(bm,mode,distance,cursor):
    a._prepare_bmesh(bm);bm.verts.index_update();bm.faces.index_update()
    selected=[v for v in bm.verts if v.select]
    if len(selected)<2:raise ValueError(tr('Select at least two points'))
    history=[v for v in bm.select_history if isinstance(v,bmesh.types.BMVert) and v.select]
    if mode=='DISTANCE':mapping=bmesh.ops.find_doubles(bm,verts=selected,dist=distance)['targetmap']
    else:
        if mode in ('ACTIVE','FIRST','LAST') and not history:raise ValueError(tr('Select the desired active point last'))
        target=(history[0] if mode=='FIRST' and history else history[-1] if history else selected[0])
        if mode=='CENTER':position=sum((v.co for v in selected),Vector())/len(selected)
        elif mode=='CURSOR':position=Vector(cursor)
        else:position=target.co.copy()
        mapping={v:target for v in selected if v is not target}
    mapping={one:two for one,two in mapping.items() if one is not two}
    for one in tuple(mapping):
        two=mapping[one];seen={one}
        while two in mapping and two not in seen:seen.add(two);two=mapping[two]
        mapping[one]=two
    if not mapping:return False
    selected_after={mapping.get(v,v) for v in selected}
    order=bm.faces.layers.int[a.L_ORDER]
    saved={int(face[order]):tuple(frozenset(mapping.get(v,v) for v in slot) for slot in boundary_vertices(face,bm)) for face in bm.faces}
    if any(slots[0] and slots[1] and slots[0].intersection(slots[1]) for slots in saved.values()):raise ValueError(tr('Cannot merge both boundaries of one segment into one point'))
    directions=_mapped(a._directional_smoothing(bm,True),mapping)
    density=_mapped(a._edge_density(bm,True),mapping,True)
    layout=_mapped(a._layout_lengths(bm,True),mapping,True)
    survivors=set(mapping.values())
    point_gain=bm.verts.layers.float[a.L_POINT_DENSITY];point_radius=bm.verts.layers.float[a.L_POINT_RADIUS]
    values={target:tuple(max((float(v[layer]) for v in selected if mapping.get(v,v) is target),default=0.)
                         for layer in (point_gain,point_radius)) for target in survivors}
    if mode!='DISTANCE':next(iter(survivors)).co=position
    bmesh.ops.weld_verts(bm,targetmap=mapping)
    bm.verts.index_update();bm.faces.index_update()
    for face in bm.faces:
        slots=saved.get(int(face[order]))
        if slots is not None:assign_vertices(face,bm,slots)
    a._restore_directions(bm,directions);a._restore_density(bm,density);a._restore_layout(bm,layout)
    for target,(gain,radius) in values.items():target[point_gain]=gain;target[point_radius]=radius
    if any(len(e.link_faces)>2 for e in bm.edges):raise ValueError(tr('Merge would join three surfaces on one edge'))
    _select(bm,list(selected_after))


class NYANW35_OT_merge(Operator):
    bl_idname='nyanw35.merge'
    bl_label='Merge'
    bl_description='Merge selected vertices while preserving NyanwHair boundaries and settings'
    bl_options={'REGISTER','UNDO'}
    mode:EnumProperty(items=[('CENTER','At Center',''),('ACTIVE','At Active',''),('FIRST','At First',''),('LAST','At Last',''),('CURSOR','At Cursor',''),('DISTANCE','By Distance','')],default='CENTER')
    distance:FloatProperty(name='Distance',default=.001,min=0.,subtype='DISTANCE')
    @classmethod
    def poll(cls,context):return bool(a.is_guide(context.edit_object))
    def invoke(self,context,event):
        if sum(v.select and not v.hide for v in bmesh.from_edit_mesh(context.edit_object.data).verts)<2:return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self) if self.mode=='DISTANCE' else self.execute(context)
    def execute(self,context):
        obj=context.edit_object;live=bmesh.from_edit_mesh(obj.data);selected=[v for v in live.verts if v.select and not v.hide]
        if len(selected)<2:return {'CANCELLED'}
        if self.mode=='DISTANCE' and not bmesh.ops.find_doubles(live,verts=selected,dist=self.distance)['targetmap']:return {'CANCELLED'}
        cursor=obj.matrix_world.inverted_safe()@context.scene.cursor.location
        try:a._edit_transaction(obj,lambda bm:merge_stage(bm,self.mode,self.distance,cursor))
        except (ValueError,RuntimeError,ReferenceError) as exc:self.report({'WARNING'},str(exc));return {'CANCELLED'}
        a._prepare_object(obj);a._rebuild(obj,True);return {'FINISHED'}


class NYANW35_MT_merge(Menu):
    bl_label='Merge'
    def draw(self,context):
        translated_layout(self.layout).operator_context='INVOKE_DEFAULT'
        bm=bmesh.from_edit_mesh(context.edit_object.data)
        has_history=any(isinstance(v,bmesh.types.BMVert) and v.select for v in bm.select_history)
        for mode,label in [('CENTER','At Center'),('ACTIVE','At Active'),('FIRST','At First'),('LAST','At Last'),('CURSOR','At Cursor'),('DISTANCE','By Distance')]:
            row=translated_layout(self.layout).row();row.enabled=mode not in ('ACTIVE','FIRST','LAST') or has_history
            op=row.operator('nyanw35.merge',text=label);op.mode=mode


class NYANW35_OT_merge_menu(Operator):
    bl_idname='nyanw35.merge_menu';bl_label='Merge'
    @classmethod
    def poll(cls,context):return bool(a.is_guide(context.edit_object))
    def invoke(self,context,event):
        if not any(v.select and not v.hide for v in bmesh.from_edit_mesh(context.edit_object.data).verts):return {'CANCELLED'}
        bpy.ops.wm.call_menu(name='NYANW35_MT_merge');return {'FINISHED'}


def single_extrude_stage(bm,result,offset):
    a._prepare_bmesh(bm,result);bm.verts.index_update();bm.faces.index_update()
    selected=[v for v in bm.verts if v.select]
    if len(selected) not in (1,2):
        raise ValueError(tr('Select a free boundary, branch edge or face for E-Face'))
    directions=a._directional_smoothing(bm,True);density=a._edge_density(bm,True);layout=a._layout_lengths(bm,True)
    edge=bm.edges.get(tuple(selected)) if len(selected)==2 else None
    if len(selected)==2 and edge is None:raise ValueError(tr('Select one edge'))
    owners=list(edge.link_faces) if edge else list(selected[0].link_faces)
    if not owners:raise ValueError(tr('Select a boundary edge of an existing segment to branch'))
    if len(owners)!=1:raise ValueError(tr('Edge already joins two surfaces. Select a free boundary edge'))
    face=owners[0]
    caps=a._read_caps(face,a._boundary_layer(bm))
    if not caps or any(c is None or c[0] not in ('edge','point') for c in caps):raise ValueError(tr('Set both segment boundaries first'))
    slots=list(boundary_vertices(face,bm));chosen=frozenset(selected)
    cap_index=next((i for i,s in enumerate(slots) if s==chosen),None)
    if edge is None and cap_index is None:raise ValueError(tr('Point must be a marked free boundary'))
    ring=list(face.verts);old_edges=tuple(face.edges)
    attributes=[(layer,face[layer]) for layers in (bm.faces.layers.int,bm.faces.layers.float,bm.faces.layers.string) for layer in layers.values()]
    material=face.material_index;smooth=face.smooth
    def face_values(new):
        for layer,value in attributes:new[layer]=value
        new.material_index=material;new.smooth=smooth
    origin=sum((v.co for v in selected),Vector())/len(selected)
    default_direction=Vector((0,0,1))
    if cap_index is not None:
        if edge:
            i=next(i for i,v in enumerate(ring) if v in chosen and ring[(i+1)%len(ring)] in chosen)
            first=ring[i];second=ring[(i+1)%len(ring)]
            default_direction=origin-(ring[i-1].co+ring[(i+2)%len(ring)].co)*.5
            one=bm.verts.new(first.co+offset);two=bm.verts.new(second.co+offset)
            polygon=ring[:i+1]+[one,two]+ring[i+1:]
        else:
            first=selected[0];i=ring.index(first);old=first.co.copy()
            default_direction=old-(ring[i-1].co+ring[(i+1)%len(ring)].co)*.5
            across=(ring[(i+1)%len(ring)].co-ring[i-1].co)*.15
            if across.length<1e-8:raise ValueError(tr('Could not open the pointed tip'))
            first.co=old-across;second=bm.verts.new(old+across)
            neighbor=ring[(i+1)%len(ring)]
            def move_corner(records):
                return {((second if u is first and v is neighbor else u),(second if v is first and u is neighbor else v)):value for (u,v),value in records.items()}
            directions=move_corner(directions);density=move_corner(density);layout=move_corner(layout)
            for name in (a.L_POINT_DENSITY,a.L_POINT_RADIUS):
                layer=bm.verts.layers.float[name];second[layer]=first[layer]
            one=bm.verts.new(first.co+offset);two=bm.verts.new(second.co+offset)
            polygon=ring[:i]+[first,one,two,second]+ring[i+1:]
        bm.faces.remove(face)
        new=bm.faces.new(polygon);face_values(new)
        slots[cap_index]=frozenset((one,two));assign_vertices(new,bm,slots)
        for old_edge in old_edges:
            if old_edge.is_valid and not old_edge.link_faces:bm.edges.remove(old_edge)
        mode='EXTEND'
    else:
        first,second=next((l.vert,l.link_loop_next.vert) for l in face.loops if l.edge is edge)
        default_direction=(second.co-first.co).cross(face.normal)
        one=bm.verts.new(first.co+offset);two=bm.verts.new(second.co+offset)
        new=bm.faces.new((second,first,one,two));face_values(new)
        new[bm.faces.layers.int[a.L_ORDER]]=max(int(f[bm.faces.layers.int[a.L_ORDER]]) for f in bm.faces)+1
        key=a.core.edge_key(first.index,second.index)
        count=sum(k[0]=='s' and k[1:3]==key for k in result.vertex_keys)+1
        a._set_zone(new,bm,'width',max(1,min(32,count)))
        assign_vertices(new,bm,(frozenset((first,second)),frozenset((one,two))))
        directions[first,one]=0.;directions[second,two]=0.;mode='BRANCH'
    for v in (one,two):
        v[bm.verts.layers.float[a.L_POINT_DENSITY]]=0.;v[bm.verts.layers.float[a.L_POINT_RADIUS]]=0.
    a._restore_directions(bm,directions);a._restore_density(bm,density);a._restore_layout(bm,layout)
    _select(bm,[one,two]);bm.normal_update();bm.verts.index_update();bm.faces.index_update()
    if offset.length>1e-8 and a.core.length(a.core.newell([tuple(v.co) for v in bm.verts],tuple(v.index for v in new.verts)))<a.core.EPS:raise ValueError(tr('Choose a direction that creates a nonzero surface'))
    return dict(mode=mode,indices=(one.index,two.index),bases=(tuple(one.co-offset),tuple(two.co-offset)),anchor=tuple(origin),direction=tuple(default_direction.normalized() if default_direction.length else Vector((0,0,1))))


def extrude_stage(bm,result,offset):
    from .extrusion import extrude
    return extrude(bm,result,offset)


def restore_backup(obj,backup,selection):
    mesh=bpy.data.meshes.new('.NyanwHair_Cancel')
    try:
        backup.to_mesh(mesh);live=bmesh.from_edit_mesh(obj.data);live.clear();live.from_mesh(mesh);a._restore_selection(live,selection)
        bmesh.update_edit_mesh(obj.data,loop_triangles=True,destructive=True)
    finally:bpy.data.meshes.remove(mesh)


def validate_stage(bm,obj):
    bm.verts.index_update();bm.faces.index_update()
    from . import groups
    faces=[f for f in bm.faces if groups.face_group(f,bm,groups.active_id(obj))==groups.active_id(obj)]
    result=a.core.generate([tuple(v.co) for v in bm.verts],[tuple(v.index for v in f.verts) for f in faces],
        [a._get_zone(f,bm) for f in faces],rows=obj.nh35.rows,caps=[a.core.neutral_caps(tuple(v.index for v in f.verts),a._read_caps(f,a._boundary_layer(bm))) for f in faces],
        reference_length=(groups.settings(obj).reference_length if groups.settings(obj) else obj.nh35.detail_length),directional_smoothing=a._directional_smoothing(bm),local_density=a._edge_density(bm),
        point_density=a._point_density(bm),layout_lengths=a._layout_lengths(bm),regular_rows=True,face_indices=[f.index for f in faces])
    if result.warnings:raise ValueError(next(iter(result.warnings.values())))
    if not result.faces:raise ValueError(tr('Could not build the surface'))


_ACTIVE=set()
class NYANW35_OT_extrude(Operator):
    bl_idname='nyanw35.extrude'
    bl_label='Smart strand extrusion'
    bl_options={'REGISTER','UNDO','BLOCKING'}
    offset:FloatVectorProperty(name='Offset',subtype='TRANSLATION',default=(0.,0.,.25))
    curve_points:IntProperty(name='Intermediate points',default=0,min=0,max=64)
    curve_bend:FloatVectorProperty(name='Bend',subtype='TRANSLATION',default=(0.,0.,0.))
    @classmethod
    def poll(cls,context):return bool(a.is_guide(context.edit_object))
    def execute(self,context):
        obj=context.edit_object
        if not any(v.select and not v.hide for v in bmesh.from_edit_mesh(obj.data).verts):return {'CANCELLED'}
        if Vector(self.offset).length<1e-8:return {'CANCELLED'}
        a._rebuild(obj);backup=bmesh.from_edit_mesh(obj.data).copy();selection=a._selection_state(bmesh.from_edit_mesh(obj.data));committed=False
        def operation(bm):
            data=extrude_stage(bm,a._CACHE[obj.as_pointer()]['result'],obj.matrix_world.inverted_safe().to_3x3()@Vector(self.offset))
            if data['mode']=='WIRE' and self.curve_points:
                from . import wirecurve
                start=obj.matrix_world@Vector(data['anchor']);coords=wirecurve.positions(start,start+Vector(self.offset),self.curve_bend,self.curve_points)
                wirecurve.rebuild(bm,data['wire_start'],data['indices'][0],(),[obj.matrix_world.inverted_safe()@point for point in coords])
            if data['mode']!='WIRE':validate_stage(bm,obj)
            return data
        try:
            data=a._edit_transaction(obj,operation);committed=True
            a._prepare_object(obj);a._rebuild(obj,True)
            if data['mode']!='WIRE' and a._CACHE[obj.as_pointer()]['error_count']:raise ValueError(tr('Could not build the surface'))
        except (ValueError,RuntimeError,ReferenceError) as exc:
            if committed:restore_backup(obj,backup,selection);a._CACHE.pop(obj.as_pointer(),None);a._rebuild(obj,True)
            self.report({'WARNING'},str(exc));return {'CANCELLED'}
        finally:backup.free()
        return {'FINISHED'}
    def invoke(self,context,event):
        if context.region_data is None or context.region is None:return {'CANCELLED'}
        obj=context.edit_object
        if not any(v.select and not v.hide for v in bmesh.from_edit_mesh(obj.data).verts):return {'CANCELLED'}
        self.curve_points=0;self.curve_bend=(0.,0.,0.);self._curve_phase=False;self._curve_inside=();self._curve_handle=None;self._curve_preview=()
        self._obj=obj;self._backup=bmesh.from_edit_mesh(obj.data).copy();self._selection=a._selection_state(bmesh.from_edit_mesh(obj.data))
        self._mouse=Vector((event.mouse_region_x,event.mouse_region_y));self._axis=None;self._number='';self._delta=Vector();self._precision=False;self._motion_offset=Vector();self._raw=Vector()
        a._rebuild(obj)
        self._zone_state={key:a._CACHE[obj.as_pointer()][key] for key in ('active','zone_selection','zone_selection_explicit') if key in a._CACHE[obj.as_pointer()]}
        try:self._data=a._edit_transaction(obj,lambda bm:extrude_stage(bm,a._CACHE[obj.as_pointer()]['result'],Vector()))
        except (ValueError,RuntimeError,ReferenceError) as exc:
            self._backup.free();self._backup=None;self.report({'WARNING'},str(exc));return {'CANCELLED'}
        self._anchor=obj.matrix_world@Vector(self._data['anchor'])
        self._default_direction=(obj.matrix_world.to_3x3()@Vector(self._data['direction'])).normalized()
        self._start=view3d_utils.region_2d_to_location_3d(context.region,context.region_data,self._mouse,self._anchor)
        _ACTIVE.add(self);context.window_manager.modal_handler_add(self);self._status(context)
        return {'RUNNING_MODAL'}
    def _status(self,context):
        if getattr(self,'_curve_phase',False):
            context.area.header_text_set(tr('Bend · intermediate points: %d · mouse: bend · wheel: points · LMB / Enter · Esc')%self.curve_points);return
        label={'EXTEND':tr('Extend'),'BRANCH':tr('Branch'),'VOLUME':'E-Face','WIRE':tr('Guide wire')}.get(self._data['mode'],tr('Extrude'))
        if self._data.get('count',1)>1:label+=' ×%d'%self._data['count']
        extra=tr(' · wheel: bend') if self._data['mode']=='WIRE' else ''
        context.area.header_text_set(label+tr(' · mouse / X Y Z / number')+extra+tr(' · LMB / Enter · Esc'))
    def _move(self,context,event):
        mouse=Vector((event.mouse_region_x,event.mouse_region_y));delta=view3d_utils.region_2d_to_location_3d(context.region,context.region_data,mouse,self._anchor)-self._start
        if self._axis:
            axis=Vector(self._axis);base=view3d_utils.location_3d_to_region_2d(context.region,context.region_data,self._anchor)
            end=view3d_utils.location_3d_to_region_2d(context.region,context.region_data,self._anchor+axis)
            projected=end-base if end is not None and base is not None else Vector((0.,0.))
            value=(mouse-self._mouse).dot(projected)/projected.length_squared if projected.length_squared>1e-8 else (mouse.y-self._mouse.y)*context.region_data.view_distance/context.region.height
            delta=axis*value
        raw=delta.copy()
        if event.shift!=self._precision:
            if event.shift:self._precision_raw=raw.copy();self._precision_base=self._delta.copy()
            else:self._motion_offset=self._delta-raw
            self._precision=event.shift
        delta=self._precision_base+(raw-self._precision_raw)*.1 if self._precision else raw+self._motion_offset
        if self._number not in ('','-','.'):

            try:
                value=float(self._number);axis=Vector(self._axis) if self._axis else (delta.normalized() if delta.length else self._default_direction)
                delta=axis*value
            except ValueError:pass
        self._delta=delta;local=self._obj.matrix_world.inverted_safe().to_3x3()@delta
        bm=bmesh.from_edit_mesh(self._obj.data);bm.verts.ensure_lookup_table()
        for index,base in zip(self._data['indices'],self._data['bases']):bm.verts[index].co=Vector(base)+local
        if self._data.get('opening'):
            from . import extrusion
            extrusion.update_opening(bm,self._data['opening'],local)
        bmesh.update_edit_mesh(self._obj.data,loop_triangles=False,destructive=False)
        a._rebuild(self._obj);context.area.tag_redraw()
    def _curve_update(self,context,event=None,rebuild=False):
        from . import wirecurve
        if event is not None:
            mouse=Vector((event.mouse_region_x,event.mouse_region_y))
            raw=view3d_utils.region_2d_to_location_3d(context.region,context.region_data,mouse,self._curve_middle)-self._curve_mouse_start
            chord=self._curve_end-self._anchor
            raw-=chord*(raw.dot(chord)/chord.length_squared)
            if event.shift!=self._curve_precision:
                if event.shift:self._curve_precision_raw=raw.copy();self._curve_precision_base=self._curve_bend.copy()
                else:self._curve_motion_offset=self._curve_bend-raw
                self._curve_precision=event.shift
            self._curve_bend=self._curve_precision_base+(raw-self._curve_precision_raw)*.1 if self._curve_precision else raw+self._curve_motion_offset
        points=wirecurve.positions(self._anchor,self._curve_end,self._curve_bend,self.curve_points)
        inverse=self._obj.matrix_world.inverted_safe();bm=bmesh.from_edit_mesh(self._obj.data);bm.verts.ensure_lookup_table()
        local=[inverse@point for point in points]
        if rebuild:self._curve_inside=wirecurve.rebuild(bm,self._data['wire_start'],self._data['indices'][0],self._curve_inside,local)
        else:
            for index,point in zip(self._curve_inside,local[1:-1]):bm.verts[index].co=point
            bm.verts[self._data['indices'][0]].co=local[-1]
        self.curve_bend=tuple(self._curve_bend)
        self._curve_preview=tuple(tuple(p) for p in wirecurve.positions(self._anchor,self._curve_end,self._curve_bend,47))
        bmesh.update_edit_mesh(self._obj.data,loop_triangles=False,destructive=rebuild)
        a._rebuild(self._obj);self._status(context);context.area.tag_redraw()

    def _curve_wheel(self,context,event):
        from . import wirecurve
        if not self._curve_phase:
            if self._delta.length<1e-8:return
            self._curve_phase=True;self._curve_end=self._anchor+self._delta;self._curve_middle=(self._anchor+self._curve_end)*.5
            mouse=Vector((event.mouse_region_x,event.mouse_region_y))
            self._curve_mouse_start=view3d_utils.region_2d_to_location_3d(context.region,context.region_data,mouse,self._curve_middle)
            self._curve_bend=Vector();self._curve_precision=False;self._curve_motion_offset=Vector();self.curve_points=1
            self._curve_handle=bpy.types.SpaceView3D.draw_handler_add(self._curve_draw,(),'WINDOW','POST_VIEW')
        else:self.curve_points=max(1,min(wirecurve.MAX_POINTS,self.curve_points+(1 if event.type=='WHEELUPMOUSE' else -1)))
        self._curve_update(context,rebuild=True)

    def _curve_draw(self):
        ctx=bpy.context
        if ctx.edit_object!=self._obj or not a._helpers_visible(ctx) or self._obj.nh35.transparency>=1. or not self._curve_preview:return
        gpu=a.gpu
        if gpu is None:return
        depth=gpu.state.depth_test_get();mask=gpu.state.depth_mask_get();blend=gpu.state.blend_get();width=gpu.state.line_width_get()
        try:
            from . import palette
            style=palette.get(self._obj.nh35)
            from . import strokes
            scale,limit=strokes.view_style(a._CACHE.get(self._obj.as_pointer(),{}),self._obj,ctx)
            shader=a._stroke_shader(style.selected_width*scale,max_clearance=limit);shader.bind();shader.uniform_float('color',palette.preview_color(self._obj.nh35,style.selected))
            gpu.state.blend_set('ALPHA');gpu.state.depth_test_set('NONE' if a._xray_enabled(ctx) else 'LESS_EQUAL');gpu.state.depth_mask_set(False)
            a._stroke_batch(shader,'LINE_STRIP',{'pos':self._curve_preview}).draw(shader)
        except (ReferenceError,RuntimeError,ValueError):pass
        finally:gpu.state.depth_test_set(depth);gpu.state.depth_mask_set(mask);gpu.state.line_width_set(width);gpu.state.blend_set(blend)

    def _end(self,context,restore):
        try:
            handle=getattr(self,'_curve_handle',None)
            if handle is not None:bpy.types.SpaceView3D.draw_handler_remove(handle,'WINDOW');self._curve_handle=None
            if restore and self._obj.mode=='EDIT':
                restore_backup(self._obj,self._backup,self._selection)
            a._CACHE.pop(self._obj.as_pointer(),None);a._rebuild(self._obj,True)
            if restore and self._obj.as_pointer() in a._CACHE:
                a._CACHE[self._obj.as_pointer()].update(getattr(self,'_zone_state',{}));a._sync_ui(self._obj)
        except ReferenceError:pass
        finally:
            if self._backup is not None:self._backup.free();self._backup=None
            _ACTIVE.discard(self)
            if context.area:context.area.header_text_set(None);context.area.tag_redraw()
    def modal(self,context,event):
        try:return self._modal(context,event)
        except (ValueError,RuntimeError,ReferenceError) as exc:
            self.report({'WARNING'},str(exc));self._end(context,True);return {'CANCELLED'}
    def _modal(self,context,event):
        if getattr(self,'_backup',None) is None:return {'CANCELLED'}
        if event.ctrl and event.type=='Z' and event.value=='PRESS':self._end(context,True);return {'CANCELLED'}
        if event.type in ('ESC','RIGHTMOUSE') and event.value=='PRESS':self._end(context,True);return {'CANCELLED'}
        if event.type in ('WHEELUPMOUSE','WHEELDOWNMOUSE') and event.value=='PRESS' and self._data['mode']=='WIRE':
            self._curve_wheel(context,event);return {'RUNNING_MODAL'}
        if event.type in ('RET','NUMPAD_ENTER','LEFTMOUSE') and event.value=='PRESS':
            if self._delta.length<1e-8:self._end(context,True);return {'CANCELLED'}
            try:
                a._prepare_object(self._obj);a._rebuild(self._obj,True)
                if self._data['mode']!='WIRE' and a._CACHE[self._obj.as_pointer()]['error_count']:raise ValueError(tr('Extrusion creates an invalid surface'))
            except (ValueError,RuntimeError,ReferenceError) as exc:self.report({'WARNING'},str(exc));self._end(context,True);return {'CANCELLED'}
            self.offset=self._delta;self._end(context,False);return {'FINISHED'}
        if self._curve_phase:
            if event.type=='MOUSEMOVE':self._curve_update(context,event)
            return {'RUNNING_MODAL'}
        if event.value=='PRESS' and event.type in ('X','Y','Z'):
            axis={'X':(1.,0.,0.),'Y':(0.,1.,0.),'Z':(0.,0.,1.)}[event.type];self._axis=None if self._axis==axis else axis;self._motion_offset=Vector();self._precision=False
            if self._axis:self._delta=Vector(self._axis)*self._delta.dot(Vector(self._axis))
        elif event.value=='PRESS' and event.type=='BACK_SPACE':self._number=self._number[:-1]
        elif event.value=='PRESS' and event.unicode and event.unicode in '0123456789.,-':
            char=event.unicode.replace(',','.')
            if char=='-' and self._number or char=='.' and '.' in self._number:return {'RUNNING_MODAL'}
            self._number+=char
        elif event.type!='MOUSEMOVE':return {'RUNNING_MODAL'}
        self._move(context,event);return {'RUNNING_MODAL'}
    def cancel(self,context):
        if getattr(self,'_backup',None) is not None:self._end(context,True)


def cancel_all(restore=True):
    for operator in tuple(_ACTIVE):operator._end(bpy.context,restore)


CLASSES=(NYANW35_OT_boundary,NYANW35_OT_merge,NYANW35_MT_merge,NYANW35_OT_merge_menu,NYANW35_OT_extrude)
