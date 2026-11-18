from abc import ABC, abstractmethod

from src.models.raw_job import RawJob


class JobSource(ABC):
    """Common interface every job source adapter implements.

    Adding a new source later means writing one class that satisfies this
    interface — nothing else in the pipeline needs to change.
    """

    name: str

    @abstractmethod
    def fetch(self, query: str, limit: int | None = None) -> list[RawJob]:
        """Fetch raw jobs for a single search query. Must not raise on
        zero results — return an empty list instead."""
        raise NotImplementedError
