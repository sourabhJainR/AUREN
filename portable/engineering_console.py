"""Machine-readable engineering console snapshot and control surface."""
from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True)
class ConsoleSnapshot:
    episode_id:str; phase:str; confidence:float; resource_lane:str; findings:int; evidence_count:int
@dataclass(frozen=True)
class ConsoleCommand:
    action:str
    def __post_init__(self):
        if self.action not in {"continue","stop","review","repair"}: raise ValueError("unsupported console action")
class EngineeringConsole:
    def snapshot(self,episode):
        return ConsoleSnapshot(episode.episode_id,episode.phase.value, float(getattr(episode,"metadata_confidence",0.0)), episode.resource_lane, len(episode.findings), len(episode.evidence_ids))
