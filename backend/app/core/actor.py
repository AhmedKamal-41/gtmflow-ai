"""Phase 12: the authenticated actor of the current request or job.

Set by the auth dependency (app/api/auth.py) for API requests and by the job
runner for background work; read wherever an actor is recorded (review,
revision, seller activation and delivery-resolution labels) and stamped on
every WorkflowEvent by a before-insert hook, so the audit trail records who
did what without each call site having to remember.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event


@dataclass(frozen=True)
class Actor:
    label: str          # "user:<username>", "job:user:<username>", "cli:<name>", ...
    user_id: str | None = None
    role: str | None = None


SYSTEM = Actor(label="system")
_current: ContextVar[Actor | None] = ContextVar("gtmflow_actor", default=None)


def set_actor(actor: Actor | None):
    return _current.set(actor)


def reset_actor(token) -> None:
    _current.reset(token)


def current_actor() -> Actor:
    return _current.get() or SYSTEM


def actor_label() -> str:
    return current_actor().label[:64]


def _stamp(mapper: Any, connection: Any, target: Any) -> None:
    actor = current_actor()
    data = dict(target.event_data or {})
    # Older delivery call sites use "actor" for the dispatch path. Keep
    # that useful context, but the authenticated identity is authoritative.
    if data.get("actor") and data["actor"] != actor.label:
        data.setdefault("action_source", data["actor"])
    data["actor"] = actor.label
    data.pop("actor_user_id", None)
    if actor.user_id is not None:
        data["actor_user_id"] = actor.user_id
    target.event_data = data


def install_event_hook() -> None:
    from app.models.workflow_event import WorkflowEvent

    if not event.contains(WorkflowEvent, "before_insert", _stamp):
        event.listen(WorkflowEvent, "before_insert", _stamp)
