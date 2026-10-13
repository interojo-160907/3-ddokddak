"""Whole-job ceilings, distinct from an individual HTTP response timeout."""


def collection_limit_minutes(source: str) -> int:
    # APS reads status, Backward, Plan, Arrange and status again in sequence.
    # A full refresh then runs the remaining three sources in sequence.
    return {"all": 60, "aps": 15, "bom": 10, "production": 15, "live": 20}.get(source, 15)
