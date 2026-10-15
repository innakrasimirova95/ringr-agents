import pytest
from django.conf import LazySettings


@pytest.fixture(autouse=True)
def instant_network(settings: LazySettings) -> None:
    """Tests talk to the API in-process: no simulated latency or lost responses."""
    settings.SIMULATED_NETWORK = {
        **settings.SIMULATED_NETWORK,
        'latency_ms': 0,
        'lost_response_rate': 0,
    }
