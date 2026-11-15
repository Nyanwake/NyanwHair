"""Explicit two-segment union; keep the outer guides and their attributes."""
import bmesh
from bpy.types import Operator
from . import addon as a, groups
from .i18n import tr


def _pair(bm):
    if 'FACE' in bm.select_mode:
        faces=[f for f in bm.faces if f.select and not f.hide]
        if len(faces)==2:return faces
    else:
        edges=[e for e in bm.edges if not e.hide and
               (e.select if 'EDGE' in bm.select_mode else all(v.select for v in e.verts))]
        pairs={frozenset(e.link_faces) for e in edges if len(e.link_faces)==2}
        if len(pairs)==1:return sorted(next(iter(pairs)),key=lambda f:f.index)
    raise ValueError(tr('Select two neighbouring segments or their shared edge'))


def join_stage(bm,obj):
    from .editing import boundary_vertices, assign_vertices, validate_stage
    a._prepare_bmesh(bm);bm.faces.index_update()
    faces=_pair(bm);primary=min(faces,key=lambda f:int(f[bm.faces.layers.int[a.L_ORDER]]))
    if any(f.hide or groups.face_group(f,bm,groups.active_id(obj))!=groups.active_id(obj) for f in faces):
        raise ValueError(tr('Join segments within the active hair group'))
    shared=set(faces[0].edges)&set(faces[1].edges)
    if not shared or any(len(e.link_faces)!=2 for e in shared):
        raise ValueError(tr('Segments must share one continuous edge chain'))
    # A simple outer cycle rejects holes, disconnected seams and pinched unions.
    outer=(set(faces[0].edges)|set(faces[1].edges))-shared
    adjacent={}
    for edge in outer:
        for vertex in edge.verts:adjacent.setdefault(vertex,[]).append(edge.other_vert(vertex))
    if any(len(neighbours)!=2 for neighbours in adjacent.values()):
        raise ValueError(tr('The joined contour must be a single closed loop'))
    start_loop=next((loop for loop in primary.loops if loop.edge in outer),None)
    if start_loop is None:raise ValueError(tr('The joined contour must be a single closed loop'))
    contour=[start_loop.vert];previous=start_loop.vert;current=start_loop.link_loop_next.vert
    while current is not contour[0]:
        if current in contour or len(contour)>=len(adjacent):
            raise ValueError(tr('The joined contour must be a single closed loop'))
        contour.append(current)
        following=next(v for v in adjacent[current] if v is not previous)
        previous,current=current,following
    if len(contour)!=len(adjacent):raise ValueError(tr('The joined contour must be a single closed loop'))
    slots={f:boundary_vertices(f,bm) for f in faces}
    seam=frozenset(v for edge in shared for v in edge.verts)
    # Joining end-to-end keeps the two far ends, including pointed ends.
    outer_slots=[]
    for face in (primary,next(f for f in faces if f is not primary)):
        caps=slots[face]
        hits=[i for i,cap in enumerate(caps) if cap==seam]
        if len(hits)==1:outer_slots.append(caps[1-hits[0]])
    if len(outer_slots)==2 and slots[primary][0]==seam:outer_slots.reverse()
    settings=[a._get_zone(f,bm) for f in faces];preferred=a._get_zone(primary,bm)
    smooth=a._directional_smoothing(bm,True)
    material=primary.material_index;smooth_face=primary.smooth
    order=min(s.order for s in settings)
    interior=seam-set(contour)
    for face in faces:bm.faces.remove(face)
    for edge in shared:bm.edges.remove(edge)
    for vertex in interior:
        if not vertex.link_edges:bm.verts.remove(vertex)
    face=bm.faces.new(contour);face.material_index=material;face.smooth=smooth_face
    face[bm.faces.layers.int[a.L_ORDER]]=order
    face[bm.faces.layers.int[a.L_GROUP]]=preferred.group
    for field in ('width','detail','bias'):
        values=[getattr(s,field) for s in settings]
        if field=='detail' and any(values):values=[v or obj.nh35.rows for v in values]
        value=sum(values)/2
        a._set_zone(face,bm,field,int(value+.5) if field in ('width','detail') else value)
    a._set_zone(face,bm,'flip',False)
    a._set_zone(face,bm,'normal_flip',preferred.normal_flip)
    a._set_zone(face,bm,'bulge',sum(s.signed_bulge for s in settings)/2*(-1 if preferred.normal_flip else 1))
    if len(outer_slots)==2:assign_vertices(face,bm,outer_slots)
    else:
        bm.verts.index_update()
        caps=a.core.auto_caps([tuple(v.co) for v in bm.verts],tuple(v.index for v in face.verts))
        a._write_boundary_pair(face,bm,caps)
    a._restore_directions(bm,smooth)
    for item in bm.faces:item.select_set(False)
    for edge in bm.edges:edge.select_set(False)
    for vertex in bm.verts:vertex.select_set(False)
    face.select_set(True);bm.faces.active=face
    bm.select_history.clear();bm.select_history.add(face)
    bm.normal_update();bm.faces.index_update();bm.verts.index_update()
    validate_stage(bm,obj)


class NYANW35_OT_join_segments(Operator):
    bl_idname='nyanw35.join_segments'
    bl_label='Join segments'
    bl_description='Remove the shared seam of two segments, keep their outer points and average segment settings'
    bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):return bool(a.is_guide(context.edit_object))
    def execute(self,context):
        obj=context.edit_object
        try:a._edit_transaction(obj,lambda bm:join_stage(bm,obj))
        except (ValueError,RuntimeError,ReferenceError) as exc:
            self.report({'WARNING'},str(exc));return {'CANCELLED'}
        a._rebuild(obj,True);a._sync_ui(obj)
        if context.area:context.area.tag_redraw()
        return {'FINISHED'}
