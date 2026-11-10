"""Corner normals with sharp guide boundaries; GPU-only MatCap resources."""
from .core import edge_key


def sharp_edges(result):
    indices={key:i for i,key in enumerate(result.vertex_keys)};sharp=set()
    for rails in result.rail_samples.values():
        for samples in rails:
            for (ka,pa),(kb,pb) in zip(samples,samples[1:]):
                if ka in indices and kb in indices and indices[ka]!=indices[kb]:sharp.add(edge_key(indices[ka],indices[kb]))
    return sharp|set(getattr(result,"extra_sharp_edges",()))


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
        offsets=[];vertices=[];next_vertices=[];face_ids=[];edges={}
        for fi,face in enumerate(faces):
            offset=len(vertices);offsets.append(offset)
            vertices.extend(face);next_vertices.extend(face[1:]+face[:1]);face_ids.extend([fi]*len(face))
            if smooth:
                for i,a in enumerate(face):
                    j=(i+1)%len(face);b=face[j]
                    edges.setdefault(edge_key(a,b),[]).append(((a,offset+i),(b,offset+j)))
        if smooth:
            parents=list(range(len(vertices)))
            def root(x):
                while parents[x]!=x:parents[x]=parents[parents[x]];x=parents[x]
                return x
            for key,group in edges.items():
                if key in sharp or len(group)!=2:continue
                one=dict(group[0]);two=dict(group[1])
                for vertex in key:parents[root(one[vertex])]=root(two[vertex])
            groups=np.asarray([root(i) for i in range(len(vertices))],dtype=np.intp)
        else:groups=None
        topology=(signature,np.asarray(offsets,dtype=np.intp),np.asarray(vertices,dtype=np.intp),
            np.asarray(next_vertices,dtype=np.intp),np.asarray(face_ids,dtype=np.intp),groups)
    _,offsets,vertices,next_vertices,face_ids,groups=topology
    points=np.asarray(result.vertices,dtype=np.float64)
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
    output=tuple(values[offset:offset+len(face)] for offset,face in zip(offsets,faces))
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
            float intensity=lighting.w>.5?1.0:.75+.25*abs(dot(n,normalize(vec3(.31,.43,.85))));
            vec3 base=baseColor*intensity*(lighting.z/.8);
            if(lighting.x>.5){
                vec2 uv=clamp(n.xy*.5+.5,vec2(.001),vec2(.999));
                base=(baseColor*texture(matcapDiffuse,uv).rgb+lighting.y*texture(matcapSpecular,uv).rgb)*lighting.z;
            }
            fragColor=vec4(mix(base,tint.rgb,tint.a),1.0);
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


def bind(context,follow_matcap=True,brightness=.8):
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
        textures=_MATCAPS[None];status='MatCap недоступен: используется студийный свет'
    surface.uniform_sampler('matcapDiffuse',textures[0]);surface.uniform_sampler('matcapSpecular',textures[1])
    surface.uniform_float('lighting',(float(bool(path and status is None)),float(shading.show_specular_highlight),brightness,float(shading.type=='SOLID' and shading.light=='FLAT')))
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
