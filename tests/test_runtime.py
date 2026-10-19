import json
from dataclasses import replace

import httpx

from agent_runtime import CallOutcome, build_deps, run_call
from agent_runtime.api_client import ApiClient
from agent_runtime.config import load_config

CALL_ID = '6f1c2b1e-0000-4000-8000-000000000001'


async def test_a_call_runs_to_an_outcome_and_reports_it() -> None:
    config = replace(load_config(), llm_hang_rate=0, llm_error_rate=0, no_answer_rate=0)
    reported: list[dict[str, object]] = []

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/context'):
            return httpx.Response(
                200,
                json={
                    'call_id': CALL_ID,
                    'tenant': {'id': 1},
                    'contact': {'name': 'Lucia Garcia', 'phone': '+34600000001'},
                    'script': [{'order': 1, 'text': 'Greet the contact.'}],
                    'history': [],
                },
            )
        if request.url.path == '/tenants/1/config':
            return httpx.Response(200, json={'language': 'es-ES', 'system_prompt': 'Be brief.'})
        reported.append(json.loads(request.content))
        return httpx.Response(200, json={'status': 'ok'})

    client = ApiClient('http://api.test', client_timeout_s=1, transport=httpx.MockTransport(api))

    outcome = await run_call(CALL_ID, build_deps(config, api=client))

    assert outcome in {CallOutcome.COMPLETED, CallOutcome.USER_HANGUP}
    assert [r['outcome'] for r in reported] == [outcome]



# lanza NoAnswer, el runtime retorna CallOutcome.NO_ANSWER
# y reporta outcome='no_answer' a la API.
async def test_no_answer_outcome_reported() -> None:
    config = replace(load_config(), no_answer_rate=1.0)
    reported: list[dict[str, object]] = []

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/context'):
            return httpx.Response(
                200,
                json={
                    'call_id': CALL_ID,
                    'tenant': {'id': 1},
                    'contact': {'name': 'Lucia Garcia', 'phone': '+34600000001'},
                    'script': [{'order': 1, 'text': 'Greet the contact.'}],
                    'history': [],
                },
            )
        if request.url.path == '/tenants/1/config':
            return httpx.Response(200, json={'language': 'es-ES', 'system_prompt': 'Be brief.'})
        reported.append(json.loads(request.content))
        return httpx.Response(200, json={'status': 'ok'})

    client = ApiClient('http://api.test', client_timeout_s=1, transport=httpx.MockTransport(api))

    outcome = await run_call(CALL_ID, build_deps(config, api=client))

    assert outcome == CallOutcome.NO_ANSWER
    assert len(reported) == 1
    assert reported[0]['outcome'] == 'no_answer'
