# core/agent/tests/test_sprint13_prospector.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from django.test import override_settings
from django.utils import timezone

pytestmark = pytest.mark.django_db


class TestProspectLead:
    def test_unique_per_user_and_place(self):
        """Mismo place_id para el mismo chat_id lanza IntegrityError."""
        from core.agent.infrastructure.models import ProspectLead
        from django.db import IntegrityError
        ProspectLead.objects.create(
            place_id='ChIJXXX', chat_id='123', name='Plomería ABC',
            giro='plomeros', lat=25.67, lng=-100.31,
        )
        with pytest.raises(IntegrityError):
            ProspectLead.objects.create(
                place_id='ChIJXXX', chat_id='123', name='Plomería ABC duplicada',
                giro='plomeros', lat=25.67, lng=-100.31,
            )

    def test_different_users_can_have_same_place(self):
        """Mismo place_id para distinto chat_id es válido."""
        from core.agent.infrastructure.models import ProspectLead
        ProspectLead.objects.create(
            place_id='ChIJYYY', chat_id='111', name='Salon A', giro='salones',
        )
        ProspectLead.objects.create(
            place_id='ChIJYYY', chat_id='222', name='Salon A', giro='salones',
        )
        assert ProspectLead.objects.filter(place_id='ChIJYYY').count() == 2

    def test_contacted_defaults_to_false(self):
        from core.agent.infrastructure.models import ProspectLead
        lead = ProspectLead.objects.create(
            place_id='ChIJZZZ', chat_id='123', name='Test', giro='test',
        )
        assert lead.contacted is False
        assert lead.contacted_at is None

    def test_score_is_nullable(self):
        from core.agent.infrastructure.models import ProspectLead
        lead = ProspectLead.objects.create(
            place_id='ChIJAAA', chat_id='123', name='Test', giro='test',
        )
        assert lead.score is None


class TestProspectN8nJobDedup:
    def _make_lead(self, place_id='ChIJAAA', name='Test'):
        return {
            'place_id': place_id,
            'name': name,
            'address': 'Calle 1',
            'phone': '8181234567',
            'website': '',
            'rating': 4.2,
            'reviews_total': 50,
            'lat': 25.67,
            'lng': -100.31,
        }

    def test_stores_new_leads(self):
        from core.agent.infrastructure.models import ProspectLead
        leads = [self._make_lead('PlaceA'), self._make_lead('PlaceB')]
        with patch('core.agent.infrastructure.jobs.requests.post') as mock_n8n, \
             patch('core.agent.infrastructure.jobs._send_telegram'), \
             patch('core.agent.infrastructure.jobs._score_leads_with_gemini',
                   side_effect=lambda leads, giro: leads):
            mock_n8n.return_value.json.return_value = {'total': 2, 'leads': leads}
            mock_n8n.return_value.raise_for_status = lambda: None
            from core.agent.infrastructure.jobs import prospect_n8n_job
            prospect_n8n_job(giro='plomeros', lat=25.67, lng=-100.31,
                             rango_km=5.0, chat_id=123)
        assert ProspectLead.objects.filter(chat_id='123').count() == 2

    def test_skips_duplicate_place_ids(self):
        from core.agent.infrastructure.models import ProspectLead
        ProspectLead.objects.create(
            place_id='PlaceA', chat_id='123', name='Ya existe', giro='plomeros',
        )
        leads = [self._make_lead('PlaceA'), self._make_lead('PlaceB')]
        with patch('core.agent.infrastructure.jobs.requests.post') as mock_n8n, \
             patch('core.agent.infrastructure.jobs._send_telegram'), \
             patch('core.agent.infrastructure.jobs._score_leads_with_gemini',
                   side_effect=lambda leads, giro: leads):
            mock_n8n.return_value.json.return_value = {'total': 2, 'leads': leads}
            mock_n8n.return_value.raise_for_status = lambda: None
            from core.agent.infrastructure.jobs import prospect_n8n_job
            prospect_n8n_job(giro='plomeros', lat=25.67, lng=-100.31,
                             rango_km=5.0, chat_id=123)
        assert ProspectLead.objects.filter(chat_id='123').count() == 2
        new_lead = ProspectLead.objects.get(place_id='PlaceB', chat_id='123')
        assert new_lead.name == 'Test'

    def test_sends_telegram_with_new_count(self):
        leads = [self._make_lead('PlaceX')]
        with patch('core.agent.infrastructure.jobs.requests.post') as mock_n8n, \
             patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg, \
             patch('core.agent.infrastructure.jobs._score_leads_with_gemini',
                   side_effect=lambda leads, giro: leads):
            mock_n8n.return_value.json.return_value = {'total': 1, 'leads': leads}
            mock_n8n.return_value.raise_for_status = lambda: None
            from core.agent.infrastructure.jobs import prospect_n8n_job
            prospect_n8n_job(giro='plomeros', lat=25.67, lng=-100.31,
                             rango_km=5.0, chat_id=456)
        mock_tg.assert_called_once()
        msg = mock_tg.call_args[0][1]
        assert '1' in msg


