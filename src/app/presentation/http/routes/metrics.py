from fastapi import APIRouter, Response

from app.infrastructure.metrics.prometheus import render_latest

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus exposition. nginx does not expose it: scraped inside the network."""
    body, content_type = render_latest()
    return Response(body, media_type=content_type)
