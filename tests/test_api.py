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


def test_result_is_stored(client: Client, call: Call) -> None:
    response = client.post(
        f'/calls/{call.id}/result',
        {'outcome': 'completed', 'duration_s': 95.0, 'transcript': 'user: Hello'},
        content_type='application/json',
    )

    assert response.status_code == 200
    assert CallResult.objects.get(call=call).outcome == 'completed'
