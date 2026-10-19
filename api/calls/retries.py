"""Lógica de programación de reintentos para llamadas sin contestar según contrato del cliente."""

import datetime
from zoneinfo import ZoneInfo

from django.utils import timezone

from calls.models import Call, Tenant


def compute_retry_scheduled_at(
    from_time: datetime.datetime,
    delay: datetime.timedelta,
    hours_start: datetime.time | None,
    hours_end: datetime.time | None,
    tz_name: str = 'Europe/Madrid',
) -> datetime.datetime:
    """Calcula la próxima hora de programación respetando la franja horaria de llamadas.
    
    - Si la espera termina antes de abrir: programa para la apertura del mismo día.
    - Si la espera termina dentro del horario comercial: programa en ese instante exacto.
    - Si la espera termina en el cierre o después: programa para la apertura del día siguiente.
    """
    earliest = from_time + delay
    if hours_start is None or hours_end is None:
        return earliest

    tz = ZoneInfo(tz_name)
    local = earliest.astimezone(tz)

    # Si cae antes de la apertura del día
    if local.time() < hours_start:
        return local.replace(
            hour=hours_start.hour, minute=hours_start.minute, second=0, microsecond=0
        )
    # Si cae en o después de la hora de cierre, se traslada al día siguiente
    if local.time() >= hours_end:
        next_day = local.date() + datetime.timedelta(days=1)
        return datetime.datetime.combine(next_day, hours_start, tzinfo=tz)
    return local


def maybe_schedule_retry(
    call: Call, tenant: Tenant, now: datetime.datetime | None = None
) -> Call | None:
    """Programa un reintento si el contrato del cliente lo permite.

        un reintento = llamada pendiente para la misma campaña y el mismo
      contacto, con scheduled_at igual al momento a partir del cual puede iniciarse
    """
    # Evitar duplicados: comprobar si esta llamada ya tiene un reintento generado
    if Call.objects.filter(retry_of=call).exists():
        return None

    # No superar el límite de intentos estipulado en el contrato
    if call.attempt >= tenant.max_attempts:
        return None

    if now is None:
        now = timezone.now()

    scheduled_at = compute_retry_scheduled_at(
        from_time=now,
        delay=tenant.retry_delay,
        hours_start=tenant.calling_hours_start,
        hours_end=tenant.calling_hours_end,
        tz_name=tenant.calling_timezone,
    )

    return Call.objects.create(
        campaign=call.campaign,
        contact=call.contact,
        status=Call.Status.PENDING,
        scheduled_at=scheduled_at,
        attempt=call.attempt + 1,
        retry_of=call,
    )