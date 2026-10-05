"""Executive team and repository recipe extraction for AUREN.

Agents are skill-scoped workers inside one company-level work unit. They do not
own safety authority; the AutonomousCompany remains the durable coordinator.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .agent_capabilities import Skill, SkillRegistry
from .autonomous_company import AutonomousCompany, TrustScore

@dataclass(frozen=True)
class SkillAgent:
    name: str
    role: str
    skills: tuple[str, ...]
    execute: Callable[[str], tuple[str, str, tuple[str, ...]]]

@dataclass(frozen=True)
class Recipe:
    pattern: str
    recipe: str
    source: str
    confidence: float
    evidence: tuple[str, ...]

class RecipeMiner:
    """Extract reusable engineering recipes without copying source code."""

    _ACTION = re.compile(r"(?im)^\s*(?:[-*]\s*)?(?:must|never|always|when|if|then|before|after)\b.+$")
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _python_patterns(self, path: Path, text: str) -> list[Recipe]:
        try:
            tree=ast.parse(text,filename=str(path))
        except SyntaxError:
            return []
        recipes=[]
        for node in ast.walk(tree):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                doc=ast.get_docstring(node)
                if not doc: continue
                lines=[x.strip() for x in doc.splitlines() if x.strip()]
                actionable=[x for x in lines if any(k in x.lower() for k in ("must","never","always","fail closed","verify","rollback","evidence","retry"))]
                if actionable:
                    pattern=f"{type(node).__name__}:{node.name}"
                    recipe="; ".join(actionable[:5])
                    evidence=(str(path.relative_to(self.root)),)
                    recipes.append(Recipe(pattern,recipe,str(path.relative_to(self.root)),0.78,evidence))
        return recipes

    def extract(self, paths: Iterable[str | Path] | None = None, *, limit: int = 200) -> tuple[Recipe,...]:
        candidates=[self.root / p for p in paths] if paths is not None else list(self.root.rglob("*.py")) + list(self.root.rglob("*.md"))
        out=[]
        for path in candidates:
            if not path.is_file() or any(part in {".git",".venv","__pycache__","node_modules"} for part in path.parts):
                continue
            try: text=path.read_text(encoding="utf-8",errors="ignore")
            except OSError: continue
            if path.suffix==".py":
                out.extend(self._python_patterns(path,text))
            else:
                for line in self._ACTION.findall(text):
                    clean=" ".join(line.split())
                    if len(clean)<20: continue
                    digest=hashlib.sha256(clean.encode()).hexdigest()[:12]
                    out.append(Recipe(f"rule:{digest}",clean,str(path.relative_to(self.root)),0.65,(str(path.relative_to(self.root)),)))
            if len(out)>=limit: break
        unique={}
        for item in out:
            unique[(item.pattern,item.recipe)]=item
        return tuple(list(unique.values())[:limit])

class ExecutiveTeam:
    """Single-company execution model: planner, builder, verifier, reviewer, recovery and learner."""

    DEFAULT_ROLES=("planner","builder","verifier","reviewer","recovery","learner")
    def __init__(self, company: AutonomousCompany, registry: SkillRegistry | None = None) -> None:
        self.company=company
        self.skills=registry or SkillRegistry()
        self.agents: dict[str,SkillAgent]={}

    def register(self, agent: SkillAgent) -> None:
        if not agent.name.strip() or not agent.skills:
            raise ValueError("agent name and skills are required")
        available={skill.name for skill in self.skills.discover()}\n        missing=[name for name in agent.skills if name not in available]
        if missing:
            raise KeyError(f"unregistered skills: {missing}")
        self.agents[agent.name]=agent

    def discover(self, goal: str) -> tuple[SkillAgent,...]:
        ranked={s.name:i for i,s in enumerate(self.skills.discover(goal))}
        return tuple(sorted(self.agents.values(),key=lambda a:min((ranked.get(s,9999) for s in a.skills),default=9999)))

    def run_agent(self, wid: str, agent_name: str, context: str) -> tuple[str,str,tuple[str,...]]:
        agent=self.agents[agent_name]
        result=agent.execute(context)
        self.company.record(wid,"agent",result[0],f"{agent.name}: {result[1]}",result[2])
        return result

    def share_trust(self, wid: str) -> str:
        score=self.company.trust(wid)
        return json.dumps(score.as_dict(),sort_keys=True,indent=2)

    def mine_and_record_recipes(self, wid: str, root: str | Path, paths: Iterable[str | Path] | None = None) -> tuple[Recipe,...]:
        recipes=RecipeMiner(root).extract(paths)
        for item in recipes:
            self.company.extract_recipe(wid,item.pattern,recipe=item.recipe,evidence=item.evidence,confidence=item.confidence)
        return recipes

__all__=["ExecutiveTeam","RecipeMiner","Recipe","SkillAgent"]
