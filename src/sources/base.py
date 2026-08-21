from abc import ABC, abstractmethod
from typing import List
from src.models import Job


class JobSource(ABC):
    @abstractmethod
    def fetch(self) -> List[Job]:
        raise NotImplementedError
