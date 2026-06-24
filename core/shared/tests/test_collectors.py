import pytest
from unittest.mock import patch, MagicMock
from core.shared.metrics import RQJobsCollector, ActiveUsersCollector


class TestRQJobsCollector:
    def test_collect_returns_gauge_family(self):
        collector = RQJobsCollector()
        mock_queue = MagicMock()
        mock_queue.count = 5
        mock_queue.started_job_registry.count = 2
        mock_queue.finished_job_registry.count = 10
        mock_queue.failed_job_registry.count = 1

        with patch('django_rq.get_queue', return_value=mock_queue):
            metrics = list(collector.collect())

        assert len(metrics) == 1
        metric = metrics[0]
        assert metric.name == 'cosmic_rq_jobs'
        samples = {s.labels['state']: s.value for s in metric.samples}
        assert samples['queued'] == 5
        assert samples['started'] == 2
        assert samples['finished'] == 10
        assert samples['failed'] == 1

    def test_collect_handles_error(self):
        collector = RQJobsCollector()
        with patch('django_rq.get_queue', side_effect=Exception('redis down')):
            metrics = list(collector.collect())
        assert metrics == []


@pytest.mark.django_db
class TestActiveUsersCollector:
    def test_collect_returns_gauge_family(self):
        collector = ActiveUsersCollector()
        with patch('django.contrib.auth.get_user_model') as mock_get:
            mock_get.return_value.objects.filter.return_value.count.return_value = 7
            metrics = list(collector.collect())

        assert len(metrics) == 1
        assert metrics[0].samples[0].value == 7

    def test_collect_handles_error(self):
        collector = ActiveUsersCollector()
        with patch('django.contrib.auth.get_user_model') as mock_get:
            mock_get.return_value.objects.filter.side_effect = Exception('db error')
            metrics = list(collector.collect())
        assert metrics == []
