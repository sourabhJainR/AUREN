"""Production readiness gates for persistence, observability and dependencies."""
from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True)
class ProductionReadiness:
    structured_logging:bool; db_integrity:bool; graceful_shutdown:bool; dependency_scan:bool; reproducible_build:bool
    def passed(self)->bool:return all((self.structured_logging,self.db_integrity,self.graceful_shutdown,self.dependency_scan,self.reproducible_build))
    def missing(self)->tuple[str,...]:
        return tuple(k for k,v in self.__dict__.items() if not v)
