"""Owned numeric mesh blocks with deferred Python editing views."""
import bisect
from collections.abc import Sequence
import numpy as np


class PointArray(Sequence):
    def __init__(self,array):self.array=np.asarray(array,dtype=np.float64);self.array.setflags(write=False)
    def __len__(self):return len(self.array)
    def __array__(self,dtype=None,copy=None):
        data=np.asarray(self.array,dtype=dtype)
        return data.copy() if copy else data
    def __getitem__(self,index):
        if isinstance(index,slice):return [tuple(p) for p in self.array[index].tolist()]
        return tuple(self.array[index].tolist())
    def __iter__(self):return map(tuple,self.array.tolist())
    def __eq__(self,other):return list(self)==list(other)


class IndexMap:
    def __init__(self,array):self.array=np.asarray(array,dtype=np.intp)
    def __getitem__(self,index):
        return int(self.array[index]) if 0<=index<len(self.array) and self.array[index]>=0 else None
    def __contains__(self,index):return 0<=index<len(self.array) and self.array[index]>=0
    def get(self,index,default=None):
        value=self[index];return default if value is None else value


class PackedFaces(Sequence):
    def __init__(self,offsets,indices):
        self.offsets=np.asarray(offsets,dtype=np.intp);self.indices=np.asarray(indices,dtype=np.intp)
        self.offsets.setflags(write=False);self.indices.setflags(write=False);self.cache={};self._signature=None
    @classmethod
    def quads(cls,values):
        values=np.asarray(values,dtype=np.intp).reshape(-1,4)
        unique=1+np.count_nonzero(np.diff(np.sort(values,axis=1),axis=1),axis=1)
        values=values[unique>=3];keep=values!=np.roll(values,1,axis=1);sizes=keep.sum(axis=1)
        return cls(np.r_[0,np.cumsum(sizes)],values[keep])
    @classmethod
    def rows(cls,faces):
        if all(len(face)==4 for face in faces):return cls.quads(faces)
        clean=[]
        for face in faces:
            row=[v for i,v in enumerate(face) if v!=face[i-1]]
            if len(row)>1 and row[0]==row[-1]:row.pop()
            if len(set(row))>=3:clean.append(row)
        sizes=np.fromiter(map(len,clean),dtype=np.intp,count=len(clean))
        return cls(np.r_[0,np.cumsum(sizes)],np.fromiter((v for face in clean for v in face),dtype=np.intp,count=int(sizes.sum())))
    def __len__(self):return len(self.offsets)-1
    def __getitem__(self,index):
        if isinstance(index,slice):return [self[i] for i in range(*index.indices(len(self)))]
        if index<0:index+=len(self)
        if not 0<=index<len(self):raise IndexError(index)
        if index not in self.cache:self.cache[index]=tuple(self.indices[self.offsets[index]:self.offsets[index+1]].tolist())
        return self.cache[index]
    def __eq__(self,other):
        if isinstance(other,PackedFaces):return np.array_equal(self.offsets,other.offsets) and np.array_equal(self.indices,other.indices)
        return list(self)==list(other)
    @property
    def signature(self):
        if self._signature is None:self._signature=(self.offsets.tobytes(),self.indices.tobytes())
        return self._signature
    def mapped(self,mapping,reverse=False):
        values=mapping[self.indices]
        if reverse:
            sizes=np.diff(self.offsets);ends=np.repeat(self.offsets[:-1]+self.offsets[1:]-1,sizes)
            values=values[ends-np.arange(len(values))]
        return PackedFaces(self.offsets,values)


def face_signature(faces):return faces.signature if isinstance(faces,PackedFaces) else tuple(faces)
def shared_items(keys):
    return keys.shared_items() if hasattr(keys,'shared_items') else ((key,i) for i,key in enumerate(keys) if key[0]!='inside')


class RecipeBlock:
    def __init__(self,points,faces):
        self.points=points;self.positions=np.asarray([position for key,position in points],dtype=np.float64).reshape(-1,3)
        self.shared={i:key for i,(key,position) in enumerate(points) if key[0] not in ('inside','width')}
        self.faces=PackedFaces.rows(faces);self.positions.setflags(write=False)
    def key(self,index):return self.points[index][0]


