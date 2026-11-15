"""Reuse independent control components without changing the surface generator."""
from .i18n import tr
from collections import defaultdict
from collections.abc import Sequence,Mapping
from . import core
from .mesh_views import CutLines,OwnerEdges


class _MeshAssembly:
    def __init__(self,parts,plans):self.parts=parts;self.plans=plans;self.data=None
    def get(self,name):
        if self.data is None:
            self.data=self._build();self.parts=();self.plans=()
        return self.data[name]
    def _build(self):
        vertices=[];keys=[];faces=[];owners=[];columns={};grids={}
        by_owner={owner:part for part in self.parts for owner in part.face_paths}
        remaps={id(part):[-1]*len(part.vertices) for part in self.parts}
        for plan in self.plans:
            part=by_owner[plan.index];remap=remaps[id(part)]
            for v in part.production[plan.index]:
                if remap[v]>=0:raise ValueError(tr('Duplicate component index'))
                remap[v]=len(vertices);vertices.append(part.vertices[v]);keys.append(part.vertex_keys[v])
        if any(v<0 for remap in remaps.values() for v in remap):raise ValueError(tr('Incomplete component assembly order'))
        by_face=defaultdict(list)
        for part in self.parts:
            remap=remaps[id(part)]
            for face,owner in zip(part.faces,part.owners):by_face[owner].append(tuple(remap[v] for v in face))
            columns.update((owner,data.remapped(remap)) for owner,data in part.surface_columns.items())
            grids.update((owner,data.remapped(remap)) for owner,data in part.surface_grids.items())
        for plan in self.plans:faces.extend(by_face[plan.index]);owners.extend([plan.index]*len(by_face[plan.index]))
        return dict(vertices=vertices,vertex_keys=keys,faces=faces,owners=owners,surface_columns=columns,surface_grids=grids,
            cut_lines=CutLines(vertices,columns,OwnerEdges(faces,owners)))


class _MeshSequence(Sequence):
    def __init__(self,assembly,name,size):self.assembly=assembly;self.name=name;self.size=size
    def __len__(self):return self.size
    def __iter__(self):return iter(self.assembly.get(self.name))
    def __getitem__(self,index):return self.assembly.get(self.name)[index]
    def __eq__(self,other):
        return self.assembly.get(self.name)==(other.assembly.get(other.name) if isinstance(other,_MeshSequence) else other)


class _MeshMapping(Mapping):
    def __init__(self,assembly,name,size):self.assembly=assembly;self.name=name;self.size=size
    def __len__(self):return self.size
    def __iter__(self):return iter(self.assembly.get(self.name))
    def __getitem__(self,key):return self.assembly.get(self.name)[key]


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
    """Solve and cache disconnected guide islands independently.

    Shared control vertices define a component, including point junctions.
    Incomplete/ambiguous inputs use the original generator as a whole.
    """
    fallback=lambda:core.generate(points,boundaries,settings,rows,max_polygons,previous=previous,**kwargs)
    if any(len(loop)<3 or any(v<0 or v>=len(points) for v in loop) for loop in boundaries):return fallback()
    if len(boundaries)<2 or not kwargs.get('regular_rows') or kwargs.get('edge_evaluator') is not None:return fallback()
    options=list(settings or [core.ZoneSettings() for _ in boundaries]);caps=kwargs.get('caps');indices=list(kwargs.get('face_indices') or range(len(boundaries)))
    if not caps or any(cap is None for cap in caps):return fallback()
    parent=list(range(len(boundaries)));vertex_owner={}
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    for i,loop in enumerate(boundaries):
        for v in loop:
            if v in vertex_owner:parent[find(i)]=find(vertex_owner[v])
            else:vertex_owner[v]=i
    groups=defaultdict(list)
    for i in range(len(boundaries)):groups[find(i)].append(i)
    if len(groups)<2:return fallback()
    old=getattr(previous,'component_cache',{});current={};parts=[];reused=0
    for members in groups.values():
        used=sorted({v for i in members for v in boundaries[i]});used_set=set(used)
        local=dict(kwargs);local['caps']=[caps[i] for i in members];local['face_indices']=[indices[i] for i in members]
        for name in ('directional_smoothing','local_density','layout_lengths'):
            local[name]={e:value for e,value in (kwargs.get(name) or {}).items() if e[0] in used_set and e[1] in used_set}
        local['point_density']={v:value for v,value in (kwargs.get('point_density') or {}).items() if v in used_set}
        key=tuple((indices[i],tuple(boundaries[i])) for i in members)
        signature=(tuple(tuple(float(c) for c in points[v]) for v in used),tuple(options[i] for i in members),_freeze(local['caps']),int(rows),float(kwargs.get('reference_length',0.)),max_polygons,
            tuple(tuple(sorted(local[name].items())) for name in ('directional_smoothing','local_density','point_density','layout_lengths')),tuple(kwargs.get('plane_axes',())))
        saved=old.get(key)
        if saved and saved[0]==signature:part=saved[1];reused+=len(members)
        else:
            part=core.generate(points,[boundaries[i] for i in members],[options[i] for i in members],rows,max_polygons,previous=saved[1] if saved else None,_track_production=True,**local)
        if part.warnings:return fallback()
        current[key]=(signature,part);parts.append(part)
    # Live renders the independent parts. Only editing and baking need a
    # joint vertex index; assemble it on first access, in production order.
    out=core.MeshResult();out.source_points=tuple(tuple(float(c) for c in p) for p in points);out.regular_rows=bool(kwargs['regular_rows']);out.base_rows=max(2,min(256,int(rows)));out.reference_length=float(kwargs.get('reference_length',0.)) or max(part.reference_length for part in parts);out.target_spacing=min(part.target_spacing for part in parts)
    for part in parts:
        for name in ('warnings','face_paths','guide_curves','effective_widths','cap_widths','rim_caps','smooth_rails','width_transitions','samples_per_edge','plans_by_loop','transitions','orientation_by_face','surface_sections','rail_samples','layout_lengths','row_cache','sampling_cache','edge_sampling_cache','correspondence_cache','patch_cache','final_sampling_cache','valid_cache'):
            getattr(out,name).update(getattr(part,name,{}))
    vertices=sum(len(part.vertices) for part in parts);faces=sum(len(part.faces) for part in parts)
    if faces>max_polygons or sum(out.samples_per_edge.values())>2*max_polygons:raise ValueError(tr('Group polygon or sample limit exceeded'))
    assembly=_MeshAssembly(parts,sorted(out.plans_by_loop.values(),key=core._plan_priority))
    for name,size in (('vertices',vertices),('vertex_keys',vertices),('faces',faces),('owners',faces)):setattr(out,name,_MeshSequence(assembly,name,size))
    for name in ('surface_columns','surface_grids','cut_lines'):setattr(out,name,_MeshMapping(assembly,name,len(out.plans_by_loop)))
    for name in ('directional_smoothing','local_density','point_density'):setattr(out,name,dict(kwargs.get(name) or {}))
    out.edge_evaluator=_Network(parts,out.source_points,out.directional_smoothing);out.parts=tuple(parts);out.component_cache=current;out.reused_components=sum(1 for part in parts if any(part is v[1] for v in old.values()));out.reused_patches=reused
    return out
