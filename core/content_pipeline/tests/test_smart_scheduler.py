import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock
from core.content_pipeline.smart_scheduler import detect_industry, smart_schedule_dates


class TestDetectIndustry:
    def test_restaurant_keywords(self):
        dna = MagicMock()
        dna.tone = 'casual'
        dna.description = 'Restaurante de comida italiana con pastas artesanales'
        assert detect_industry(dna) == 'food'

    def test_fitness_keywords(self):
        dna = MagicMock()
        dna.tone = 'motivacional'
        dna.description = 'Gimnasio y entrenamiento personal para atletas'
        assert detect_industry(dna) == 'fitness'

    def test_retail_keywords(self):
        dna = MagicMock()
        dna.tone = 'elegante'
        dna.description = 'Tienda de ropa y accesorios de moda para mujer'
        assert detect_industry(dna) == 'retail'

    def test_default_fallback(self):
        dna = MagicMock()
        dna.tone = 'profesional'
        dna.description = 'Empresa de servicios generales'
        assert detect_industry(dna) == 'default'


class TestSmartScheduleDates:
    def test_returns_7_datetimes(self):
        dna = MagicMock()
        dna.tone = 'profesional'
        dna.description = 'Empresa de software'
        base = date(2026, 6, 2)  # lunes
        result = smart_schedule_dates(dna, base_date=base, count=7)
        assert len(result) == 7

    def test_first_slot_is_today(self):
        dna = MagicMock()
        dna.tone = 'casual'
        dna.description = 'Empresa de software'
        base = date(2026, 6, 2)
        result = smart_schedule_dates(dna, base_date=base, count=7)
        assert result[0].date() == base

    def test_no_duplicate_dates(self):
        dna = MagicMock()
        dna.tone = 'casual'
        dna.description = 'Empresa de software'
        base = date(2026, 6, 2)
        result = smart_schedule_dates(dna, base_date=base, count=7)
        dates_only = [r.date() for r in result]
        assert len(dates_only) == len(set(dates_only))
