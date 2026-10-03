"""Tasks and the agent interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..actions import Action
from ..context import ContextBundle


@dataclass
class Task:
    task_id: str
    user_id: str
    purpose: str
    text: str  # what the user said
    scope: dict = field(default_factory=dict)  # what the context request is scoped to
    params: dict = field(default_factory=dict)  # structured details (target_gb, device_id, ...)


class Agent(Protocol):
    name: str

    def act(self, task: Task, bundle: ContextBundle) -> list[Action]: ...
