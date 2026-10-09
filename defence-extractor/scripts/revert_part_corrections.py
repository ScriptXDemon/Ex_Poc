"""One-off repair for runs made before the A12 rules of 2026-10-09: the verifier's owner correction moved values
(a) of a part (controller, battery, camera...) to its parent product on the strength of the section label alone, or
(b) to a product the product map had only inferred (continuation / lead mention). Put them back on the extractor's
subject and re-assemble the affected documents. No LLM calls.

    python scripts/revert_part_corrections.py <run_id> [--dry-run]
"""

from __future__ import annotations

import asyncio
import sys

from defence_extractor.config import get_settings
from defence_extractor.contracts.common import VerificationStatus
from defence_extractor.pipeline.runner import assemble, build_runtime
from defence_extractor.pipeline.state import DocState


async def main(run_id: str, dry: bool) -> None:
    s = get_settings()
    rt = build_runtime(s, run_id)
    total = 0
    for d in sorted((s.runs_dir / run_id / "docs").iterdir()):
        st = DocState.load(s.runs_dir, run_id, d.name)
        if st is None or st.kg is None or not (d / "result.json").exists():
            continue
        emap = st.kg.entity_map()
        n = 0
        for f in st.facts:
            subj = emap.get(f.subject_entity_id or "")
            note = f.attribution_note or ""
            weak = "section:continuation" in note or "section:lead_mention" in note or "section:single_product" in note
            if ("owner_corrected" in f.anomalies and "section:" in note and subj is not None and subj.entity_id != f.owner_entity_id
                    and (subj.parent_entity_id == f.owner_entity_id or weak)):
                verifier_owner = f.owner_entity_id
                f.owner_entity_id = subj.entity_id
                f.anomalies = [a for a in f.anomalies if a != "owner_corrected"]
                if subj.parent_entity_id == verifier_owner and not weak:  # a part inside its product's section: keep it
                    f.anomalies.append("verifier_suggests_parent")
                    f.attribution_note = "kept on the part: the section label alone does not move it to the product"
                else:  # the verifier disagreed and only an inferred owner backed it: unresolved, as A12 now decides
                    f.verification, f.rejection_reason = VerificationStatus.unresolved, "belongs_to_other_entity"
                    f.alternative_owner_ids = list(dict.fromkeys([verifier_owner, *f.alternative_owner_ids]))
                    f.attribution_note = "verifier disagreed; the product map's owner was only inferred"
                n += 1
        total += n
        if n:
            print(f"{d.name}: {n} values put back on their part")
            if not dry:
                st.save(s.runs_dir)
                async with rt.gw:
                    await assemble(rt, st, s.runs_dir)
    print(f"total {total}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], "--dry-run" in sys.argv))
