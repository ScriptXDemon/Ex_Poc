"""Inspect a document's pipeline state: page map, entities, facts (with verification), ledger, timings.

usage: python scripts/inspect_doc.py <run_id> <doc_id> [--facts N]
"""

from __future__ import annotations

import argparse
import collections

from defence_extractor.config import get_settings
from defence_extractor.pipeline.render import build_chunks
from defence_extractor.pipeline.state import DocState


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("doc_id")
    ap.add_argument("--facts", type=int, default=200)
    a = ap.parse_args()
    s = get_settings()
    st = DocState.load(s.runs_dir, a.run_id, a.doc_id)
    if st is None:
        print("no state")
        return
    doc = st.ingest.doc
    print(f"== {doc.doc_id} {doc.title!r} lang={st.ingest.profile.language} status={st.status} done={st.done}")
    print("errors:", st.errors)
    if st.page_map:
        pm = st.page_map
        print(f"page_type={pm.page_type} scope={pm.page_scope} subjects={pm.primary_subject_candidates} flags={pm.risk_flags}")
        roles = collections.Counter(x.role.value for x in pm.sections)
        print("section roles:", dict(roles))
        for x in pm.sections[:60]:
            sec = doc.section_map().get(x.section_id)
            print(f"   {x.section_id:5} {x.role.value:22} {x.spec_density:6} {(sec.heading if sec else '')!r:.60} subj={x.subjects[:3]}")
    chunks = build_chunks(doc, st.page_map, s.section_chunk_tokens)
    print(f"chunks: {len(chunks)} -> " + ", ".join(f"{c.chunk_id}({len(c.block_ids)} blocks,{c.tokens}t,{c.roles})" for c in chunks[:12]))
    if st.kg:
        print("entities:")
        for e in st.kg.entities:
            print(f"   {e.entity_id:4} {e.name[:40]:40} {e.entity_type.value:12} {e.role.value:16} parent={e.parent_entity_id} "
                  f"classes={[c.category for c in e.product_classes]} mentions={len(e.mention_block_ids)}")
        print("focal:", st.kg.focal_entity_ids, "| relations:", len(st.kg.relations), "| markers:", len(st.kg.markers))
    vc = collections.Counter(f.verification.value for f in st.facts)
    rc = collections.Counter(f.rejection_reason for f in st.facts if f.rejection_reason)
    print(f"facts: {len(st.facts)} verification={dict(vc)} reasons={dict(rc)}")
    for f in st.facts[: a.facts]:
        print(f"   {f.fact_id} {f.method[:6]:6} {f.verification.value[:10]:10} owner={f.owner_entity_id} subj={f.subject_entity_id} "
              f"{f.fact_class.value[:14]:14} {(f.parameter.id if f.parameter else f.parameter_hint)!s:28.28} "
              f"[{f.source_parameter or ''}] = {f.value_text[:60]!r} ev={[e.block_id for e in f.evidence]} "
              f"inv={f.inventory_item_ids[:3]} why={f.rejection_reason or ''} anom={f.anomalies[:2]} note={(f.verification_note or '')[:60]}")
    for t in st.timings:
        print(f"   {t.stage:12} {t.seconds:6.1f}s calls={t.usage.calls} in={t.usage.prompt_tokens} out={t.usage.completion_tokens} "
              f"reason={t.usage.reasoning_tokens} cached={t.usage.cached_tokens} ${t.usage.cost_usd:.5f}")


if __name__ == "__main__":
    main()
