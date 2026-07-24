import pytest
from io import StringIO
from django.core.management import call_command
from django.test import TestCase

@pytest.mark.django_db
class TestManagementCommands(TestCase):
    def test_reset_daily_usage_command(self):
        """
        Verifica que el comando reset_daily_usage se ejecuta sin errores.
        """
        out = StringIO()
        call_command('reset_daily_usage', stdout=out)
        self.assertIn('Process finished successfully.', out.getvalue())

    def test_expire_stale_trials_command(self):
        """
        Verifica que el comando expire_stale_trials se ejecuta sin errores.
        """
        from unittest.mock import patch
        with patch('core.content_pipeline.tasks.expire_stale_trials_task') as mock_task:
            call_command('expire_stale_trials')
        mock_task.assert_called_once()

