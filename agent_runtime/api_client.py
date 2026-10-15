"""Client for the calls API."""

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class CallContext:
    call_id: str
    tenant_id: int
    contact: str
    script: list[str]
    history: list[str]


class ApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        client_timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # Reads are not retried, so they get a generous timeout.
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=10,
            limits=httpx.Limits(max_connections=None, max_keepalive_connections=64),
            transport=transport,
        )
        self._client_timeout_s = client_timeout_s
        # Tenant configuration only changes with a deploy.
        self._tenant_configs: dict[int, dict[str, str]] = {}

    async def get_context(self, call_id: str) -> CallContext:
        response = await self._http.get(f'/calls/{call_id}/context')
        response.raise_for_status()
        data = response.json()
        return CallContext(
            call_id=data['call_id'],
            tenant_id=data['tenant']['id'],
            contact=f'{data["contact"]["name"]} ({data["contact"]["phone"]})',
            script=[step['text'] for step in data['script']],
            history=[previous['outcome'] for previous in data['history']],
        )

    async def get_tenant_config(self, tenant_id: int) -> dict[str, str]:
        if tenant_id not in self._tenant_configs:
            response = await self._http.get(f'/tenants/{tenant_id}/config')
            response.raise_for_status()
            self._tenant_configs[tenant_id] = response.json()
        return self._tenant_configs[tenant_id]

    async def post_result(
        self, call_id: str, *, outcome: str, duration_s: float, transcript: str
    ) -> httpx.Response:
        return await self._http.post(
            f'/calls/{call_id}/result',
            json={'outcome': outcome, 'duration_s': duration_s, 'transcript': transcript},
            timeout=self._client_timeout_s,
        )
