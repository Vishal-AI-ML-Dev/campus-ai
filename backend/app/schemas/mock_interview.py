"""Pydantic schemas for the AI Mock Interview module (feature-flagged).

Kept in its own module (not re-exported from ``app.schemas``) so the feature
stays isolated from existing contracts.
"""

from pydantic import BaseModel


class MockInterviewStatusOut(BaseModel):
    """Whether the AI Mock Interview feature is available to the caller."""

    enabled: bool


class MockInterviewCatalogOut(BaseModel):
    """Static choices the interview setup screen offers.

    Only lists what the module is designed to support; individual modes are
    switched on in later phases.
    """

    interview_types: list[str]
    modes: list[str]
    difficulties: list[str]
    languages: list[str]
    durations_minutes: list[int]
