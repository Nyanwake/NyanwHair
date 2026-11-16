"""Regular strip recipes with local topology and array-owned interior vertices."""
import bisect
import numpy as np
from .core import edge_key,bias_cross_position
from .mesh_views import IndexRows,Sections
from .packed import PackedFaces,IndexMap
from .surface import CapBlend,geometry_path
from .ribbon import zipper_flows
from .row_layout import correspondence,rows,profile_normals,profile_grid
from .i18n import tr


class StripBlock:
    def __init__(self,plan,width,registry,ca,cb,left,right,profiles,limit):
        count=max(1,int(((len(left)-1)+(len(right)-1))*.5+.5))
        mapping=correspondence(registry,ca,cb) if width>1 else None
        aa,bb,fractions,fa,fb=rows(ca,cb,left,right,count,mapping)
        if len(aa)*(width+1)>2*limit:raise ValueError(tr('Strip vertex limit exceeded'))
        normals=profile_normals(aa,bb,plan.normal,packed=True)
        blend=CapBlend.from_profiles(plan,[(aa[i][1],bb[i][1]) for i in (0,-1)],(normals[0],normals[-1]),profiles)
        profile=[bias_cross_position(x/width,plan.settings.bias) for x in range(width+1)]
        positions=profile_grid(aa,bb,normals,plan.bulge,profile,blend,fractions,packed=True)
        data=np.empty((len(left)+len(right)+len(aa)*(width-1),3));self.shared={};indices={};cursor=0
        def vertex(key,point):
            nonlocal cursor
            index=indices.get(key)
            if index is None:
                index=cursor;indices[key]=index;self.shared[index]=key;data[index]=point;cursor+=1
            return index
        actual_a=np.asarray([vertex(k,p) for k,p in left],dtype=np.intp)
        actual_b=np.asarray([vertex(k,p) for k,p in right],dtype=np.intp)
        grid=np.full((len(aa),width+1),-1,dtype=np.intp);sections=np.asarray(positions,dtype=np.float64)
        self.private_starts=[];self.private_rows=[];self.owner=plan.index
        for row,((ka,a),(kb,b)) in enumerate(zip(aa,bb)):
            grid[row,0]=indices.get(ka,-1);grid[row,-1]=indices.get(kb,-1)
            sections[row,0]=a;sections[row,-1]=b
            if width==1:continue
            if ka==kb:
                grid[row,1:-1]=vertex(ka,a);sections[row,1:-1]=a
            elif row==0 and plan.start[0]=='edge' or row==len(aa)-1 and plan.end[0]=='edge':
                cap=plan.start if row==0 else plan.end
                edge=edge_key(plan.boundary[cap[1]],plan.boundary[(cap[1]+1)%len(plan.boundary)])
                for x in range(1,width):
                    aligned=x if plan.left[0 if row==0 else -1]==edge[0] else width-x
                    key=registry.cap_keys[edge][aligned];point=profiles[edge][aligned]
                    grid[row,x]=vertex(key,point);sections[row,x]=point
            else:
                self.private_starts.append(cursor);self.private_rows.append(row)
                data[cursor:cursor+width-1]=sections[row,1:-1]
                grid[row,1:-1]=np.arange(cursor,cursor+width-1);cursor+=width-1
        self.positions=data[:cursor];self.positions.setflags(write=False)
        self.grid=grid;self.sections=Sections(sections)
        self.columns=[actual_a]+[grid[:,x] for x in range(1,width)]+[actual_b]
        faces=[]
        for x in range(width):
            one=fa if x==0 else fractions;two=fb if x==width-1 else fractions
            path=geometry_path(self.positions[self.columns[x]],self.positions[self.columns[x+1]],one,two) if mapping and x in (0,width-1) else zipper_flows(one,two)
            path=np.asarray(path,dtype=np.intp);a,b=path[:-1].T;na,nb=path[1:].T
            faces.append(np.column_stack((self.columns[x][a],self.columns[x+1][b],self.columns[x+1][nb],self.columns[x][na])))
        count=sum(len(f) for f in faces)
        if count>limit:raise ValueError(tr('Strip polygon limit exceeded'))
        self.faces=PackedFaces.quads(np.concatenate(faces))
    def key(self,index):
        key=self.shared.get(index)
        if key is not None:return key
        i=bisect.bisect_right(self.private_starts,index)-1
        return ('inside',self.owner,self.private_rows[i],index-self.private_starts[i]+1)


def build(plan,width,registry,ca,cb,left,right,profiles,put,emit,limit,cache,previous):
    caps=[]
    for kind,i in (plan.start,plan.end):
        if kind=='edge':
            edge=edge_key(plan.boundary[i],plan.boundary[(i+1)%len(plan.boundary)])
            caps.append((edge,tuple(profiles[edge]),tuple(registry.cap_keys.get(edge,()))))
    signature=('strip_blocks',plan,width,ca.geometry_signature,cb.geometry_signature,tuple(left),tuple(right),tuple(caps))
    old=previous.get(plan.boundary);reused=bool(old and old[0]==signature)
    block=old[1] if reused else StripBlock(plan,width,registry,ca,cb,left,right,profiles,limit)
    cache[plan.boundary]=(signature,block)
    remap=put.bind(block);emit.block(block.faces,remap)
    mapping=IndexMap(remap)
    return block.sections,IndexRows(block.columns,(mapping,)),IndexRows(block.grid,(mapping,),True),reused
