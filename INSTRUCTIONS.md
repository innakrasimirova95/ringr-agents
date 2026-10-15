# Prueba técnica de Ringr

En Ringr hacemos llamadas telefónicas con agentes de voz. Este repositorio es un modelo pequeño de nuestro
stack: un runtime asíncrono que ejecuta las llamadas (`agent_runtime/`) y la API de Django con la
que habla (`api/`). En [`docs/CODEBASE.md`](docs/CODEBASE.md) tienes un mapa del código.

Puedes leer cualquier parte del código, pero cambia solo los ficheros marcados como tuyos en
`docs/CODEBASE.md`. Si ves algo más que se pueda mejorar, dentro o fuera de esos ficheros, apúntalo:
la última parte de la prueba es contarnos qué cambiarías.

Una parte consiste en encontrar y arreglar lo que falla, y otra en diseñar una funcionalidad pequeña.

## Cómo trabajar

No hace falta terminarlo todo: preferimos ver una parte bien hecha, y tu razonamiento sobre el
resto. Lo que no llegues a hacer, cuéntanos en tus notas cómo lo harías.

Puedes usar herramientas de IA como quieras.

## Cómo arrancarlo

Necesitas Docker y [uv](https://docs.astral.sh/uv/). No hay claves de API ni servicios externos
reales.

```
make up        # Postgres y la API en Docker
make seed      # reinicia los datos de demo
make down
```

## La tarea

### Contexto

Desde que entraron los clientes más grandes, la memoria del runtime crece hasta que se reinicia el
pod, y de ahí vienen algunas de las incidencias de las últimas semanas. Hay tres problemas que
necesitan tu atención y, al final, te pedimos tu opinión sobre el código.

### 1. Las llamadas de las campañas grandes tardan en arrancar

Las campañas pequeñas arrancan casi al instante; la campaña del cliente más grande tiene un retraso
apreciable antes de cada llamada, y empeora a medida que crece la campaña.

### 2. Facturación duplicada

`POST /calls/{id}/result` guarda el resultado y suma los minutos de la llamada a
`Tenant.minutes_used`, que es lo que facturamos. El runtime reintenta el POST cuando no recibe
respuesta, y aparecen resultados duplicados y minutos facturados dos veces.

- Repetir la misma petición no debe duplicar nada: un resultado, los minutos facturados una vez y
  un 200.
- Lo mismo si llegan dos peticiones idénticas a la vez.
- Un fallo a medias no debe dejar un resultado guardado sin sus minutos, ni al revés.

No está decidido qué debe pasar cuando llega un resultado *distinto* para una llamada que ya tiene
uno.

### 3. Funcionalidad: reintentar las llamadas que nadie contesta

Cuando nadie descuelga, la telefonía simulada lanza `NoAnswer` y hoy el runtime lo informa como
`error`. Hay que distinguir las llamadas sin contestar, y algunos clientes quieren que se vuelvan a
intentar, según dicen sus contratos:

| Tenant | Intentos (contando el primero) | Espera entre intentos | Horario de llamadas (hora de Madrid) |
| --- | --- | --- | --- |
| acme | 3 | 30 min | 09:00–21:00 |
| beta | 2 | 2 h | 10:00–20:00 |
| gamma | 1 (sin reintentos) | | |

Vendrán más tenants con otros valores; estos tres son los que debe cargar `make seed`.

- El runtime informa las llamadas sin contestar como `no_answer`, y la API lo acepta.
- Cuando una llamada acaba sin contestar y el contrato permite otro intento, la API programa un
  reintento en cuanto pase la espera, pero nunca fuera del horario de llamadas (si la espera
  termina fuera de horario, el reintento sale cuando vuelva a abrir).
- Informar dos veces del mismo resultado no debe programar dos reintentos.

Algún cliente comenta que, de vez en cuando, hay contactos con los que la llamada se quedó a medias.

El marcador automático (dialer), que no está en este repositorio, inicia cada llamada pendiente (`Call`)
en cuanto llega su hora programada (`scheduled_at`). Por tanto, **un reintento es simplemente una
nueva llamada pendiente para la misma campaña y el mismo contacto, con `scheduled_at` igual al
momento a partir del cual puede iniciarse.** El resto lo decides tú: dónde viven las reglas, cómo
cuentas los intentos y cómo estructuras el código.

### Contratos fijos

Otros servicios dependen de esto:

- `async def run_call(call_id: str, deps: Deps) -> CallOutcome`, exportada por `agent_runtime`.
  `CallOutcome` es `completed | user_hangup | timeout | error`, y puedes añadir `no_answer`.
- `POST /calls/{id}/result` devuelve 200 cuando el resultado queda registrado, también cuando se
  repite una petición idéntica.
- El runtime solo reintenta ante un 5xx o si no hay respuesta, nunca ante un 4xx.
- Un reintento es una `Call` pendiente nueva con `scheduled_at`, como se explica arriba.

En la API puedes usar el ORM o SQL directo, lo que prefieras.

### 4. Última parte: qué cambiarías

Cuando termines lo anterior, o hasta donde llegues, repasa el código una vez más con ojos frescos
y cuéntanos qué mejorarías o harías de otra forma: código, diseño, modelo de datos, tests,
herramientas, cualquier cosa que hayas visto por el camino. Di dónde lo viste, por qué importa y
qué harías.

No hace falta que cambies nada de eso. Si arreglas algo, que sea en los ficheros que son tuyos y
cuéntalo en tus notas. Unos pocos puntos bien razonados valen más que una lista larga.

## Qué tienes que enviarnos

Tus cambios en el código. Súbelo a un repositorio público y mándanos el enlace, o comprime la
carpeta entera, **incluida la carpeta oculta `.git`**, y envíanosla:

```
zip -r ringr-agents-<tu-nombre>.zip ringr-agents -x '*/.venv/*'
```
