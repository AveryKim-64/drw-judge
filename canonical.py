"""Canonical contract schema for cross-venue matching.

See docs/project_brief.md for design rationale. Short version:

- `anchor`      = WHAT is being measured (subject + jurisdiction)
- `occurrence`  = WHEN it is measured (point / interval / cumulative / sequence).
                  This was the field that distinguished Polymarket's annual-
                  cumulative Fed question from Kalshi's per-meeting decision
                  tree during the 2026-04-21 test pull.
- `measurement` = HOW yes/no is determined (metric + threshold + whether
                  off-schedule events count).
- `shape`       = outcome structure (binary / scalar_tree / categorical)
                  plus the MECE group sibling markets share.
- `resolution`  = authority + cutoffs.
- `raw`         = audit trail / embedding fallback.

The match engine emits one of the `MatchLabel` values for each cross-venue pair.

Stdlib only (dataclasses + enum + json) so this module imports without pip.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import List, Optional, Union


class SubjectType(str, Enum):
    RATE = "rate"
    PRICE = "price"
    WINNER = "winner"
    COUNT = "count"
    OUTCOME = "outcome"
    OCCURRENCE = "occurrence"


class OccurrenceType(str, Enum):
    POINT = "point"
    INTERVAL = "interval"
    CUMULATIVE = "cumulative"
    SEQUENCE = "sequence"


class ThresholdOp(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    BETWEEN = "between"
    IS = "is"


class OutcomeType(str, Enum):
    BINARY = "binary"
    SCALAR_TREE = "scalar_tree"
    CATEGORICAL = "categorical"


class MatchLabel(str, Enum):
    EQUIVALENT = "equivalent"
    SUBSET = "subset"
    SUPERSET = "superset"
    SYNTHETIC = "synthetic"
    UNRELATED = "unrelated"


@dataclass
class Source:
    venue: str
    id: str
    url: str


@dataclass
class Anchor:
    subject: str
    subject_type: SubjectType
    jurisdiction: Optional[str] = None


@dataclass
class Interval:
    start: str
    end: str


@dataclass
class Occurrence:
    type: OccurrenceType
    point_time: Optional[str] = None
    interval: Optional[Interval] = None
    sequence: Optional[List["Occurrence"]] = None


@dataclass
class Threshold:
    op: ThresholdOp
    value: Union[float, str, List[float]]


@dataclass
class Measurement:
    metric: str
    threshold: Threshold
    unit: Optional[str] = None
    includes_unscheduled: bool = False


@dataclass
class Shape:
    outcome_type: OutcomeType
    outcomes: List[str]
    mece_group: Optional[str] = None


@dataclass
class Resolution:
    authority: str
    trading_cutoff: str
    evaluation_time: str
    data_source: Optional[str] = None


@dataclass
class Raw:
    title: str = ""
    description: str = ""
    rules: str = ""


@dataclass
class CanonicalContract:
    source: Source
    anchor: Anchor
    occurrence: Occurrence
    measurement: Measurement
    shape: Shape
    resolution: Resolution
    raw: Raw = field(default_factory=Raw)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=_serialize)


def _serialize(obj):
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"Cannot serialize {type(obj).__name__}")


if __name__ == "__main__":
    # Smoke test: build Polymarket 616906 (the Fed-rates test case from the brief).
    example = CanonicalContract(
        source=Source(
            venue="polymarket",
            id="616906",
            url="https://gamma-api.polymarket.com/markets/616906",
        ),
        anchor=Anchor(
            subject="US federal funds rate",
            subject_type=SubjectType.RATE,
            jurisdiction="FOMC",
        ),
        occurrence=Occurrence(
            type=OccurrenceType.CUMULATIVE,
            interval=Interval(start="2026-01-01T00:00:00Z", end="2026-12-31T23:59:59Z"),
        ),
        measurement=Measurement(
            metric="count_of_25bp_cuts",
            threshold=Threshold(op=ThresholdOp.EQ, value=4),
            unit="count",
            includes_unscheduled=True,
        ),
        shape=Shape(outcome_type=OutcomeType.BINARY, outcomes=["Yes", "No"]),
        resolution=Resolution(
            authority="FOMC post-meeting statements",
            trading_cutoff="2026-12-31T00:00:00Z",
            evaluation_time="2026-12-31T23:59:00Z",
        ),
        raw=Raw(
            title="Will 4 Fed rate cuts happen in 2026?",
            description="Resolves according to the exact amount of cuts of 25 basis points in 2026...",
        ),
    )
    print(example.to_json())
