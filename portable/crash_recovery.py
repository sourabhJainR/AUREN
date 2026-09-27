"""Crash-safe episode checkpoint store with idempotent writes."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, sqlite3, time
@dataclass(frozen=True)
class Checkpoint:
    episode_id:str; sequence:int; state:str; payload:dict; digest:str
class CheckpointStore:
    def __init__(self,db:str=":memory:"):
        self.db=db; self.cx=sqlite3.connect(db); self.cx.execute("PRAGMA journal_mode=WAL"); self.cx.execute("CREATE TABLE IF NOT EXISTS checkpoints(episode TEXT, seq INTEGER, state TEXT, payload TEXT, digest TEXT, PRIMARY KEY(episode,seq))"); self.cx.commit()
    def save(self,episode_id:str,sequence:int,state:str,payload:dict)->Checkpoint:
        raw=json.dumps(payload,sort_keys=True,separators=(",",":")); digest=hashlib.sha256(raw.encode()).hexdigest()
        self.cx.execute("INSERT OR IGNORE INTO checkpoints VALUES(?,?,?,?,?)",(episode_id,sequence,state,raw,digest)); self.cx.commit()
        return Checkpoint(episode_id,sequence,state,payload,digest)
    def latest(self,episode_id:str)->Checkpoint|None:
        row=self.cx.execute("SELECT seq,state,payload,digest FROM checkpoints WHERE episode=? ORDER BY seq DESC LIMIT 1",(episode_id,)).fetchone()
        if not row:return None
        seq,state,raw,digest=row; payload=json.loads(raw)
        if hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()!=digest: raise ValueError("checkpoint integrity failure")
        return Checkpoint(episode_id,seq,state,payload,digest)
