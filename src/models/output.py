from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from pydantic.alias_generators import to_camel

SCHEMA_VERSION = "1.2"
FULL_RECORD_KEY = "FULL_RESULT"
UNVERIFIED_LIST_LIMIT = 25
OFFICIAL_TEXT_LIMIT = 1000
ResultStatus = Literal["success", "partial", "invalid_input", "source_unavailable"]
SourceStatus = Literal["success", "partial", "unavailable", "not_queried", "not_published_for_river"]
DISCLAIMER = (
    "Official USACE information as retrieved; not navigational advice and not a passage, safety or "
    "clearance determination. Consult official current navigation information, the lockmaster and "
    "applicable operating procedures."
)


class Out(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True)


class Source(Out):
    agency: str = "U.S. Army Corps of Engineers"
    system: str
    url: str


class SourceFailure(Out):
    source: str
    state: str
    detail: str


class SourceCoverage(Out):
    status: SourceStatus
    checked_at: str | None = None
    from_cache: bool = False
    detail: str | None = None


class NoticeGeometryCoverage(Out):
    status: Literal["complete", "partial", "unavailable", "not_queried"]
    scope: Literal["all_usace_districts"] = Field(
        default="all_usace_districts",
        description="Published geometry is checked for every active NTNI notice, from every USACE district.",
    )
    active_notices_considered: int
    geometry_checked: int = Field(description="Notices whose published geometry was tested against the corridor.")
    no_geometry_published: int = Field(
        description="Active notices with no published geometry (listed and not listed); never counted as non-matches."
    )
    geometry_not_retrieved: list[int] = Field(description="Control numbers whose geometry could not be retrieved.")
    unverified_listing_margin_nm: float
    unverified_listing_districts: list[str] | None = Field(
        description="USACE districts within the margin of the corridor whose geometry-less notices are listed in "
        "unverifiedLocationNotices; null when district boundaries could not be checked (all are listed)."
    )
    no_geometry_outside_listing_districts: int = Field(
        description="Geometry-less active notices from districts away from the route; not listed."
    )


class Coverage(Out):
    ntni: SourceCoverage
    ntni_upcoming: SourceCoverage
    ntni_geometry: NoticeGeometryCoverage
    corps_locks: SourceCoverage
    lpms: SourceCoverage
    complete_for_requested_sources: bool
    source_failures: list[SourceFailure] = []
    coverage_note: str


class RouteRelationship(Out):
    intersects_route: bool
    intersects_corridor: bool
    route_position: float = Field(description="0 = route start, 1 = route end; first contact with the corridor.")
    route_distance_nm: float = Field(description="Distance along the route to first contact.")
    minimum_distance_nm: float


class EffectiveWindow(Out):
    start: str | None
    end: str | None


class Notice(Out):
    constraint_type: Literal["navigation_notice"] = "navigation_notice"
    notice_id: int
    amendment_number: int | None = None
    title: str | None
    category: str | None = None
    temporal_status: str
    status_basis: str
    effective_window: EffectiveWindow
    end_date_status: Literal["provided", "not_provided"]
    issue_date: str | None
    age_days: int | None
    official_text_says_until_further_notice: bool
    temporal_note: str | None = None
    matched_by: Literal["geometry"] = "geometry"
    spatially_verified: Literal[True] = True
    relationship_to_route: RouteRelationship
    waterway: str | None
    district: str | None
    official_text: str | None = Field(
        description=f"NTNI remarks, cut at about {OFFICIAL_TEXT_LIMIT} characters when longer; the full text is in "
        f"the run's key-value store record {FULL_RECORD_KEY}."
    )
    official_text_truncated: bool = False
    official_text_length: int | None = Field(default=None, description="Length of the full official text.")
    attachments: list[dict[str, Any]] = []
    related_lock_ids: list[str] = []
    source: Source


class UnverifiedNoticeBrief(Out):
    """Agent-facing entry for an active notice with no published geometry; never a confirmed route match."""

    notice_id: int
    amendment_number: int | None = None
    title: str | None
    temporal_status: str
    status_basis: str
    effective_window: EffectiveWindow
    end_date_status: Literal["provided", "not_provided"]
    issue_date: str | None
    age_days: int | None
    official_text_says_until_further_notice: bool
    location_status: Literal["river_mile_text_only", "unlocated"]
    matched_by: None = None
    spatially_verified: Literal[False] = False
    river_mile_text: list[str] = []
    parsed_river_miles: list[dict[str, float]] = []
    waterway: str | None
    district: str | None
    notice_url: str = Field(description="Official NTNI per-notice record for this notice.")


