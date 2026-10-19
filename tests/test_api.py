import threading
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from calls.models import Call, CallResult, Campaign, CampaignOwnership, Contact, ScriptStep, Tenant
from calls.retries import compute_retry_scheduled_at

pytestmark = pytest.mark.django_db


@pytest.fixture
def call() -> Call:
    tenant = Tenant.objects.create(
        name='acme',
        max_attempts=3,
        retry_delay=timedelta(minutes=30),
        calling_hours_start=time(9, 0),
        calling_hours_end=time(21, 0),
    )
    campaign = Campaign.objects.create(name='acme collections')
    CampaignOwnership.objects.create(campaign=campaign, tenant=tenant)
    ScriptStep.objects.create(campaign=campaign, order=1, text='Greet the contact.')
    contact = Contact.objects.create(name='Lucia Garcia', phone='+34600000001')
    return Call.objects.create(campaign=campaign, contact=contact)


def test_context_has_the_expected_shape(client: Client, call: Call) -> None:
    body = client.get(f'/calls/{call.id}/context').json()

    assert body == {
        'call_id': str(call.id),
        'tenant': {'id': call.campaign.ownerships.get().tenant_id},
        'contact': {'name': 'Lucia Garcia', 'phone': '+34600000001'},
        'script': [{'order': 1, 'text': 'Greet the contact.'}],
        'history': [],
    }


# Valida que call_context resuelva el historial en menos consultas SQL eliminando el problema N+1.
def test_call_context_avoids_n_plus_one_queries(client: Client, call: Call) -> None:
    finished_calls = [
        Call(campaign=call.campaign, contact=call.contact, status=Call.Status.FINISHED)
        for _ in range(20)
    ]
    Call.objects.bulk_create(finished_calls)
    CallResult.objects.bulk_create(
        [CallResult(call=c, outcome='completed', duration_s=60.0) for c in finished_calls]
    )

    with CaptureQueriesContext(connection) as queries:
        response = client.get(f'/calls/{call.id}/context')

    assert response.status_code == 200
    assert len(response.json()['history']) == 20
    assert len(queries) <= 5


def test_result_is_stored_and_bills_minutes(client: Client, call: Call) -> None:
    response = client.post(
        f'/calls/{call.id}/result',
        {'outcome': 'completed', 'duration_s': 95.0, 'transcript': 'user: Hello'},
        content_type='application/json',
    )

    assert response.status_code == 200
    assert CallResult.objects.get(call=call).outcome == 'completed'
    tenant = call.campaign.ownerships.get().tenant
    tenant.refresh_from_db()
    assert tenant.minutes_used == 2  # ceil(95 / 60)


# Valida idempotencia: 1 resultado, minutos facturados 1 sola vez y 200 OK.
def test_repeated_identical_result_is_idempotent(client: Client, call: Call) -> None:
    payload = {'outcome': 'completed', 'duration_s': 95.0, 'transcript': 'user: Hello'}
    res1 = client.post(f'/calls/{call.id}/result', payload, content_type='application/json')
    res2 = client.post(f'/calls/{call.id}/result', payload, content_type='application/json')

    assert res1.status_code == 200
    assert res2.status_code == 200
    assert CallResult.objects.filter(call=call).count() == 1
    tenant = call.campaign.ownerships.get().tenant
    tenant.refresh_from_db()
    assert tenant.minutes_used == 2


# Valida resultado distinto para llamada finalizada: devuelve 409 Conflict.
def test_conflicting_result_returns_409(client: Client, call: Call) -> None:
    res1 = client.post(
        f'/calls/{call.id}/result',
        {'outcome': 'completed', 'duration_s': 95.0, 'transcript': 'user: Hello'},
        content_type='application/json',
    )
    assert res1.status_code == 200

    res2 = client.post(
        f'/calls/{call.id}/result',
        {'outcome': 'user_hangup', 'duration_s': 30.0, 'transcript': 'user: Bye'},
        content_type='application/json',
    )
    assert res2.status_code == 409
    assert CallResult.objects.get(call=call).outcome == 'completed'


# Valida dos peticiones idénticas concurrentes: select_for_update evita duplicidad.
@pytest.mark.django_db(transaction=True)
def test_concurrent_identical_requests(call: Call) -> None:
    statuses: list[int] = []

    def send() -> None:
        try:
            c = Client()
            r = c.post(
                f'/calls/{call.id}/result',
                {'outcome': 'completed', 'duration_s': 120.0, 'transcript': 'test'},
                content_type='application/json',
            )
            statuses.append(r.status_code)
        finally:
            connection.close()

    t1 = threading.Thread(target=send)
    t2 = threading.Thread(target=send)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert statuses == [200, 200]
    assert CallResult.objects.filter(call=call).count() == 1
    tenant = call.campaign.ownerships.get().tenant
    tenant.refresh_from_db()
    assert tenant.minutes_used == 2


