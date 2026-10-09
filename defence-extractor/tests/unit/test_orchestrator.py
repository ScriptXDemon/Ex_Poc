from defence_extractor.orchestrator.workflows import PIPELINE_STAGES
from defence_extractor.pipeline.state import STAGES


def test_workflow_stage_list_in_sync():
    assert PIPELINE_STAGES == STAGES[:-1]
