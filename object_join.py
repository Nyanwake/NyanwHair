"""Undoable object join with semantic group remapping before Blender's join."""
import bpy,bmesh,re
from bpy.types import Operator
from . import addon as a,groups
from .i18n import tr


def eligible(context):
    return context.mode=='OBJECT' and a.is_guide(context.object) and len(context.selected_editable_objects)>1 and all(a.is_guide(o) for o in context.selected_editable_objects)


def combine(states):
    records=[];names=set();maps=[]
    for active,source in states:
        remap={}
        for record in source:
            name=record[1]
            if name in names:
                match=re.fullmatch(r'(.*)\.(\d+)',name)
                root,index=(match[1],int(match[2])+1) if match else (name,1)
                while root+'.'+str(index) in names:index+=1
                name=root+'.'+str(index)
            item=list(record);item[0]=len(records)+1;item[1]=name
            names.add(name);records.append(item);remap[record[0]]=item[0]
        maps.append(remap)
    return [maps[0].get(states[0][0],1),records],maps


def materialized_groups(objects,states):
    from mathutils import Matrix
    output=[];inverse=objects[0].matrix_world.inverted_safe()
    for obj,(_,records) in zip(objects,states):
        transform=inverse@obj.matrix_world;changed=set()
        for record in records:
            for axis,on in enumerate(record[3]):
                if not on:continue
                reflection=Matrix.Identity(4);reflection[axis][axis]=-1.
                delta=reflection@transform-transform@reflection
                if max(abs(v) for row in delta for v in row)>1e-7:changed.add(record[0])
        output.append(changed)
    return output


def assign(obj,state):
    restoring=groups._RESTORING;groups._RESTORING=True
    try:
        obj.nh35.groups.clear()
        for record in state[1]:
            group=obj.nh35.groups.add();group.uid=record[0];group.name=record[1]
            if record[2] is not None and record[5]:group.color=record[2]
            group.mirror_x,group.mirror_y,group.mirror_z=record[3];group.reference_length=record[4]
            group.mirror_merge=record[6];group.mirror_distance=record[7]
            if len(record)>8:group.visible=record[8]
        obj.nh35.active_group=state[0]
    finally:groups._RESTORING=restoring


class NYANW35_OT_join_objects(Operator):
    bl_idname='nyanw35.join_objects'
    bl_label='Join hair objects'
    bl_description='Join hair objects; duplicate group names receive numeric suffixes'
    bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):return context.mode=='OBJECT' and context.object is not None
    def invoke(self,context,event):
        return self.execute(context) if eligible(context) else {'PASS_THROUGH'}
    def execute(self,context):
        if not eligible(context):return {'CANCELLED'}
        active=context.object;objects=[active]+[o for o in context.selected_editable_objects if o is not active]
        if any(o.data.shape_keys for o in objects):
            self.report({'WARNING'},tr('Topology editing requires guides without Shape Keys'));return {'CANCELLED'}
        for obj in objects:groups.ensure(obj)
        states=[groups._state(obj) for obj in objects];state,mappings=combine(states)
        materialize=materialized_groups(objects,states)
        for changed,mapping in zip(materialize,mappings):
            for uid in changed:state[1][mapping[uid]-1][3]=[False,False,False]
        originals=[o.data for o in objects];pointers=[o.as_pointer() for o in objects];copies=[];order=1;joined=False
        try:
            for obj,mapping,changed in zip(objects,mappings,materialize):
                mesh=obj.data.copy();copies.append(mesh);bm=bmesh.new()
                try:
                    bm.from_mesh(mesh);a._prepare_bmesh(bm,group_id=groups.active_id(obj))
                    from . import mirror
                    previous=groups.active_id(obj);restoring=groups._RESTORING;groups._RESTORING=True
                    try:
                        for group in obj.nh35.groups:
                            if group.uid in changed and any(getattr(group,'mirror_'+axis) for axis in 'xyz'):
                                obj.nh35.active_group=group.uid;mirror.apply_guides(bm,obj)
                    finally:obj.nh35.active_group=previous;groups._RESTORING=restoring
                    face_group=bm.faces.layers.int[a.L_GROUP];vertex_group=bm.verts.layers.int[a.L_VERTEX_GROUP];identity=bm.faces.layers.int[a.L_ORDER]
                    for face in bm.faces:
                        face[face_group]=mapping.get(int(face[face_group]),state[0]);face[identity]=order;order+=1
                    for vertex in bm.verts:vertex[vertex_group]=mapping.get(int(vertex[vertex_group]),state[0])
                    for domain in (bm.faces,bm.verts):
                        for name in list(domain.layers.string.keys()):
                            if name.startswith(groups._JOURNAL):domain.layers.string.remove(domain.layers.string[name])
                    bm.to_mesh(mesh)
                finally:bm.free()
                obj.data=mesh
            if bpy.ops.object.join()!= {'FINISHED'}:raise RuntimeError(tr('Could not join hair objects'))
            joined=True;assign(active,state)
            for pointer in pointers:a._CACHE.pop(pointer,None);groups.CACHE.pop(pointer,None)
            a._prepare_object(active);a._rebuild(active,True)
            for area in context.screen.areas if context.screen else ():
                if area.type=='VIEW_3D':area.tag_redraw()
        except (ValueError,RuntimeError) as exc:
            if not joined:
                for obj,mesh in zip(objects,originals):obj.data=mesh
            self.report({'ERROR'},str(exc));return {'FINISHED'} if joined else {'CANCELLED'}
        finally:
            for mesh in copies:
                if mesh.users==0:bpy.data.meshes.remove(mesh)
        return {'FINISHED'}


def draw_join(self,context):
    if eligible(context):self.layout.operator('nyanw35.join_objects',text=tr('Join hair objects'))