# Valida que llamadas concurrentes del mismo cliente sumen minutos con F() sin lost updates.
@pytest.mark.django_db(transaction=True)
def test_concurrent_different_calls_atomic_billing(call: Call) -> None:
    tenant = call.campaign.ownerships.get().tenant
    contact2 = Contact.objects.create(name='Pedro Gomez', phone='+34600000002')
    call2 = Call.objects.create(campaign=call.campaign, contact=contact2)

    def finish_call(target_call: Call, duration: float) -> None:
        try:
            c = Client()
            c.post(
                f'/calls/{target_call.id}/result',
                {'outcome': 'completed', 'duration_s': duration, 'transcript': 'hi'},
                content_type='application/json',
            )
        finally:
            connection.close()

    t1 = threading.Thread(target=finish_call, args=(call, 60.0))    # 1 min
    t2 = threading.Thread(target=finish_call, args=(call2, 120.0))  # 2 min
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    tenant.refresh_from_db()
    assert tenant.minutes_used == 3


# Valida que no_answer programe un reintento (Call pendiente con scheduled_at y attempt=2).
def test_no_answer_schedules_retry(client: Client, call: Call) -> None:
    response = client.post(
        f'/calls/{call.id}/result',
        {'outcome': 'no_answer', 'duration_s': 0.0, 'transcript': ''},
        content_type='application/json',
    )

    assert response.status_code == 200
    call.refresh_from_db()
    assert call.status == Call.Status.FINISHED

    # Debe haberse programado una llamada de reintento
    retries = Call.objects.filter(retry_of=call)
    assert retries.count() == 1
    retry = retries.get()
    assert retry.status == Call.Status.PENDING
    assert retry.attempt == 2
    assert retry.campaign == call.campaign
    assert retry.contact == call.contact
    assert retry.scheduled_at > call.created_at


# "Informar dos veces del mismo resultado no debe programar dos reintentos."
def test_duplicate_no_answer_does_not_schedule_duplicate_retry(client: Client, call: Call) -> None:
    payload = {'outcome': 'no_answer', 'duration_s': 0.0, 'transcript': ''}
    client.post(f'/calls/{call.id}/result', payload, content_type='application/json')
    client.post(f'/calls/{call.id}/result', payload, content_type='application/json')

    assert Call.objects.filter(retry_of=call).count() == 1
    # Solo deben existir 2 llamadas en total (la original y su único reintento)
    assert Call.objects.filter(contact=call.contact).count() == 2


# Valida que no se programen reintentos tras alcanzar max_attempts contractual.
def test_no_answer_does_not_retry_past_max_attempts(client: Client, call: Call) -> None:
    call.attempt = 3
    call.save(update_fields=['attempt'])

    response = client.post(
        f'/calls/{call.id}/result',
        {'outcome': 'no_answer', 'duration_s': 0.0, 'transcript': ''},
        content_type='application/json',
    )

    assert response.status_code == 200
    assert not Call.objects.filter(retry_of=call).exists()
    assert Call.objects.filter(status=Call.Status.PENDING).count() == 0


# Valida el cálculo de scheduled_at en Madrid: dentro de horario, antes, después o sin horario.
def test_compute_retry_scheduled_at_within_and_outside_hours() -> None:
    madrid = ZoneInfo('Europe/Madrid')
    start = time(9, 0)
    end = time(21, 0)

    # 1. Dentro de horario: 10:00 + 30 min -> 10:30 hoy
    t1 = datetime(2026, 10, 18, 10, 0, tzinfo=madrid)
    res1 = compute_retry_scheduled_at(t1, timedelta(minutes=30), start, end)
    assert res1 == datetime(2026, 10, 18, 10, 30, tzinfo=madrid)

    # 2. Fuera de horario (tarde): 20:45 + 30 min -> 09:00 mañana
    t2 = datetime(2026, 10, 18, 20, 45, tzinfo=madrid)
    res2 = compute_retry_scheduled_at(t2, timedelta(minutes=30), start, end)
    assert res2 == datetime(2026, 10, 19, 9, 0, tzinfo=madrid)

    # 3. Fuera de horario (temprano): 06:00 + 30 min -> 09:00 hoy
    t3 = datetime(2026, 10, 18, 6, 0, tzinfo=madrid)
    res3 = compute_retry_scheduled_at(t3, timedelta(minutes=30), start, end)
    assert res3 == datetime(2026, 10, 18, 9, 0, tzinfo=madrid)

    # 4. Sin franja horaria configurada: 12:00 + 1 h -> 13:00
    t4 = datetime(2026, 10, 18, 12, 0, tzinfo=madrid)
    res4 = compute_retry_scheduled_at(t4, timedelta(hours=1), None, None)
    assert res4 == datetime(2026, 10, 18, 13, 0, tzinfo=madrid)


# Valida que make seed inicialice los contratos de acme, beta y gamma según la tabla de contratos.
def test_seed_command_loads_contract_data() -> None:
    call_command('seed')
    acme = Tenant.objects.get(name='acme')
    assert acme.max_attempts == 3
    assert acme.retry_delay == timedelta(minutes=30)
    assert acme.calling_hours_start == time(9, 0)
    assert acme.calling_hours_end == time(21, 0)

    beta = Tenant.objects.get(name='beta')
    assert beta.max_attempts == 2
    assert beta.retry_delay == timedelta(hours=2)
    assert beta.calling_hours_start == time(10, 0)
    assert beta.calling_hours_end == time(20, 0)

    gamma = Tenant.objects.get(name='gamma')
    assert gamma.max_attempts == 1
    assert gamma.retry_delay == timedelta(0)
    assert gamma.calling_hours_start is None
    assert gamma.calling_hours_end is None