class UnverifiedLocationNotice(UnverifiedNoticeBrief):
    """Full entry, stored in the run's key-value store record FULL_RESULT."""

    official_text: str | None
    source: Source


class UnverifiedNoticeListing(Out):
    total: int = Field(description="All nearby active notices with no published geometry.")
    listed: int = Field(description="How many of them are itemised in unverifiedLocationNotices.")
    truncated: bool
    order: str
    notice_ids_by_district: dict[str, list[int]] = Field(
        description="Every one of the `total` notices, by USACE district; none are omitted here."
    )
    full_record_key: str = Field(
        description="Key-value store record of this run holding every notice in full, with official text."
    )
    source: Source


class Queue(Out):
    vessels_pending: int | None = Field(description="LPMS totalPendingArrivals as reported.")
    vessels_locking: int | None = None
    reported_average_delay_minutes: float | None = Field(
        description="LPMS average4HourDelay as reported (last 4 hours). Not a prediction."
    )
    reported_at_local: str | None = Field(description="LPMS entryDatetime; the source states no timezone.")


class LockConditions(Out):
    hours_of_operation: str | None
    queue: Queue
    lockages_last_24h: dict[str, int | None]
    active_stall_stoppages: Any = None
    lpms_ntni_links: Any = None
    upper_gauge_ft: str | None = None
    lower_gauge_ft: str | None = None
    freshness: Literal["current", "possibly_stale", "stale"]
    age_minutes_range: list[int]
    freshness_note: str
    source: Source


class Lock(Out):
    constraint_type: Literal["lock"] = "lock"
    lock_id: str = Field(description="RIVERCD-LOCKCD from the USACE Locks layer.")
    name: str | None = Field(
        description="LPMS lockName where LPMS reports this lock, otherwise PMSNAME from the USACE Locks layer."
    )
    river_code: str
    lock_code: str
    river: str | None
    river_mile: float | None
    waterway_project: str | None
    district: str | None
    ndc_codes: list[str]
    chambers: list[dict[str, Any]]
    coordinates: dict[str, float]
    route_position: float
    route_distance_nm: float
    distance_from_route_nm: float
    related_notice_ids: list[int] = []
    operating_conditions: LockConditions | None = None
    operating_conditions_status: Literal[
        "reported", "not_reported_by_lpms", "lpms_unavailable", "not_published_for_river"
    ] = Field(
        description=(
            "not_published_for_river: LPMS answered 'Data Unavailable' for this river, so no current lock "
            "operating conditions are published; this is not a source failure, but conditions are unknown."
        )
    )
    source: Source


class Summary(Out):
    notice_count: int
    lock_count: int
    locks_with_reported_constraints: int
    locks_with_operating_conditions: int
    confirmed_match_count: int = Field(description="Geometry-confirmed notices plus locks in the corridor.")
    unverified_notice_count: int
    unverified_notices_listed: int
    unverified_list_truncated: bool
    expired_notices_excluded: int
    notices_by_temporal_status: dict[str, int]


class Billing(Out):
    billable: bool
    event_name: str | None = None
    reason: str


class Query(Out):
    type: Literal["route"] = "route"
    corridor_nm: float
    route_length_nm: float | None = None
    point_count: int | None = None


class Result(Out):
    schema_version: str = SCHEMA_VERSION
    status: ResultStatus
    summary: Summary | None = None
    billing: Billing
    coverage: Coverage | None = None
    query: Query
    checked_at: str
    at_time: str
    constraints: list[dict[str, Any]] = []
    locks: list[Lock] = []
    notices: list[Notice] = []
    unverified_location_notices: list[UnverifiedNoticeBrief] = []
    unverified_notice_listing: UnverifiedNoticeListing | None = None
    full_record_key: str | None = Field(
        default=None,
        description="Key-value store record of this run with the uncut notice texts and every unverified notice.",
    )
    errors: list[str] = []
    disclaimer: str = DISCLAIMER
    _full_unverified: list[UnverifiedLocationNotice] = PrivateAttr(default_factory=list)
    _full_texts: dict[int, str] = PrivateAttr(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    def to_full_record(self) -> dict[str, Any]:
        rec = self.to_record()
        rec["recordType"] = "full"
        rec["unverifiedLocationNotices"] = [u.model_dump(mode="json", by_alias=True) for u in self._full_unverified]
        for n in rec["notices"]:
            if n["noticeId"] in self._full_texts:
                n["officialText"] = self._full_texts[n["noticeId"]]
                n["officialTextTruncated"] = False
        return rec
