"""Stand-ins for the external providers: telephony with speech-to-text, the LLM and TTS.

They behave like the real providers, failures included, and are deterministic from ``seed``.
"""

import asyncio
import random
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from agent_runtime.config import FakesConfig


@dataclass(frozen=True)
class Utterance:
    text: str


class Hangup:
    """The user hung up."""


class EndOfCall:
    """The conversation reached its natural end."""


class NoAnswer(Exception):  # noqa: N818
    """Nobody picked up the phone."""


class LLMError(Exception):
    """The provider failed while streaming a reply."""


PHRASES = [
    'Hello, who is this?',
    'Can you tell me the amount again?',
    'I already paid that last month.',
    'Could you send it to me by email?',
    'Okay, that works for me.',
]
REPLIES = [
    'I am calling about the pending balance on your account and the options to settle it.',
    'You can pay it in one go or in three monthly instalments with no extra fee.',
    'Of course, I will send you all the details by email right after this call.',
]

# Receives (event, call_id) for 'open', 'close', 'utterance' and 'play'.
Observer = Callable[[str, str], None]


class FakeTransport:
    """The phone line. Each call plays out from ``seed`` and ``call_id``."""

    def __init__(self, call_id: str, config: FakesConfig, observer: Observer | None = None):
        self._call_id = call_id
        self._config = config
        self._observer = observer
        rng = random.Random(f'{config.seed}:{call_id}')
        count = rng.randint(*config.utterances)
        self._utterances = [rng.choice(PHRASES) for _ in range(count)]
        self._gaps_ms = [rng.uniform(*config.gap_ms) for _ in range(count)]
        self._hangup_at = rng.randrange(count) if rng.random() < config.hangup_rate else None
        self._answered = rng.random() >= config.no_answer_rate
        self._next = 0

    async def open(self) -> None:
        """Dial and wait until someone picks up."""
        if not self._answered:
            raise NoAnswer(self._call_id)
        self._notify('open')

    async def next_event(self) -> Utterance | Hangup | EndOfCall:
        if self._next == self._hangup_at:
            return Hangup()
        if self._next == len(self._utterances):
            return EndOfCall()
        await asyncio.sleep(self._config.scaled_s(self._gaps_ms[self._next]))
        self._next += 1
        self._notify('utterance')
        return Utterance(self._utterances[self._next - 1])

    async def play(self, audio: bytes) -> None:
        self._notify('play')

    async def close(self) -> None:
        self._notify('close')

    def _notify(self, event: str) -> None:
        if self._observer is not None:
            self._observer(event, self._call_id)


class FakeLLM:
    """A streaming chat API. The reply, and whether the stream stalls, depend only on the
    prompt, as real providers tend to fail again on the same input. Errors are random."""

    def __init__(self, config: FakesConfig) -> None:
        self._config = config
        self._rng = random.Random(config.seed)
        turns_per_call = sum(config.utterances) / 2
        self._hang_per_reply = 1 - (1 - config.llm_hang_rate) ** (1 / turns_per_call)
        # Reads waiting on a stalled stream, held the way an open socket holds them.
        self._stalled: set[asyncio.Future[None]] = set()

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        prompt_rng = random.Random(f'{self._config.seed}:{prompt}')
        tokens = prompt_rng.choice(REPLIES).split(' ')
        hangs = prompt_rng.random() < self._hang_per_reply
        fails = self._rng.random() < self._config.llm_error_rate
        fails_at = self._rng.randrange(len(tokens)) if fails else None
        for index, token in enumerate(tokens):
            if hangs and index == len(tokens) // 2:
                read = asyncio.get_running_loop().create_future()
                self._stalled.add(read)
                try:
                    await read
                finally:
                    self._stalled.discard(read)
            if index == fails_at:
                raise LLMError('provider closed the stream')
            await asyncio.sleep(self._config.scaled_s(self._config.token_latency_ms))
            yield token if index == 0 else f' {token}'


def fake_tts(text: str) -> bytes:
    """Text to speech: about 200 bytes of audio per character."""
    return bytes(200 * len(text))
