"""Everything a call needs from the outside world, built in one place."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from agent_runtime.api_client import ApiClient
from agent_runtime.config import FakesConfig
from agent_runtime.fakes import FakeLLM, FakeTransport, fake_tts

API_BASE_URL = os.environ.get('API_BASE_URL', 'http://localhost:8800')


@dataclass
class Deps:
    config: FakesConfig
    api: ApiClient
    llm: FakeLLM
    tts: Callable[[str], bytes]
    transport_factory: Callable[[str], FakeTransport]


def build_deps(config: FakesConfig, **overrides: Any) -> Deps:
    """Build the runtime dependencies; any field can be replaced through ``overrides``."""
    fields: dict[str, Any] = {
        'config': config,
        'llm': FakeLLM(config),
        'tts': fake_tts,
        'transport_factory': partial(FakeTransport, config=config),
        **overrides,
    }
    if 'api' not in fields:
        fields['api'] = ApiClient(API_BASE_URL, client_timeout_s=config.client_timeout_ms / 1000)
    return Deps(**fields)
