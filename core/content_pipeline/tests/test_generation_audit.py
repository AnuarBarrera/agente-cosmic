from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.brand_dna.models import AnalysisJob
from core.content_pipeline.audit import GenerationContext, record_generation_event
from core.content_pipeline.models import GenerationAuditEvent

pytestmark = pytest.mark.django_db


def test_event_is_correlated_hashed_capped_and_redacted():
    job = AnalysisJob.objects.create(email='owner@example.com')
    event = record_generation_event(
        GenerationContext(job_id=str(job.id), day_number=4, attempt=2),
        stage='product_photo_triage', decision='rejected',
        flags={'reason': 'dense_text'},
        prompt='email person@example.com tel +52 55 1234 5678 token=supersecret ' + ('x' * 700),
        response={'ok': False},
    )
    assert event.job == job
    assert event.attempt == 2
    assert event.prompt_hash
    assert len(event.prompt_preview) <= 500
    assert 'person@example.com' not in event.prompt_preview
    assert '55 1234 5678' not in event.prompt_preview
    assert 'supersecret' not in event.prompt_preview


def test_missing_context_is_backwards_compatible():
    assert record_generation_event(None, stage='legacy', decision='skipped') is None
    assert GenerationAuditEvent.objects.count() == 0


def test_purge_command_deletes_only_expired_events():
    job = AnalysisJob.objects.create(email='owner@example.com')
    old = GenerationAuditEvent.objects.create(job=job, stage='qc', decision='rejected')
    recent = GenerationAuditEvent.objects.create(job=job, stage='qc', decision='accepted')
    GenerationAuditEvent.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(days=91),
    )

    output = StringIO()
    call_command('purge_generation_audit_events', stdout=output)

    assert not GenerationAuditEvent.objects.filter(pk=old.pk).exists()
    assert GenerationAuditEvent.objects.filter(pk=recent.pk).exists()
    assert 'Eliminados 1' in output.getvalue()
