"""Tests del Sprint 7 — Investigación de prospectos."""
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings

pytestmark = pytest.mark.django_db


class TestProspectResearchTool:
    def test_execute_returns_brief_for_valid_prospect(self):
        from core.agent.infrastructure.tools.prospect_tools import ProspectResearchTool
        mock_gemini = MagicMock()
        mock_gemini.generate_response.return_value = (
            '## Brief de ventas\n\n'
            '**Fortalezas:** Presencia en redes, contenido frecuente.\n'
            '**Áreas de oportunidad:** Identidad visual inconsistente.\n'
            '**Ángulo recomendado:** Proponer rediseño de identidad.\n'
            '**Tono sugerido:** Directo y profesional.'
        )
        with patch('core.agent.infrastructure.tools.prospect_tools.GeminiAdapter', return_value=mock_gemini), \
             patch('core.agent.infrastructure.tools.prospect_tools._scrape_url', return_value='Contenido del sitio'), \
             override_settings(GEMINI_API_KEY='test-key'):
            tool = ProspectResearchTool()
            result = tool.execute(
                name='Tu Web MX',
                urls=['https://tuwebmx.com'],
            )
        assert result.success is True
        assert result.tool_name == 'prospect_research'
        assert 'Brief' in result.content or len(result.content) > 50

    def test_execute_returns_error_without_api_key(self):
        from core.agent.infrastructure.tools.prospect_tools import ProspectResearchTool
        with override_settings(GEMINI_API_KEY=''):
            tool = ProspectResearchTool()
            result = tool.execute(name='Cliente X', urls=[])
        assert result.success is False

    def test_execute_returns_error_when_no_name(self):
        from core.agent.infrastructure.tools.prospect_tools import ProspectResearchTool
        with override_settings(GEMINI_API_KEY='test-key'):
            tool = ProspectResearchTool()
            result = tool.execute(name='', urls=[])
        assert result.success is False

    def test_execute_sanitizes_scraped_content(self):
        from core.agent.infrastructure.tools.prospect_tools import ProspectResearchTool
        mock_gemini = MagicMock()
        mock_gemini.generate_response.return_value = 'Brief generado.'
        with patch('core.agent.infrastructure.tools.prospect_tools.GeminiAdapter', return_value=mock_gemini), \
             patch('core.agent.infrastructure.tools.prospect_tools._scrape_url', return_value='contenido') as mock_scrape, \
             patch('core.agent.infrastructure.tools.prospect_tools.scrape_guard') as mock_guard, \
             override_settings(GEMINI_API_KEY='test-key'):
            mock_guard.safe_external_content.return_value = 'contenido seguro'
            tool = ProspectResearchTool()
            result = tool.execute(name='Cliente', urls=['https://example.com'])
        mock_guard.safe_external_content.assert_called()
        assert result.success is True

    def test_execute_works_without_urls(self):
        from core.agent.infrastructure.tools.prospect_tools import ProspectResearchTool
        mock_gemini = MagicMock()
        mock_gemini.generate_response.return_value = 'Brief basado solo en nombre.'
        with patch('core.agent.infrastructure.tools.prospect_tools.GeminiAdapter', return_value=mock_gemini), \
             override_settings(GEMINI_API_KEY='test-key'):
            tool = ProspectResearchTool()
            result = tool.execute(name='Cliente X', urls=[])
        assert result.success is True

    def test_scrape_url_returns_string(self):
        from core.agent.infrastructure.tools.prospect_tools import _scrape_url
        with patch('core.agent.infrastructure.tools.prospect_tools.sync_playwright') as mock_pw:
            mock_page = MagicMock()
            mock_page.content.return_value = '<html><body>Hola mundo</body></html>'
            mock_browser = MagicMock()
            mock_browser.new_page.return_value = mock_page
            mock_chromium = MagicMock()
            mock_chromium.launch.return_value = mock_browser
            mock_pw.return_value.__enter__.return_value.chromium = mock_chromium
            result = _scrape_url('https://example.com')
        assert isinstance(result, str)
        assert 'Hola mundo' in result
