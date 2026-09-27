"""Deterministic incremental repository intelligence index."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
from typing import Iterable
from . import rust_core

@dataclass(frozen=True)
class SymbolRecord:
    path:str; name:str; kind:str; content_sha:str
@dataclass(frozen=True)
class ImpactEdge:
    source:str; target:str; kind:str

class RepositoryIndex:
    def __init__(self): self._symbols={}; self._edges=set()
    def update(self,path:str,content:bytes,symbols:Iterable[tuple[str,str]]=()):
        sha=hashlib.sha256(content).hexdigest()
        self._symbols[path]=tuple(SymbolRecord(path,n,k,sha) for n,k in symbols)
        return sha
    def add_edge(self,source:str,target:str,kind:str="references"): self._edges.add(ImpactEdge(source,target,kind))
    def symbols(self,path:str|None=None): return tuple(s for p in self._symbols.values() for s in p if path is None or s.path==path)
    def impacted(self,path:str)->tuple[str,...]: return tuple(sorted({e.target for e in self._edges if e.source==path}))
    def digest(self)->str:
        files=[{"path":path,"content_sha":records[0].content_sha if records else ""} for path,records in self._symbols.items()]
        native=rust_core.repository_digest(files)
        if native:
            return native
        canonical=sorted((x["path"],x["content_sha"]) for x in files)
        h=hashlib.sha256()
        for path,sha in canonical:
            h.update(path.encode()); h.update(b"\0"); h.update(sha.encode()); h.update(b"\0")
        return h.hexdigest()
