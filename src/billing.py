from __future__ import annotations

from .models.output import Billing, ResultStatus

EVENT_NAME = "waterway-route-check"


def billing_decision(status: ResultStatus, core_sources_ok: bool, matched: int) -> Billing:
    """Charge for completed route computation; never for invalid input or missing core sources.

    Core sources are the NTNI active-notice list and the USACE Locks layer. A partial result is charged
    only when both core sources were checked and at least one notice or lock matched the route.
    """
    if status == "success":
        return Billing(billable=True, event_name=EVENT_NAME, reason="All requested sources checked.")
    if status == "partial" and core_sources_ok and matched > 0:
        return Billing(
            billable=True,
            event_name=EVENT_NAME,
            reason="Coverage incomplete, but route constraints were found in the sources checked.",
        )
    if status == "partial":
        return Billing(
            billable=False, reason="Not charged: incomplete coverage without route matches from core sources."
        )
    return Billing(billable=False, reason=f"Not charged: status {status}.")
