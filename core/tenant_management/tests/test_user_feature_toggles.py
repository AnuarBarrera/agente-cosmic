import secrets
import pytest
from django.contrib.auth import get_user_model

User = get_user_model()

pytestmark = pytest.mark.django_db


def test_new_user_has_reels_and_carousel_enabled_by_default():
    pwd = f"T3st-{secrets.token_urlsafe(10)}!"
    email = f'newuser-{secrets.token_hex(4)}@test.com'
    user = User.objects.create_user(email=email, password=pwd, username=email)
    assert user.reels_enabled is True
    assert user.carousel_enabled is True
