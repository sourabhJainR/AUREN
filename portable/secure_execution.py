"""Disposable execution policy and host-isolation contract for generated code."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

class TrustClass(str, Enum):
    TRUSTED="trusted"; SEMI_TRUSTED="semi-trusted"; UNTRUSTED="untrusted"; HOSTILE="hostile"

@dataclass(frozen=True)
class ExecutionLimits:
    cpu_seconds:int=60; memory_mb:int=1024; processes:int=32; network:bool=False
    readonly_root:bool=True; ephemeral_workspace:bool=True
    def __post_init__(self):
        if min(self.cpu_seconds,self.memory_mb,self.processes)<1: raise ValueError("limits must be positive")

@dataclass(frozen=True)
class IsolationContract:
    trust:TrustClass
    limits:ExecutionLimits
    require_container:bool
    require_vm:bool
    credentials_allowed:bool=False
    def __post_init__(self):
        if self.trust in {TrustClass.UNTRUSTED,TrustClass.HOSTILE} and not self.require_container and not self.require_vm:
            raise ValueError("untrusted execution requires container or VM isolation")
        if self.trust is TrustClass.HOSTILE and self.credentials_allowed:
            raise ValueError("hostile execution cannot receive credentials")

def contract_for(trust:TrustClass, *, vm:bool=False, network:bool=False)->IsolationContract:
    return IsolationContract(trust, ExecutionLimits(network=network),
        require_container=trust in {TrustClass.UNTRUSTED,TrustClass.SEMI_TRUSTED} and not vm,
        require_vm=vm or trust is TrustClass.HOSTILE)
