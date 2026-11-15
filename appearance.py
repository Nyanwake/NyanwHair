"""Corner normals with sharp guide boundaries; GPU-only MatCap resources."""
from .i18n import tr
from .core import edge_key


def _guide_edges(result,sharp_only):
    signature=(id(result.vertex_keys),len(result.vertex_keys),id(result.rail_samples),id(result.cap_widths),id(result.rim_caps),id(result.smooth_rails))
    saved=getattr(result,'guide_edge_cache',None)
    if saved is None or saved[0]!=signature:
        saved=(signature,{key:i for i,key in enumerate(result.vertex_keys)},{})
        result.guide_edge_cache=saved
    if sharp_only in saved[2]:return saved[2][sharp_only]
    indices=saved[1];sharp=set()
    for rails in result.rail_samples.values():
        for samples in rails:
            for (ka,pa),(kb,pb) in zip(samples,samples[1:]):
                edge=ka[1:3] if ka[0]=='s' else kb[1:3] if kb[0]=='s' else edge_key(ka[1],kb[1])
                if sharp_only and edge in result.smooth_rails:continue
                if ka in indices and kb in indices and indices[ka]!=indices[kb]:sharp.add(edge_key(indices[ka],indices[kb]))
    for lo,hi in result.rim_caps:
        width=result.cap_widths[lo,hi]
        keys=[('v',lo)]+[('cap',lo,hi,x,width) for x in range(1,width)]+[('v',hi)]
        for one,two in zip(keys,keys[1:]):
            if one in indices and two in indices and indices[one]!=indices[two]:sharp.add(edge_key(indices[one],indices[two]))
    saved[2][sharp_only]=sharp
    return sharp


def sharp_edges(result):return _guide_edges(result,True)|set(getattr(result,'extra_sharp_edges',()))
def guide_edges(result):return _guide_edges(result,False)|set(getattr(result,'extra_seam_edges',()))



def _normal_groups(vertices,next_vertices,offsets,sharp,count):
    import numpy as np
    size=len(vertices);codes=np.minimum(vertices,next_vertices)*count+np.maximum(vertices,next_vertices)
    order=np.argsort(codes,kind='stable');sorted_codes=codes[order]
    starts=np.r_[0,np.flatnonzero(sorted_codes[1:]!=sorted_codes[:-1])+1]
    starts=starts[np.diff(np.r_[starts,size])==2]
    if sharp:
        sharp_codes=np.fromiter((a*count+b for a,b in sharp),dtype=np.int64)
        starts=starts[~np.isin(sorted_codes[starts],sharp_codes)]
    parents=np.arange(size,dtype=np.intp)
    if not len(starts):return parents
    one=order[starts];two=order[starts+1]
    following=np.arange(size,dtype=np.intp)+1;following[np.r_[offsets[1:]-1,size-1]]=offsets
    same=vertices[one]==vertices[two]
    a=np.r_[one,following[one]];b=np.r_[np.where(same,two,following[two]),np.where(same,following[two],two)]
    # Hook whole fan roots; pointer jumping preserves exact connectivity.
    while True:
        while True:
            flattened=parents[parents]
            if np.array_equal(flattened,parents):break
            parents=flattened
        ra=parents[a];rb=parents[b]
        if np.all(ra==rb):return parents
        np.minimum.at(parents,np.maximum(ra,rb),np.minimum(ra,rb))


class _CornerNormals:
    __slots__=('values','offsets','sizes')
    def __init__(self,values,offsets,faces):
        self.values=values;self.offsets=offsets;self.sizes=tuple(map(len,faces))
    def __len__(self):return len(self.offsets)
    def __getitem__(self,index):
        if isinstance(index,slice):return tuple(self[i] for i in range(*index.indices(len(self))))
        offset=self.offsets[index];return self.values[offset:offset+self.sizes[index]]
    def __iter__(self):
        for i in range(len(self)):yield self[i]

