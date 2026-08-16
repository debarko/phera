from __future__ import annotations

import pytest

from phera.modules.workflows.catalog import WORKFLOW_NODE_TYPES
from phera.modules.workflows.matching import event_matches_trigger


class TestWorkflowCatalog:
    def test_catalog_has_core_node_kinds(self):
        kinds = {entry["kind"] for entry in WORKFLOW_NODE_TYPES}
        assert {"trigger", "wait", "action", "filter", "branch"}.issubset(kinds)

    def test_catalog_entries_have_type_and_label(self):
        for entry in WORKFLOW_NODE_TYPES:
            assert entry.get("type")
            assert entry.get("label")


class TestEventMatchesTrigger:
    @pytest.mark.parametrize(
        ("event_type", "trigger_filter", "expected"),
        [
            ("form.submitted", {}, True),
            ("form.submitted", {"event_types": []}, True),
            ("form.submitted", {"event_types": ["form.submitted"]}, True),
            ("deal.created", {"event_types": ["form.submitted"]}, False),
            ("deal.stage_changed", {"event_types": ["deal.*"]}, True),
            ("deal.assigned", {"event_types": ["deal.*"]}, True),
            ("ticket.created", {"event_types": ["deal.*"]}, False),
        ],
    )
    def test_matching(self, event_type, trigger_filter, expected):
        assert event_matches_trigger(event_type, trigger_filter) is expected
