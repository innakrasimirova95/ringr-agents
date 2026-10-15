"""Entry point of the runtime: runs one call and reports how it ended."""

import asyncio
import logging
import time

import httpx

from agent_runtime.deps import Deps
from agent_runtime.session import CallOutcome, CallSession

logger = logging.getLogger(__name__)

RESULT_ATTEMPTS = 3


async def run_call(call_id: str, deps: Deps) -> CallOutcome:
    """Run one call, cut it at ``call_timeout_s`` and report the result to the API."""
    try:
        ctx = await deps.api.get_context(call_id)
        cfg = await deps.api.get_tenant_config(ctx.tenant_id)
    except httpx.HTTPError:
        logger.exception('Could not load call %s', call_id)
        return CallOutcome.ERROR

    session = CallSession(ctx, cfg, deps)
    started = time.monotonic()
    try:
        timeout_s = deps.config.call_timeout_s * deps.config.time_scale
        outcome = await asyncio.wait_for(session.run(), timeout=timeout_s)
    except TimeoutError:
        outcome = CallOutcome.TIMEOUT
    except Exception:
        logger.exception('Call %s failed', call_id)
        outcome = CallOutcome.ERROR
    duration_s = (time.monotonic() - started) / deps.config.time_scale

    await report_result(deps, call_id, outcome, duration_s, session.transcript)
    return outcome


async def report_result(
    deps: Deps, call_id: str, outcome: CallOutcome, duration_s: float, transcript: str
) -> None:
    """POST the result, retrying on 5xx or when the API does not answer in time."""
    for attempt in range(1, RESULT_ATTEMPTS + 1):
        try:
            response = await deps.api.post_result(
                call_id, outcome=outcome, duration_s=duration_s, transcript=transcript
            )
        except (httpx.TimeoutException, httpx.ConnectError):
            logger.warning('No answer reporting call %s', call_id)
        else:
            if response.status_code < 500:
                if response.is_error:
                    logger.error('API rejected call %s: %s', call_id, response.status_code)
                return
            logger.warning('API error %s reporting call %s', response.status_code, call_id)
        if attempt < RESULT_ATTEMPTS:
            await asyncio.sleep(0.2 * attempt)
    logger.error('Gave up reporting the result of call %s', call_id)