class TestScoreLeadsWithGemini:
    def _leads(self):
        return [
            {'place_id': 'A', 'name': 'Sin web', 'phone': '8181234567',
             'website': '', 'rating': 4.5, 'reviews_total': 100},
            {'place_id': 'B', 'name': 'Con web', 'phone': '',
             'website': 'https://example.com', 'rating': 3.0, 'reviews_total': 5},
        ]

    def test_adds_score_field_to_each_lead(self):
        from core.agent.infrastructure.gemini_adapter import GeminiAdapter
        import json
        fake_scores = json.dumps([
            {'score': 9, 'reason': 'sin web, teléfono disponible'},
            {'score': 4, 'reason': 'tiene web, sin teléfono'},
        ])
        with patch.object(GeminiAdapter, 'generate_response', return_value=fake_scores), \
             override_settings(GEMINI_API_KEY='key'):
            from core.agent.infrastructure.jobs import _score_leads_with_gemini
            result = _score_leads_with_gemini(self._leads(), 'plomeros')
        assert result[0]['score'] == 9
        assert result[0]['score_reason'] == 'sin web, teléfono disponible'
        assert result[1]['score'] == 4

    def test_uses_default_score_5_on_gemini_error(self):
        from core.agent.infrastructure.gemini_adapter import GeminiAdapter
        with patch.object(GeminiAdapter, 'generate_response', return_value='invalid json'), \
             override_settings(GEMINI_API_KEY='key'):
            from core.agent.infrastructure.jobs import _score_leads_with_gemini
            result = _score_leads_with_gemini(self._leads(), 'plomeros')
        assert all(l.get('score') == 5 for l in result)


@pytest.mark.django_db(transaction=True)
class TestFollowUp:
    @pytest.fixture
    def fake_update(self):
        update = MagicMock()
        update.effective_user.id = 1
        update.effective_user.username = 'user'
        update.effective_user.full_name = 'User'
        update.effective_chat.id = 999
        update.message.reply_text = AsyncMock()
        return update

    def test_cmd_contactado_marks_recent_leads(self, fake_update):
        """cmd_contactado marca todos los leads no-contactados del usuario como contactados."""
        from core.agent.infrastructure.models import ProspectLead
        ProspectLead.objects.create(
            place_id='Lead1', chat_id='999', name='N1', giro='plomeros', contacted=False,
        )
        ProspectLead.objects.create(
            place_id='Lead2', chat_id='999', name='N2', giro='plomeros', contacted=False,
        )
        context = MagicMock()
        context.args = []
        with patch('core.agent.management.commands.run_telegram_bot.get_or_create_session') as mock_sess:
            mock_sess.return_value = MagicMock(is_authorized=True, role='admin', id=1)
            from core.agent.management.commands.run_telegram_bot import cmd_contactado
            asyncio.get_event_loop().run_until_complete(cmd_contactado(fake_update, context))
        assert ProspectLead.objects.filter(chat_id='999', contacted=False).count() == 0
        assert ProspectLead.objects.filter(chat_id='999', contacted=True).count() == 2
