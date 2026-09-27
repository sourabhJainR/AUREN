"""Deterministic incremental repository intelligence index."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
from typing import Iterable

@dataclass(frozen=True)
class SymbolRecord:
    path:str; name:str; kind:str; content_sha:str
@dataclass(frozen=True)
class ImpactEdge:
    source:str; target:str; kind:str

class RepositoryIndex:
    def __init__(self): self._symbols={}; self._edges=set()
    def update(self,path:str,content:bytes, symbols:Iterable[tuple[str,str]]=()):
        sha=hashlib.sha256(content).hexdigest()
        self._symbols[path]=tuple(SymbolRecord(path,n,k,sha) for n,k in symbols)
        return sha
    def add_edge(self,source:str,target:str,kind:str="references"): self._edges.add(ImpactEdge(source,target,kind))
    def symbols(self,path:str|None=None): return tuple(s for p in self._symbols.values() for s in p if path is None or s.path==path)
    def impacted(self,path:str)->tuple[str,...]: return tuple(sorted({e.target for e in self._edges if e.source==path}))
