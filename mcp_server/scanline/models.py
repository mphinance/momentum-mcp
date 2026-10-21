"""Pydantic request and response models for the screen endpoint."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


# Hard ceiling on rows per request, shared by the HTTP API and the MCP tools.
MAX_LIMIT = 1000


class Filter(BaseModel):
    """A single filter clause."""

    field: str
    op: str = ">"
    value: Any = None


class Computed(BaseModel):
    """A user-defined computed column."""

    id: str
    expr: str


class Stat(BaseModel):
    """An in-result statistic request."""

    fn: Literal["zscore", "pctrank", "rank", "norm", "madzscore", "winsor", "decile"]
    field: str


class FactorWeight(BaseModel):
    """One weighted field in a composite factor score."""

    field: str
    weight: float = 1.0
    dir: Literal["high", "low"] = "high"


class Factor(BaseModel):
    """Composite factor scoring request."""

    weights: list[FactorWeight] = Field(default_factory=list)


class SortKey(BaseModel):
    """A single sort key."""

    field: str
    dir: Literal["asc", "desc"] = "desc"


class ScreenRequest(BaseModel):
    """Body of POST /api/screen."""

    market: str = "america"
    columns: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    match: Literal["all", "any"] = "all"
    computed: list[Computed] = Field(default_factory=list)
    stats: list[Stat] = Field(default_factory=list)
    factor: Factor | None = None
    sort: list[SortKey] = Field(default_factory=list)
    limit: Annotated[int, Field(ge=1, le=MAX_LIMIT)] = 150
    offset: Annotated[int, Field(ge=0)] = 0


class ScreenMeta(BaseModel):
    """Metadata about how a screen was served."""

    cached: bool = False
    ms: int = 0
    market: str = "america"
    error: str | None = None
    # Short stable machine code and a one-line detail, set whenever error is.
    error_code: str | None = None
    detail: str | None = None
    # "delayed" | "realtime" | "cookie_rejected". Never carries the cookie.
    mode: str = "delayed"
    warnings: list[str] = Field(default_factory=list)


class ScreenResponse(BaseModel):
    """Response of POST /api/screen."""

    count: int = 0
    rows: list[dict] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    meta: ScreenMeta = Field(default_factory=ScreenMeta)
