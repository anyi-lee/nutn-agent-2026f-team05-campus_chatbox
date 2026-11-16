"""Offline scripted model replies; never imported by the production app."""
from campus_mas import ModelReply, TOOLS


class TestRoles:
    configured = True
    provider = "test_provider"
    model = "scripted-v1"
    evidence_mode = "offline"

    def __init__(self, override=None):
        self.calls = []
        self.override = override

    def step(self, role, stage, context, choices):
        self.calls.append((role, stage, context))
        if self.override:
            value = self.override(role, stage, context)
            if value is not None:
                return ModelReply(value)
        if role == "coordinator":
            return ModelReply({"action": "faculty_first"})
        return ModelReply({"tool": TOOLS[role], "arguments": context["target"]})
