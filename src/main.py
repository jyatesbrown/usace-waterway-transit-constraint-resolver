"""Apify Actor entry point: one user-supplied inland-waterway route in, one record out."""

from __future__ import annotations

from datetime import UTC, datetime

from apify import Actor, Event
from pydantic import ValidationError

from .models.input import ActorInput
from .resolver.run import invalid_input, iso, run_query
from .sources.cache import STORE_NAME, KeyValueCache

STATE_KEY = "QUERY_STATE"


def _input_error(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())


async def _on_aborting(_event_data: object) -> None:
    Actor.log.info("Run is aborting; exiting without charging.")
    await Actor.exit()


async def main() -> None:
    async with Actor:
        Actor.on(Event.ABORTING, _on_aborting)
        store = await Actor.open_key_value_store()
        state = await store.get_value(STATE_KEY) or {}
        if state.get("completed"):
            Actor.log.info("Query already completed in this run; not repeating it or charging again.")
            return
        try:
            actor_input = ActorInput.model_validate(await Actor.get_input() or {})
        except ValidationError as exc:
            await Actor.push_data(invalid_input(_input_error(exc), iso(datetime.now(UTC))).to_record())
            await store.set_value(STATE_KEY, {"completed": True, "charged": False})
            await Actor.set_status_message("invalid_input (not charged)")
            return
        try:
            cache = KeyValueCache(await Actor.open_key_value_store(name=STORE_NAME))
        except Exception:
            cache = None
        outcome = await run_query(actor_input, cache=cache)
        result = outcome.result
        await Actor.push_data(result.to_record())
        await store.set_value(STATE_KEY, {"completed": True, "charged": False})
        Actor.log.info("Route check %s in %.0f ms", result.status, outcome.elapsed_ms)
        if result.billing.billable and result.billing.event_name:
            await Actor.charge(event_name=result.billing.event_name)
            await store.set_value(STATE_KEY, {"completed": True, "charged": True})
        s = result.summary
        await Actor.set_status_message(
            f"{result.status}: {s.notice_count if s else 0} notices, {s.lock_count if s else 0} locks; "
            f"billable={result.billing.billable}"
        )
