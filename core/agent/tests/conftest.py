import pytest


@pytest.fixture(scope='session')
def django_db_setup(django_test_environment, django_db_blocker):
    """
    Crea la BD de test con migraciones reales para el módulo agent.
    Necesario porque --nomigrations en pytest.ini impide crear las tablas del agente.
    """
    with django_db_blocker.unblock():
        from django.test.utils import setup_databases
        setup_databases(verbosity=0, interactive=False)
