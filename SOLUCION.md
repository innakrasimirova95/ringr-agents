# Notas de Entrega - Prueba Técnica ringr-agents

A continuación resumo las soluciones implementadas y las propuestas de mejora tras revisar el sistema de forma integral.

---

## 1. Problemas resueltos y decisiones técnicas

### 1.1 Rendimiento en el arranque de campañas grandes (Problema N+1)
* **Rendimiento (N+1 queries):** Resolví la sobrecarga en `/calls/{id}/context` (que ejecutaba más de 26 consultas) aplicando `select_related` y `prefetch_related` e indexando PostgreSQL, limitando el coste a un número fijo de queries inmediatas independientemente del historial.

### 1.2 Facturación duplicada y concurrencia
  * Uso de `transaction.atomic()` asegurando la fila de la llamada con `select_for_update()`.
  * **Idempotencia:** Si entra una petición con un resultado idéntico al ya registrado, la API responde `200 OK` de forma idempotente sin duplicar cobros.
  * **Integridad:** Si llega un resultado con datos discordantes para una llamada ya cerrada, se rechaza inmediatamente con `409 Conflict`.
  * **Actualizaciones atómicas:** La acumulación de minutos en `Tenant.minutes_used` se realiza a nivel de motor SQL mediante expresiones `F()`, eliminando condiciones de carrera y *lost updates* cuando múltiples llamadas finalizan simultáneamente.

### 1.3 Reintento de llamadas sin contestar (`no_answer`)
  * Nuevo estado `no_answer` al flujo de resultados y módulo `retries.py` para gestionar la lógica de reprogramación.
  * **Cálculo de horario:** Con `compute_retry_scheduled_at`, el reintento se calcula según el `retry_delay` y se proyecta a la zona horaria `Europe/Madrid` (gestionando cambios de hora estacionales con `ZoneInfo`). Si el tiempo de espera vence fuera de la franja comercial del cliente, la llamada se traslada automáticamente a la apertura del día siguiente.
  * `retry_of = models.OneToOneField('self')` en el modelo `Call`  delega una imposibilidad matemática de programar dos reintentos para una misma llamada. Otra opción
  puede ser añadir ForeignKey tradicional si le añades manualmente una restricción de unicidad.

### 1.4 Diagnóstico y solución de la fuga de memoria en el runtime
* **Diagnóstico:** El pod sufría reinicios por OOM (*Out Of Memory*). En `CallSession._reply()`, el bucle de streaming del LLM capturaba `except BaseException:`. En Python `asyncio`, cuando salta un timeout (`asyncio.wait_for`), el framework inyecta un `asyncio.CancelledError` (que hereda de `BaseException`, no de `Exception`). Al capturarlo, el runtime ignoraba la orden de parada e intentaba seguir reintentando llamadas al LLM, acumulando generadores abiertos y corrutinas zombi en el event loop.
* **Solución:** Modifiqué la captura a `except Exception:`. De este modo, `CancelledError` se propaga con normalidad, interrumpe el bucle de inmediato y el runtime libera todos los recursos del socket y generadores asociados.

---

## 2. Batería de pruebas y calidad de código

* **Control de N+1:** Valido con `CaptureQueriesContext` que `/context` no supere 5 consultas con 20 llamadas históricas.
* **Idempotencia y concurrencia:** Pruebas que validan tanto la deduplicación con `select_for_update` como la suma atómica con `F()`.
* **Reglas de reintentos:** Verificación de creación de llamadas `PENDING`, corte estricto al alcanzar `max_attempts` y cálculo horario de Madrid (dentro de franja, antes de apertura, tras el cierre y sin horario).

---

## 3. Tarea 4: Propuestas de mejora y evolución arquitectónica

1. **Soporte para festivos y días laborables en la franja comercial:**
   `compute_retry_scheduled_at` respeta las horas del día, pero no distingue fines de semana ni festivos nacionales. Si una llamada falla un viernes a las 21:30, se programa para el sábado a las 09:00. Integraría una comprobación de días hábiles (`weekday < 5`) y una tabla de festivos para diferir la llamada directamente al lunes.

2. **Facturación inmutable:** Sustituir el contador acumulativo actual (`Tenant.minutes_used`), que sobreescribe el total en cada llamada, por un registro de auditoría que inserte una nueva fila por cada cobro. Esto aporta trazabilidad completa de qué llamada generó cada minuto, facilita desgloses mensuales y permite gestionar rectificaciones o abonos sin destruir el historial contable.

3. ### 2. Activar el panel de administración de Django
 **Backoffice operativo con auditoría de cambios (Django Admin + History):** 
  Habilitar el panel de administración de Django como herramienta interna para los equipos de soporte y operaciones. Esto permite inspeccionar el estado de contactos, desbloquear llamadas pendientes y ajustar horarios de clientes sin requerir acceso directo a base de datos ni ejecución de scripts en producción. Para mitigar riesgos, se podría usar  `django-simple-history` para registrar qué operador modificó un contrato o forzó un cambio de estado, garantizando trazabilidad y capacidad de rollback ante errores humanos.

4. **Unificación de Enums:** Actualmente los estados de resultado (`completed`, `no_answer`, etc.) están duplicados como `StrEnum` en el runtime y como `TextChoices` en el modelo `CallResult`. Se podrían unificar ambos usando directamente el `StrEnum` en las `choices` del modelo Django para evitar inconsistencias y errores `400 Bad Request` si se añaden nuevos estados en el futuro.

5. **Limitar cuotas:** Actualmente `Tenant.minutes_used` acumula consumo sin un tope superior. Si un cliente malconfigura un dialer o una campaña entra en bucle, se pueden disparar los costes operativos. Propongo añadir a `Tenant` un límite mensual (`monthly_minute_limit`) y emitir alertas al alcanzar el 80% y bloquee de forma preventiva la transición de llamadas a `IN_PROGRESS` al llegar al 100%, protegiendo tanto la facturación del cliente.
