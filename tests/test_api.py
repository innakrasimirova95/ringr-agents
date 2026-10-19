import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from calls.models import Call, CallResult, Campaign, CampaignOwnership, Contact, ScriptStep, Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def call() -> Call:
    tenant = Tenant.objects.create(name='acme')
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


# INSTRUCTIONS.md - Problema 1: "Las llamadas de las campañas grandes tardan en arrancar".
# Valida que call_context resuelva el historial en <= 5 consultas SQL eliminando el problema N+1.
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


# INSTRUCTIONS.md - Problema 2: "Facturación duplicada".
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


# INSTRUCTIONS.md - Problema 2: "Facturación duplicada".
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


# INSTRUCTIONS.md - Problema 2: "Facturación duplicada".
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


# INSTRUCTIONS.md - Problema 2: "Facturación duplicada".
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
