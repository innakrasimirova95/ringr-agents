"""Settings of the simulated services, read from ``fakes.toml``."""

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / 'fakes.toml'


@dataclass(frozen=True)
class FakesConfig:
    seed: int = 42
    time_scale: float = 0.05
    call_timeout_s: float = 180
    endpointing_pause_ms: float = 300
    utterances: tuple[int, int] = (4, 12)
    gap_ms: tuple[int, int] = (500, 3000)
    hangup_rate: float = 0.05
    no_answer_rate: float = 0.1
    token_latency_ms: float = 40
    llm_hang_rate: float = 0.05
    llm_error_rate: float = 0.02
    client_timeout_ms: float = 1000

    def scaled_s(self, simulated_ms: float) -> float:
        """Real seconds that a simulated duration takes."""
        return simulated_ms / 1000 * self.time_scale


def load_config(path: Path = DEFAULT_PATH) -> FakesConfig:
    with path.open('rb') as file:
        raw = tomllib.load(file)
    names = {field.name for field in fields(FakesConfig)}
    values = {k: tuple(v) if isinstance(v, list) else v for k, v in raw.items() if k in names}
    return FakesConfig(**values)
