"""Viewport-only masking of native solid faces; the edit cage stays editable."""
NAME='.NyanwHair Preview Cage'


def update(obj):
    if obj.type!='MESH' or not obj.is_editable:return
    enabled=obj.nh35.enabled and obj.nh35.show_preview
    modifier=next((m for m in obj.modifiers if m.type=='MASK' and m.name==NAME and m.vertex_group==NAME),None)
    if enabled and modifier is None:
        group=obj.vertex_groups.get(NAME) or obj.vertex_groups.new(name=NAME)
        modifier=obj.modifiers.new(NAME,'MASK');modifier.vertex_group=group.name
        modifier.show_in_editmode=True;modifier.show_on_cage=False
        modifier.show_render=False;modifier.show_expanded=False
    if modifier:modifier.show_viewport=enabled


def changed(self,context):update(self.id_data)


def clear(obj):
    if not obj.is_editable:return
    modifier=next((m for m in obj.modifiers if m.type=='MASK' and m.name==NAME and m.vertex_group==NAME),None)
    if modifier:
        obj.modifiers.remove(modifier)
        group=obj.vertex_groups.get(NAME)
        if group:obj.vertex_groups.remove(group)


def initialize():
    import bpy
    from .addon import is_guide
    for obj in bpy.data.objects:
        if is_guide(obj):update(obj)


def clear_all():
    import bpy
    for obj in bpy.data.objects:
        if obj.type=='MESH':clear(obj)
