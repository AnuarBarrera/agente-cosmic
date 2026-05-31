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
