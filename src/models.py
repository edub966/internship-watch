from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Job:
    company: str
    external_id: str
    title: str
    location: str
    url: str
    source: str
    posted_at: Optional[str] = None
    description: str = ""
    score: float = 0.0

    def to_dict(self):
        return asdict(self)
