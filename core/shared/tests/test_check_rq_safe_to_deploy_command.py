import pytest
from io import StringIO
from unittest.mock import patch, MagicMock
from django.core.management import call_command

pytestmark = pytest.mark.django_db

CMD_MODULE = 'core.shared.management.commands.check_rq_safe_to_deploy'


def test_reports_safe_when_no_jobs_running():
    out = StringIO()
    with patch(f'{CMD_MODULE}.django_rq') as mock_rq, \
         patch(f'{CMD_MODULE}.StartedJobRegistry') as MockRegistry:
        MockRegistry.return_value.get_job_ids.return_value = []
        call_command('check_rq_safe_to_deploy', stdout=out)
    assert 'Seguro reiniciar' in out.getvalue()


def test_exits_nonzero_when_job_running():
    out = StringIO()
    fake_job = MagicMock()
    fake_job.func_name = 'core.content_pipeline.tasks.generate_next_week'
    fake_job.args = ('calendar-id', 2)

    with patch(f'{CMD_MODULE}.django_rq') as mock_rq, \
         patch(f'{CMD_MODULE}.StartedJobRegistry') as MockRegistry, \
         patch(f'{CMD_MODULE}.Worker') as MockWorker:
        MockRegistry.return_value.get_job_ids.return_value = ['job-1']
        mock_rq.get_queue.return_value.fetch_job.return_value = fake_job
        MockWorker.all.return_value = []
        with pytest.raises(SystemExit) as exc_info:
            call_command('check_rq_safe_to_deploy', stdout=out)

    assert exc_info.value.code == 1
    assert 'NO reinicies' in out.getvalue()
    assert 'generate_next_week' in out.getvalue()
