"""Reuse independent control components without changing the surface generator."""
from .i18n import tr
from collections import defaultdict
from . import core


class _Network:
    def __init__(self,parts,points,smoothing):
        self.points=points;self.paths=[];self.coefficients={};self.adjacency={};self.edges=set();self.smoothing=dict(smoothing or {})
        for part in parts:
            other=part.edge_evaluator
            self.paths.extend(other.paths);self.coefficients.update(other.coefficients);self.adjacency.update(other.adjacency);self.edges.update(other.edges)
    def __call__(self,a,b,t):
        from .curves import CurveNetwork
        return CurveNetwork.__call__(self,a,b,t)


def _freeze(value):
    return tuple(_freeze(x) for x in value) if isinstance(value,(list,tuple)) else value


def generate(points,boundaries,settings=None,rows=12,max_polygons=200000,previous=None,**kwargs):
    """Keep the group's physical reference, but solve detail per component.

    Shared control vertices define a component, including point junctions.
    Incomplete/ambiguous inputs use the original generator as a whole.
    """
    fallback=lambda:core.generate(points,boundaries,settings,rows,max_polygons,previous=previous,**kwargs)
    if any(len(loop)<3 or any(v<0 or v>=len(points) for v in loop) for loop in boundaries):return fallback()
    if len(boundaries)<2 or not kwargs.get('regular_rows') or kwargs.get('reference_length',0.)<=core.EPS or kwargs.get('edge_evaluator') is not None:return fallback()
    options=list(settings or [core.ZoneSettings() for _ in boundaries]);caps=kwargs.get('caps');indices=list(kwargs.get('face_indices') or range(len(boundaries)))
    if not caps or any(cap is None for cap in caps):return fallback()
    parent=list(range(len(boundaries)));vertex_owner={};edge_owners=defaultdict(int)
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    for i,loop in enumerate(boundaries):
        for v in loop:
            if v in vertex_owner:parent[find(i)]=find(vertex_owner[v])
            else:vertex_owner[v]=i
        for a,b in zip(loop,loop[1:]+loop[:1]):edge_owners[core.edge_key(a,b)]+=1
    groups=defaultdict(list)
    for i in range(len(boundaries)):groups[find(i)].append(i)
    if len(groups)<2 or any(n>2 for n in edge_owners.values()):return fallback()
    old=getattr(previous,'component_cache',{});current={};parts=[];reused=0
    for members in groups.values():
        used=sorted({v for i in members for v in boundaries[i]});used_set=set(used)
        local=dict(kwargs);local['caps']=[caps[i] for i in members];local['face_indices']=[indices[i] for i in members]
        for name in ('directional_smoothing','local_density','layout_lengths'):
            local[name]={e:value for e,value in (kwargs.get(name) or {}).items() if e[0] in used_set and e[1] in used_set}
        local['point_density']={v:value for v,value in (kwargs.get('point_density') or {}).items() if v in used_set}
        key=tuple((indices[i],tuple(boundaries[i])) for i in members)
        signature=(tuple(tuple(float(c) for c in points[v]) for v in used),tuple(options[i] for i in members),_freeze(local['caps']),int(rows),float(kwargs['reference_length']),max_polygons,
            tuple(tuple(sorted(local[name].items())) for name in ('directional_smoothing','local_density','point_density','layout_lengths')))
        saved=old.get(key)
        if saved and saved[0]==signature:part=saved[1];reused+=len(members)
        else:
            part=core.generate(points,[boundaries[i] for i in members],[options[i] for i in members],rows,max_polygons,previous=saved[1] if saved else None,_track_production=True,**local)
        if part.warnings:return fallback()
        current[key]=(signature,part);parts.append(part)
    # Merge in the original per-plan production order, even when components
    # are interleaved in the face list. Native Mesh/UV indices remain stable.
    out=core.MeshResult();out.source_points=tuple(tuple(float(c) for c in p) for p in points);out.regular_rows=bool(kwargs['regular_rows']);out.base_rows=max(2,min(256,int(rows)));out.reference_length=float(kwargs['reference_length']);out.target_spacing=min(part.target_spacing for part in parts)
    by_owner={owner:part for part in parts for owner in part.face_paths};all_plans=sorted((p for part in parts for p in part.plans_by_loop.values()),key=core._plan_priority)
    remaps={id(part):[-1]*len(part.vertices) for part in parts}
    for plan in all_plans:
        part=by_owner[plan.index];remap=remaps[id(part)]
        for v in part.production[plan.index]:
            if remap[v]>=0:raise ValueError(tr('Duplicate component index'))
            remap[v]=len(out.vertices);out.vertices.append(part.vertices[v]);out.vertex_keys.append(part.vertex_keys[v])
    if any(v<0 for remap in remaps.values() for v in remap):raise ValueError(tr('Incomplete component assembly order'))
    faces=defaultdict(list)
    for part in parts:
        remap=remaps[id(part)]
        for face,owner in zip(part.faces,part.owners):faces[owner].append(tuple(remap[v] for v in face))
        for name in ('warnings','face_paths','guide_curves','cut_lines','effective_widths','cap_widths','width_transitions','samples_per_edge','plans_by_loop','transitions','orientation_by_face','surface_sections','rail_samples','layout_lengths','row_cache','sampling_cache','correspondence_cache','patch_cache','final_sampling_cache','valid_cache'):
            getattr(out,name).update(getattr(part,name))
        out.surface_columns.update((owner,tuple(tuple(remap[v] for v in column) for column in columns)) for owner,columns in part.surface_columns.items())
        out.surface_grids.update((owner,tuple(tuple(remap[v] if v is not None else None for v in row) for row in grid)) for owner,grid in part.surface_grids.items())
    for plan in all_plans:out.faces.extend(faces[plan.index]);out.owners.extend([plan.index]*len(faces[plan.index]))
    if len(out.faces)>max_polygons or sum(out.samples_per_edge.values())>2*max_polygons:raise ValueError(tr('Group polygon or sample limit exceeded'))
    for name in ('directional_smoothing','local_density','point_density'):setattr(out,name,dict(kwargs.get(name) or {}))
    out.edge_evaluator=_Network(parts,out.source_points,out.directional_smoothing);out.parts=tuple(parts);out.component_cache=current;out.reused_components=sum(1 for part in parts if any(part is v[1] for v in old.values()));out.reused_patches=reused
    return out
