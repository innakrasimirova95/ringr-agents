# El código

Un runtime asíncrono que hace llamadas telefónicas con un agente de voz (`agent_runtime/`) y la API
de Django con la que habla (`api/`).

## Qué puedes cambiar

| | Rutas | Por qué |
| --- | --- | --- |
| 🔒 **No cambiar** | `agent_runtime/fakes.py`, `fakes.toml`, `api/calls/middleware.py` | Simulan los proveedores externos y las condiciones de producción |
| | `api/calls/migrations/0001_initial.py` | Ya aplicada en producción: añade migraciones nuevas |
| ⚠️ **Ampliar sin romper** | `run_call(call_id, deps) -> CallOutcome`, `build_deps()`, `Deps` | Los usa el marcador automático (dialer). Puedes añadir `no_answer` a `CallOutcome` |
| | Los tres endpoints HTTP | Mantén rutas y campos. `POST /result` responde 200 a una petición idéntica repetida |
| ✅ **Tuyo** | Todo lo demás | Incluidos módulos, migraciones y tests nuevos |

## Flujo de información

El marcador automático (dialer), que no está en este repositorio, inicia cada llamada pendiente (`Call`)
en cuanto llega su hora programada (`scheduled_at`).
En el diagrama, cada bloque es una parte del repositorio: el runtime (`agent_runtime/`), la API (`api/`) y
los proveedores simulados (`agent_runtime/fakes.py`), que hacen de servicios externos.

```mermaid
sequenceDiagram
    autonumber
    participant D as Dialer
    box rgba(154,98,0,0.10) Proveedores simulados
    participant F as Fakes (phone, LLM, TTS)
    end
    box rgba(47,79,216,0.10) agent_runtime/
    participant S as CallSession
    participant R as run_call
    participant A as ApiClient
    end
    box rgba(31,122,77,0.10) api/
    participant V as Django views
    participant DB as Postgres
    end

    D->>R: run_call(call_id, deps)
    R->>A: get_context(call_id)
    A->>V: GET /calls/{id}/context
    V->>DB: call, campaign, script, contact history
    V-->>A: context JSON
    R->>A: get_tenant_config(tenant_id)
    Note over A: cached per tenant
    R->>S: run(), cut after call_timeout_s
    S->>F: transport.open()
    loop every user turn
        F-->>S: Utterance | Hangup | EndOfCall
        S->>S: short pause (the user may keep talking)
        S->>F: llm.stream(prompt), up to 3 attempts
        S->>F: tts(reply), transport.play(audio)
    end
    S->>F: transport.close()
    S-->>R: CallOutcome
    R->>A: post_result(outcome, duration_s, transcript)
    A->>V: POST /calls/{id}/result
    V->>DB: store CallResult, mark Call finished, add minutes to Tenant
    alt no answer in time, or 5xx
        R->>A: post_result(...) again, up to 3 attempts
    else 200 or 4xx
        V-->>A: response
    end
    R-->>D: CallOutcome
```

| Resultado | Cuándo |
| --- | --- |
| `completed` | La conversación llegó a su final natural |
| `user_hangup` | El usuario colgó |
| `timeout` | La llamada duró más de `call_timeout_s` |
| `error` | Cualquier otra cosa: no se pudo cargar el contexto, el LLM siguió fallando, … |

## Clases del runtime

```mermaid
classDiagram
    direction LR

    class run_call {
        <<function>>
        +run_call(call_id, deps) CallOutcome
        +report_result(deps, call_id, outcome, duration_s, transcript)
    }
    class Deps {
        <<dataclass>>
        config: FakesConfig
        api: ApiClient
        llm: FakeLLM
        tts(text) bytes
        transport_factory(call_id) FakeTransport
    }
    class CallSession {
        -ctx: CallContext
        -cfg: dict
        -recording: bytearray
        +transcript: str
        +run() CallOutcome
        -reply() str
        -prompt() str
    }
    class CallOutcome {
        <<enumeration>>
        completed
        user_hangup
        timeout
        error
    }
    class ApiClient {
        -tenant_configs: dict
        +get_context(call_id) CallContext
        +get_tenant_config(tenant_id) dict
        +post_result(call_id, outcome, duration_s, transcript)
    }
    class CallContext {
        <<dataclass>>
        call_id
        tenant_id
        contact
        script: list~str~
        history: list~str~
    }
    class FakeTransport {
        <<fake>>
        +open()
        +next_event() Utterance | Hangup | EndOfCall
        +play(audio)
        +close()
    }
    class FakeLLM {
        <<fake>>
        +stream(prompt) tokens
    }
    class FakesConfig {
        <<from fakes.toml>>
        +scaled_s(simulated_ms) float
    }

    run_call ..> CallSession : creates, runs with a timeout
    run_call ..> ApiClient : loads context, reports result
    Deps *-- FakesConfig
    Deps *-- ApiClient
    Deps *-- FakeLLM
    Deps ..> FakeTransport : one per call
    CallSession --> Deps
    CallSession --> CallContext
    CallSession ..> CallOutcome : returns
    ApiClient ..> CallContext : returns
```

- `runner.py` y `session.py` nunca construyen sus dependencias: las reciben en `Deps`, así que un
  test puede cambiar cualquiera (`tests/test_runtime.py` sustituye la API por una en memoria).
- Excepciones: `NoAnswer` (`transport.open()`, nadie descuelga), `LLMError` (falla el stream del
  LLM) y `TimeoutError` (el timeout de `run_call`).
- Las duraciones simuladas se multiplican por `time_scale`, así que una llamada de 180 s dura unos 9 s.

## API y modelo de datos

| Endpoint | Qué devuelve o hace |
| --- | --- |
| `GET /calls/{id}/context` | `{call_id, tenant: {id}, contact: {name, phone}, script: [{order, text}], history: [{outcome, created_at}]}`. El historial son las 20 últimas llamadas terminadas del contacto |
| `GET /tenants/{id}/config` | `{language, system_prompt}` |
| `POST /calls/{id}/result` | Cuerpo `{outcome, duration_s, transcript}`. Guarda un `CallResult`, marca la llamada como terminada y suma `ceil(duration_s / 60)` a `Tenant.minutes_used` |

```mermaid
erDiagram
    Tenant ||--o{ CampaignOwnership : owns
    Campaign ||--o{ CampaignOwnership : "owned through"
    Campaign ||--o{ ScriptStep : "script, by order"
    Campaign ||--o{ Call : contains
    Contact ||--o{ Call : receives
    Call ||--o{ CallResult : "reported as"

    Tenant {
        string name
        string language
        text system_prompt
        int minutes_used "billed minutes"
    }
    Campaign {
        string name
    }
    ScriptStep {
        int order
        text text
    }
    Contact {
        string name
        string phone
    }
    Call {
        uuid id
        string status "pending | finished"
        datetime created_at
        datetime scheduled_at "the dialer starts it after this"
    }
    CallResult {
        string outcome
        float duration_s
        text transcript
        datetime created_at
    }
```

`make seed` carga tres tenants de tamaños muy distintos: acme (50 llamadas pendientes), beta (250) y
gamma (700, con 15–20 llamadas terminadas por contacto).

## Cómo arrancarlo

- `make up` levanta Postgres y la API en Docker. La API se recarga al guardar.
- `make dbshell` abre `psql`. Para cambiar el esquema: edita `models.py` y luego `make makemigrations`
  y `make up`.
- En un test, `CaptureQueriesContext(connection)` cuenta las consultas, y
  `pytest.mark.django_db(transaction=True)` permite que varios hilos vean los commits de los demás.