class FaceStream(Sequence):
    def __init__(self):self.parts=[];self.size=0;self.tail=None;self.cache=None
    def __len__(self):return self.size
    def append(self,face):
        if self.tail is None:self.tail=[];self.parts.append(self.tail)
        self.tail.append(face);self.size+=1;self.cache=None
    def extend(self,faces):
        if isinstance(faces,FaceStream):self.parts.extend(faces.parts)
        elif len(faces):self.parts.append(faces)
        self.size+=len(faces);self.tail=None;self.cache=None
    def packed(self):
        if self.cache is None:
            offsets=[np.array([0],dtype=np.intp)];indices=[];count=0
            for part in self.parts:
                if isinstance(part,PackedFaces):local=part
                else:
                    sizes=np.fromiter(map(len,part),dtype=np.intp,count=len(part))
                    local=PackedFaces(np.r_[0,np.cumsum(sizes)],np.fromiter((v for f in part for v in f),dtype=np.intp,count=int(sizes.sum())))
                offsets.append(local.offsets[1:]+count);indices.append(local.indices);count+=len(local.indices)
            self.cache=PackedFaces(np.concatenate(offsets),np.concatenate(indices) if indices else np.empty(0,dtype=np.intp))
        return self.cache
    def __iter__(self):return iter(self.packed())
    def __getitem__(self,index):return self.packed()[index]


class SelectedRows(Sequence):
    def __init__(self,rows,indices):self.rows=rows;self.indices=indices
    def __len__(self):return len(self.indices)
    def __getitem__(self,index):
        if isinstance(index,slice):return [self.rows[int(i)] for i in self.indices[index]]
        return self.rows[int(self.indices[index])]
    def __eq__(self,other):return list(self)==list(other)


class KeyStore(Sequence):
    def __init__(self):self.values=[];self.starts=[];self.blocks=[]
    def append(self,key):self.values.append(key)
    def block(self,count,block,start):
        self.starts.append(len(self.values));self.blocks.append((block,start));self.values.extend([None]*count)
    def __len__(self):return len(self.values)
    def __getitem__(self,index):
        if isinstance(index,slice):return [self[i] for i in range(*index.indices(len(self)))]
        if index<0:index+=len(self)
        if not 0<=index<len(self):raise IndexError(index)
        key=self.values[index]
        if key is None:
            i=bisect.bisect_right(self.starts,index)-1;block,start=self.blocks[i];key=block.key(start+index-self.starts[i])
        return key


class MappedKeys(Sequence):
    def __init__(self,store,used,mapping,shared):self.store=store;self.used=used;self.mapping=mapping;self.shared=shared
    def __len__(self):return len(self.used)
    def __getitem__(self,index):
        if isinstance(index,slice):return [self.store[int(i)] for i in self.used[index]]
        return self.store[int(self.used[index])]
    def shared_items(self):
        return ((key,self.mapping.get(index)) for key,index in self.shared.items() if key[0]!='inside' and index in self.mapping)
    def __eq__(self,other):return list(self)==list(other)


class VertexPool(Sequence):
    def __init__(self):self.data=np.empty((256,3));self.size=0;self.keys=KeyStore();self.shared={}
    def __len__(self):return self.size
    def __array__(self,dtype=None,copy=None):
        data=np.asarray(self.data[:self.size],dtype=dtype)
        return data.copy() if copy else data
    def __getitem__(self,index):return tuple(self.data[:self.size][index])
    def reserve(self,count):
        if self.size+count>len(self.data):
            data=np.empty((max(self.size+count,len(self.data)*2),3));data[:self.size]=self.data[:self.size];self.data=data
    def __call__(self,key,position):
        found=self.shared.get(key)
        if found is None:
            self.reserve(1);found=self.size;self.shared[key]=found;self.keys.append(key);self.data[found]=position;self.size+=1
        return found
    def get(self,key,default=None):return self.shared.get(key,default)
    def bind(self,block):
        remap=np.empty(len(block.positions),dtype=np.intp);start=0
        for end,key in list(block.shared.items())+[(len(remap),None)]:
            count=end-start
            if count:
                self.reserve(count);remap[start:end]=np.arange(self.size,self.size+count)
                self.data[self.size:self.size+count]=block.positions[start:end];self.keys.block(count,block,start);self.size+=count
            if key is not None:remap[end]=self(key,block.positions[end])
            start=end+1
        return remap
