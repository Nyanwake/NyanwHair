"""Deferred editing views over immutable numeric mesh data."""
from collections import defaultdict
from collections.abc import Mapping,Sequence
from .core import edge_key,mix


class IndexRows(Sequence):
    def __init__(self,rows,maps=(),keep_none=False):
        self.rows=rows;self.maps=maps;self.keep_none=keep_none;self.cache={}
    def remapped(self,mapping):return IndexRows(self.rows,self.maps+(mapping,),self.keep_none)
    def __len__(self):return len(self.rows)
    def __getitem__(self,index):
        if isinstance(index,slice):return tuple(self[i] for i in range(*index.indices(len(self))))
        if index<0:index+=len(self)
        if not 0<=index<len(self):raise IndexError(index)
        if index not in self.cache:
            output=[]
            for value in self.rows[index]:
                for mapping in self.maps:
                    if value is None:break
                    value=mapping.get(value) if isinstance(mapping,dict) else mapping[value]
                if value is not None or self.keep_none:output.append(value)
            self.cache[index]=tuple(output)
        return self.cache[index]


class OwnerEdges(Mapping):
    def __init__(self,faces,owners):self.faces=faces;self.owners=owners;self.cache=None
    def _prepare(self):
        if self.cache is None:
            values=defaultdict(set)
            for face,owner in zip(self.faces,self.owners):values[owner].update(edge_key(a,b) for a,b in zip(face,face[1:]+face[:1]))
            self.cache=values
    def __iter__(self):self._prepare();return iter(self.cache)
    def __len__(self):self._prepare();return len(self.cache)
    def __getitem__(self,index):self._prepare();return self.cache[index]


class CutLines(Mapping):
    def __init__(self,vertices,columns,edges):self.vertices=vertices;self.columns=columns;self.edges=edges;self.cache={}
    def __iter__(self):return iter(self.columns)
    def __len__(self):return len(self.columns)
    def __getitem__(self,zone):
        columns=self.columns[zone]
        if zone not in self.cache:
            width=len(columns)-1;lines={};edges=self.edges[zone]
            for x in range(1,width):
                line=columns[x]
                if len(line)>1 and all(a==b or edge_key(a,b) in edges for a,b in zip(line,line[1:])):lines[x/width]=tuple(self.vertices[v] for v in line)
            self.cache[zone]=lines
        return self.cache[zone]


class Sections(Sequence):
    lazy=True
    def __init__(self,rows,width=None):self.rows=rows;self.width=width;self.cache={}
    def __len__(self):return len(self.rows)
    def __getitem__(self,index):
        if isinstance(index,slice):return tuple(self[i] for i in range(*index.indices(len(self))))
        if index<0:index+=len(self)
        if not 0<=index<len(self):raise IndexError(index)
        if index not in self.cache:
            row=self.rows[index]
            if self.width is None:self.cache[index]=tuple(tuple(p) for p in row)
            else:
                n=len(row)-1;section=[]
                for x in range(self.width+1):
                    value=x/self.width*n;j=min(n-1,int(value));section.append(mix(row[j],row[j+1],value-j))
                self.cache[index]=tuple(section)
        return self.cache[index]