def corner_normals(result,smooth=True,topology=None,packed=False):
    old=getattr(result,'normal_cache',None)
    if old and old[0]==smooth:return old[1] if packed else tuple(tuple(tuple(v) for v in face) for face in old[1])
    import numpy as np
    faces=result.faces
    if not faces:
        result.normal_cache=(smooth,());return ()
    sharp=frozenset(sharp_edges(result)) if smooth else frozenset()
    signature=(bool(smooth),tuple(faces),sharp)
    topology=topology or getattr(result,'normal_topology',None)
    if topology is None or topology[0]!=signature:
        layout=getattr(result,'corner_layout',None)
        if layout is not None and layout[0]==tuple(faces):
            _,offsets,vertices,next_vertices,face_ids=layout
        else:
            offsets=[];vertices=[];next_vertices=[];face_ids=[]
            for fi,face in enumerate(faces):
                offsets.append(len(vertices));vertices.extend(face);next_vertices.extend(face[1:]+face[:1]);face_ids.extend([fi]*len(face))
            offsets=np.asarray(offsets,dtype=np.intp);vertices=np.asarray(vertices,dtype=np.intp);next_vertices=np.asarray(next_vertices,dtype=np.intp)
        groups=_normal_groups(vertices,next_vertices,offsets,sharp,len(result.vertices)) if smooth else None
        topology=(signature,offsets,vertices,next_vertices,np.asarray(face_ids,dtype=np.intp),groups)
    _,offsets,vertices,next_vertices,face_ids,groups=topology
    points=np.asarray(result.vertices,dtype=np.float64)
    from . import kernels
    values=kernels.shade(points,topology,smooth)
    if values is NotImplemented:
        areas=np.add.reduceat(np.cross(points[vertices],points[next_vertices]),offsets,axis=0)
        if smooth:
            weighted=areas[face_ids]
            sums=np.stack([np.bincount(groups,weights=weighted[:,axis],minlength=len(vertices)) for axis in range(3)],axis=1)
            size=np.linalg.norm(sums,axis=1);sums/=np.where(size>1e-10,size,1.)[:,None];sums[size<=1e-10]=0.
            values=sums[groups]
        else:
            size=np.linalg.norm(areas,axis=1);areas/=np.where(size>1e-10,size,1.)[:,None];areas[size<=1e-10]=0.
            values=areas[face_ids]
    result.normal_array=values
    output=_CornerNormals(values,offsets,faces)
    result.normal_topology=topology;result.normal_cache=(smooth,output)
    return output if packed else tuple(tuple(tuple(v) for v in face) for face in output)


_SHADER=None
_MATCAPS={}
_PREVIEWS=None

def shader():
    global _SHADER
    if _SHADER is None:
        import gpu
        info=gpu.types.GPUShaderCreateInfo();interface=gpu.types.GPUStageInterfaceInfo('nyanwhair_surface')
        interface.smooth('VEC3','viewNormal');interface.smooth('VEC4','tint');interface.smooth('VEC3','baseColor')
        info.push_constant('MAT4','viewProjectionMatrix');info.push_constant('MAT3','viewRotation');info.push_constant('VEC4','lighting')
        info.vertex_in(0,'VEC3','pos');info.vertex_in(1,'VEC3','normal');info.vertex_in(2,'VEC4','color');info.vertex_in(3,'VEC3','base');info.vertex_out(interface)
        info.sampler(0,'FLOAT_2D','matcapDiffuse');info.sampler(1,'FLOAT_2D','matcapSpecular');info.fragment_out(0,'VEC4','fragColor')
        info.vertex_source('void main(){gl_Position=viewProjectionMatrix*vec4(pos,1.0);viewNormal=normalize(viewRotation*normal);tint=color;baseColor=base;}')
        info.fragment_source('''
        void main(){
            vec3 n=normalize(viewNormal)*(gl_FrontFacing?1.0:-1.0);
            float intensity=lighting.w>=0.0?1.0:.75+.25*abs(dot(n,normalize(vec3(.31,.43,.85))));
            vec3 base=baseColor*intensity*(lighting.z/.8);
            if(lighting.x>.5){
                vec2 uv=clamp(n.xy*.5+.5,vec2(.001),vec2(.999));
                base=(baseColor*texture(matcapDiffuse,uv).rgb+lighting.y*texture(matcapSpecular,uv).rgb)*lighting.z;
            }
            fragColor=vec4(mix(base,tint.rgb,tint.a)*(gl_FrontFacing?1.0:.72),abs(lighting.w));
        }''')
        _SHADER=gpu.shader.create_from_info(info)
    return _SHADER


