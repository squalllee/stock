"""Provider interface."""

from abc import ABC, abstractmethod

from stock_master.models import Stock


class StockProvider(ABC):
    """Common interface implemented by each official market provider."""

    @abstractmethod
    def fetch(self) -> list[Stock]:
        """Fetch, filter, normalize, and return stocks for one market."""

