"""Liveness and readiness probes.

``/health`` says the process is up and never touches a dependency, so an orchestrator does
not restart the API because the database is briefly unreachable. ``/health/ready`` checks
both database connections and is what a load balancer should use to route traffic.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from finqon.api.dependencies import ResourcesDep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/cache")
async def cache_status(resources: ResourcesDep) -> dict[str, object]:
    """Semantic cache mode, store reachability, hit rate and counters for this replica.

    Aggregates only. It never returns questions, answers or user ids. Always 200: a cache
    that is down must not fail a probe, because chat works without it.
    """
    return await resources.cache_status()


@router.get("/health/ready", response_model=None)
async def readiness(resources: ResourcesDep) -> JSONResponse | dict[str, object]:
    checks = await resources.check_ready()
    if all(checks.values()):
        return {"status": "ready", "checks": checks}
    return JSONResponse(status_code=503, content={"status": "unavailable", "checks": checks})
