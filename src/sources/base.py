from abc import ABC, abstractmethod
import re
from typing import List
from src.models import Job


def title_matches_terms(title: str, terms) -> bool:
    """Match opportunity terms as tokens, not substrings like intern/al."""
    text = str(title or "").lower()
    for term in terms:
        value = str(term or "").strip().lower()
        if value and re.search(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])", text):
            return True
    return False


class JobSource(ABC):
    @abstractmethod
    def fetch(self) -> List[Job]:
        raise NotImplementedError
