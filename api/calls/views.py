"""Endpoints the call runtime talks to. Routes and payloads are a fixed contract."""

import json
import math
import uuid
from typing import Any

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from calls.models import Call, CallResult, Tenant

HISTORY_SIZE = 20


@require_GET
def call_context(request: HttpRequest, call_id: uuid.UUID) -> JsonResponse:
    """Everything the runtime needs to start a call: tenant, contact, script and history."""
    # INSTRUCTIONS.md - Problema 1: "Las llamadas de las campañas grandes tardan en arrancar".
    # Se optimiza la consulta eliminando el cuello de botella N+1:
    # 1. select_related('campaign', 'contact'): INNER JOIN que trae datos clave en 1 sola query.
    call = get_object_or_404(Call.objects.select_related('campaign', 'contact'), pk=call_id)
    owner = call.campaign.ownerships.get()

    # 2. prefetch_related('results'): obtiene los resultados del historial en 1 sola query.
    previous_calls = Call.objects.filter(
        contact=call.contact, status=Call.Status.FINISHED
    ).prefetch_related('results').order_by('-created_at')[:HISTORY_SIZE]
    history = []
    for previous in previous_calls:
        # Se usa .all() para leer de la caché precargada por Django y evitar queries SQL extra.
        results = list(previous.results.all())
        result = results[0] if results else None
        if result is not None:
            history.append({'outcome': result.outcome, 'created_at': previous.created_at})

    return JsonResponse(
        {
            'call_id': str(call.id),
            'tenant': {'id': owner.tenant_id},
            'contact': {'name': call.contact.name, 'phone': call.contact.phone},
            'script': [
                {'order': step.order, 'text': step.text}
                for step in call.campaign.steps.order_by('order')
            ],
            'history': history,
        }
    )


@require_GET
def tenant_config(request: HttpRequest, tenant_id: int) -> JsonResponse:
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    return JsonResponse({'language': tenant.language, 'system_prompt': tenant.system_prompt})


@csrf_exempt
@require_POST
def call_result(request: HttpRequest, call_id: uuid.UUID) -> JsonResponse:
    """Store the outcome of a call and bill its minutes to the tenant."""
    try:
        payload = parse_result(request.body)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    call = get_object_or_404(Call, pk=call_id)
    CallResult.objects.create(call=call, **payload)
    call.status = Call.Status.FINISHED
    call.save(update_fields=['status'])

    tenant = call.campaign.ownerships.get().tenant
    tenant.minutes_used = tenant.minutes_used + math.ceil(payload['duration_s'] / 60)
    tenant.save()
    return JsonResponse({'status': 'ok'})


def parse_result(body: bytes) -> dict[str, Any]:
    data = json.loads(body)
    if not isinstance(data, dict) or data.get('outcome') not in CallResult.Outcome.values:
        raise ValueError('unknown outcome')
    duration_s = data.get('duration_s')
    if not isinstance(duration_s, int | float) or duration_s < 0:
        raise ValueError('duration_s must be a non-negative number')
    transcript = str(data.get('transcript', ''))
    return {'outcome': data['outcome'], 'duration_s': float(duration_s), 'transcript': transcript}
