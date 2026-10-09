"""Two-layer defence ontology: canonical (+ candidate) parameters, dynamic properties kept separately."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

SEED = Path(__file__).with_name("seed_v1.yaml")


class OntologyParameter(BaseModel):
    id: str
    name: str
    family: str = "other_technical"
    dimension: str | None = None
    type: str = "text"
    multi: bool = False
    classes: list[str] = Field(default_factory=lambda: ["*"])
    legacy: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    anti: list[str] = Field(default_factory=list)
    description: str = ""
    status: str = "canonical"
    relation: str | None = None

    def applies_to(self, categories: list[str]) -> bool:
        return "*" in self.classes or not categories or any(c in self.classes for c in categories)

    def prompt_line(self) -> str:
        dim = f" [{self.dimension}]" if self.dimension else ""
        st = " (candidate)" if self.status == "candidate" else ""
        return f"{self.id}{st}: {self.name}{dim} — {self.description}"


_PAREN = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_NON = re.compile(r"[^a-z0-9À-ɏЀ-ӿ ]+")


def normalize_label(label: str | None) -> str:
    if not label:
        return ""
    s = label.lower().replace("_", " ")
    s = _PAREN.sub(" ", s)
    s = _NON.sub(" ", s)
    s = re.sub(r"\b(?:max|maximum)\b", "max", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


class Ontology:
    def __init__(self, data: dict):
        self.version: str = str(data.get("version", "0"))
        self.families: list[str] = data.get("families", [])
        self.taxonomy: dict[str, list[str]] = data.get("taxonomy", {})
        self.core: dict[str, list[str]] = data.get("core_parameters", {})
        self.predicates: dict[str, str] = data.get("relation_predicates", {})
        self.params: dict[str, OntologyParameter] = {}
        for raw in data.get("parameters", []):
            raw = dict(raw)
            if raw.get("dimension") and "type" not in raw:
                raw["type"] = "number"
            p = OntologyParameter(**raw)
            self.params[p.id] = p
        self.alias_index: dict[str, list[str]] = {}
        for p in self.params.values():
            for a in [p.id, p.name, *p.aliases, *p.legacy]:
                k = normalize_label(a)
                if k:
                    lst = self.alias_index.setdefault(k, [])
                    if p.id not in lst:
                        lst.append(p.id)
        self.categories = {c: d for d, cats in self.taxonomy.items() for c in cats}

    # -------------------------------------------------------------------------------------------
    def get(self, pid: str | None) -> OntologyParameter | None:
        return self.params.get(pid or "")

    def lookup_label(self, label: str | None) -> list[OntologyParameter]:
        k = normalize_label(label)
        return [self.params[i] for i in self.alias_index.get(k, [])]

    def for_categories(self, categories: list[str]) -> list[OntologyParameter]:
        return [p for p in self.params.values() if p.applies_to(categories)]

    def core_for(self, categories: list[str]) -> list[str]:
        out: list[str] = []
        for c in categories:
            for pid in self.core.get(c, []):
                if pid not in out and pid in self.params:
                    out.append(pid)
        return out

    def taxonomy_prompt(self) -> str:
        return "\n".join(f"{d}: {', '.join(cats)}" for d, cats in self.taxonomy.items())

    def parameter_catalogue(self, categories: list[str] | None = None) -> str:
        params = self.for_categories(categories or [])
        return "\n".join(p.prompt_line() for p in params)


EXTENSIONS = Path(__file__).parent / "extensions"


def apply_extension(data: dict, ext: dict) -> dict:
    """Merge an ontology extension (curated promotions) into the seed data: new parameters, alias additions and
    removals, core-parameter additions. An extension never redefines an existing parameter."""
    params = [dict(p) for p in data.get("parameters", [])]
    by_id = {p["id"]: p for p in params}
    for pid, aliases in (ext.get("alias_additions") or {}).items():
        if pid in by_id:
            by_id[pid]["aliases"] = list(dict.fromkeys([*by_id[pid].get("aliases", []), *aliases]))
    for pid, aliases in (ext.get("alias_removals") or {}).items():
        if pid in by_id:
            drop = {a.lower() for a in aliases}
            by_id[pid]["aliases"] = [a for a in by_id[pid].get("aliases", []) if a.lower() not in drop]
    for p in ext.get("parameters") or []:
        if p["id"] in by_id:
            raise ValueError(f"ontology extension redefines existing parameter {p['id']}")
        by_id[p["id"]] = dict(p)
        params.append(by_id[p["id"]])
    core = {k: list(v) for k, v in (data.get("core_parameters") or {}).items()}
    for cat, ids in (ext.get("core_parameter_additions") or {}).items():
        core[cat] = list(dict.fromkeys([*core.get(cat, []), *ids]))
    return {**data, "parameters": params, "core_parameters": core, "version": str(ext.get("version") or data.get("version"))}


@lru_cache(maxsize=4)
def load_ontology(path: str | None = None, extensions: bool = True) -> Ontology:
    """The seed catalogue plus every curated extension in ontology/extensions/ (applied in file-name order).
    Cached: the catalogue is read once per process."""
    data = yaml.safe_load(Path(path or SEED).read_text(encoding="utf-8"))
    if extensions and path is None:
        for ext in sorted(EXTENSIONS.glob("*.yaml")):
            data = apply_extension(data, yaml.safe_load(ext.read_text(encoding="utf-8")) or {})
    return Ontology(data)