def _textures(path):
    global _PREVIEWS
    import gpu,numpy as np
    try:
        import OpenImageIO as oiio
        image=oiio.ImageInput.open(path)
        if image is None:raise ValueError('MatCap could not be opened')
        try:spec=image.spec();names=list(spec.channelnames);data=image.read_image()
        finally:image.close()
        def layer(prefix):
            keys=[prefix+c for c in 'RGB']
            if not all(key in names for key in keys):return None
            rgb=data[:,:,[names.index(key) for key in keys]]
            alpha=np.ones((*rgb.shape[:2],1),dtype=np.float32)
            return np.ascontiguousarray(np.flipud(np.concatenate((rgb,alpha),axis=2)),dtype=np.float32)
        diffuse=layer('diffuse.')
        if diffuse is None:diffuse=layer('')
        if diffuse is None:raise ValueError('MatCap has no RGB pass')
        specular=layer('specular.')
    except ImportError:
        # Blender's preview loader is also ID-free and handles EXR thumbnails.
        import bpy.utils.previews
        if _PREVIEWS is None:_PREVIEWS=bpy.utils.previews.new()
        preview=_PREVIEWS.load(path,path,'IMAGE') if path not in _PREVIEWS else _PREVIEWS[path]
        w,h=preview.image_size
        if not w or not h:raise ValueError('MatCap preview unavailable')
        diffuse=np.asarray(preview.image_pixels_float,dtype=np.float32).reshape(h,w,4);specular=None
        # Preview pixels are already in display space.
        diffuse=np.ascontiguousarray(diffuse);diffuse[:,:,:3]=np.where(diffuse[:,:,:3]<=.04045,diffuse[:,:,:3]/12.92,((diffuse[:,:,:3]+.055)/1.055)**2.4)
    if specular is None:specular=np.zeros((1,1,4),dtype=np.float32)
    def texture(pixels):
        h,w,_=pixels.shape
        return gpu.types.GPUTexture((w,h),format='RGBA16F',data=gpu.types.Buffer('FLOAT',pixels.shape,pixels))
    return texture(diffuse),texture(specular)


def bind(context,follow_matcap=True,brightness=.8,opacity=1.):
    import gpu
    surface=shader();surface.bind()
    view=gpu.matrix.get_model_view_matrix();surface.uniform_float('viewProjectionMatrix',gpu.matrix.get_projection_matrix()@view);surface.uniform_float('viewRotation',view.to_3x3())
    shading=context.space_data.shading;path=None
    if follow_matcap and shading.type=='SOLID' and shading.light=='MATCAP':
        studio=context.preferences.studio_lights.get(shading.studio_light)
        if studio is not None:path=studio.path
    status=None
    if path not in _MATCAPS:
        try:_MATCAPS[path]=_textures(path) if path else _dummy()
        except (ValueError,RuntimeError,OSError,ImportError):_MATCAPS[path]=None
        while len(_MATCAPS)>4:_MATCAPS.pop(next(iter(_MATCAPS)))
    textures=_MATCAPS[path]
    if textures is None:
        if None not in _MATCAPS:_MATCAPS[None]=_dummy()
        textures=_MATCAPS[None];status=tr('MatCap unavailable: using studio lighting')
    surface.uniform_sampler('matcapDiffuse',textures[0]);surface.uniform_sampler('matcapSpecular',textures[1])
    # Share the existing scalar: sign selects Flat, magnitude fades the preview.
    # This keeps push constants within the portable 128-byte budget.
    flat=shading.type=='SOLID' and shading.light=='FLAT'
    surface.uniform_float('lighting',(float(bool(path and status is None)),float(shading.show_specular_highlight),brightness,opacity if flat else -opacity))
    return surface,status


def _dummy():
    import gpu
    pixel=gpu.types.GPUTexture((1,1),format='RGBA16F',data=gpu.types.Buffer('FLOAT',4,[0.,0.,0.,1.]))
    return pixel,pixel


def clear():
    global _SHADER,_PREVIEWS
    _SHADER=None;_MATCAPS.clear()
    if _PREVIEWS is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(_PREVIEWS);_PREVIEWS=None
