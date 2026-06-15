import pytest
from unittest.mock import patch, MagicMock
from django.conf import settings
from core.tenant_management.infrastructure.adapters import NotificationServiceAdapter
import requests
import logging

# Logger fixture removed - now using mock logger directly

@pytest.mark.django_db
class TestNotificationServiceAdapter:

    @patch.object(settings, 'MAILGUN_API_KEY', 'test-api-key')
    @patch.object(settings, 'MAILGUN_DOMAIN', 'test.com')
    @patch.object(settings, 'MAILGUN_SENDER_EMAIL', 'sender@test.com')
    @patch('requests.post')
    def test_send_notification_with_mailgun_success(self, mock_requests_post):
        adapter = NotificationServiceAdapter()
        tenant_id = "123"
        message = "Test message"
        recipient_email = "recipient@test.com"
        subject = "Test Subject"

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None # Simulate success
        mock_requests_post.return_value = mock_response

        adapter.send_notification(tenant_id, message, recipient_email, subject)

        mock_requests_post.assert_called_once_with(
            "https://api.mailgun.net/v3/test.com/messages",
            auth=("api", "test-api-key"),
            data={
                "from": "Chatbot <sender@test.com>",
                "to": ["recipient@test.com"],
                "subject": "Test Subject",
                "text": "Test message"
            },
            timeout=10,
        )

    @patch.object(settings, 'MAILGUN_API_KEY', 'test-api-key')
    @patch.object(settings, 'MAILGUN_DOMAIN', 'test.com')
    @patch.object(settings, 'MAILGUN_SENDER_EMAIL', 'sender@test.com')
    @patch('core.tenant_management.infrastructure.adapters.logger')
    @patch('requests.post')
    def test_send_notification_mailgun_api_error(self, mock_requests_post, mock_logger):
        adapter = NotificationServiceAdapter()
        tenant_id = "123"
        message = "Test message"
        recipient_email = "recipient@test.com"
        subject = "Test Subject"

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.RequestException("API Error")
        mock_requests_post.return_value = mock_response
        
        adapter.send_notification(tenant_id, message, recipient_email, subject)

        mock_requests_post.assert_called_once()
        mock_logger.error.assert_called()
        error_call_args = mock_logger.error.call_args[0][0]
        assert "Error sending Mailgun notification" in error_call_args

    @patch.object(settings, 'MAILGUN_API_KEY', None)
    @patch.object(settings, 'MAILGUN_DOMAIN', None)
    @patch.object(settings, 'MAILGUN_SENDER_EMAIL', None)
    @patch('core.tenant_management.infrastructure.adapters.logger')
    @patch('requests.post')
    def test_send_notification_missing_mailgun_config(self, mock_requests_post, mock_logger):
        adapter = NotificationServiceAdapter()
        tenant_id = "123"
        message = "Test message"
        recipient_email = "recipient@test.com"
        subject = "Test Subject"

        adapter.send_notification(tenant_id, message, recipient_email, subject)

        mock_requests_post.assert_not_called()
        mock_logger.error.assert_called()
        error_call_args = mock_logger.error.call_args[0][0]
        assert "Mailgun API key, domain, or sender email not configured" in error_call_args
