"""A role-change suggestion used by producer, staging and template scenarios."""

PROPOSAL = {"kind": "role-change", "what": "Reduce worker context", "why": "Repeated expensive reads",
            "evidence": "Report: worker read the entire tree three times"}
