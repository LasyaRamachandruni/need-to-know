"""HTTP API: the context service and the action gate as a service agents call.

    ntk serve        (NTK_WAREHOUSE=data/warehouse.duckdb)

    POST /v1/context          {user_id, purpose, scope}           -> context bundle
    POST /v1/actions/review   {request_id, action: {kind, params}} -> executed | needs_confirmation | blocked
    GET  /v1/audit?user_id=   recent context requests and action decisions
    GET  /v1/purposes         what each purpose may see and do

Agents identify themselves with the X-Agent-Id header, which is recorded in the
audit log. An action is reviewed against the bundle issued for its request_id,
so an agent can only act within a context it was actually given.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from . import policy as P
from .actions import Action, review
from .context import ContextBundle, ContextService

MAX_BUNDLES = 1000  # issued bundles kept for action review (oldest evicted)


class ContextRequest(BaseModel):
    user_id: str
    purpose: str
    scope: dict = Field(default_factory=dict)


class ActionBody(BaseModel):
    kind: str
    params: dict = Field(default_factory=dict)


class ReviewRequest(BaseModel):
    request_id: str
    action: ActionBody


def create_app(warehouse: str | None = None) -> FastAPI:
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state["svc"] = ContextService(warehouse or os.environ.get("NTK_WAREHOUSE", "data/warehouse.duckdb"))
        state["bundles"] = OrderedDict()
        state["decisions"] = []
        yield
        state["svc"].close()

    app = FastAPI(title="need-to-know", version="1.0", lifespan=lifespan)

    @app.get("/v1/purposes")
    def purposes():
        return {p.name: {"description": p.description, "fields": p.fields, "actions": list(p.actions)}
                for p in P.PURPOSES.values()}

    @app.post("/v1/context")
    def context(req: ContextRequest, x_agent_id: str = Header("unknown")):
        if req.purpose not in P.PURPOSES:
            raise HTTPException(422, f"unknown purpose {req.purpose!r}; see /v1/purposes")
        bundle: ContextBundle = state["svc"].get(req.user_id, req.purpose, req.scope, agent_id=x_agent_id)
        if not bundle.items:
            raise HTTPException(404, "no data for this user, purpose and scope")
        bundles = state["bundles"]
        bundles[bundle.request_id] = bundle
        while len(bundles) > MAX_BUNDLES:
            bundles.popitem(last=False)
        return bundle.to_json()

    @app.post("/v1/actions/review")
    def review_action(req: ReviewRequest, x_agent_id: str = Header("unknown")):
        bundle = state["bundles"].get(req.request_id)
        if bundle is None:
            raise HTTPException(404, "unknown or expired request_id: request context first")
        outcome = review(bundle, Action(req.action.kind, dict(req.action.params)))
        decision = {"request_id": req.request_id, "agent_id": x_agent_id, "user_id": bundle.user_id,
                    "kind": req.action.kind, "status": outcome.status, "reasons": outcome.reasons,
                    "changes": outcome.changes}
        state["decisions"].append(decision)
        return {**decision, "params": outcome.action.params}

    @app.get("/v1/audit")
    def audit(user_id: str, limit: int = Query(50, ge=1, le=500)):
        ctx = [a for a in state["svc"].audit if a["user_id"] == user_id][-limit:]
        dec = [d for d in state["decisions"] if d["user_id"] == user_id][-limit:]
        return {"context_requests": ctx, "action_decisions": dec}

    return app


app = create_app()
