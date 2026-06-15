class BillingSystemAdapter:
    def process_payment(self, tenant_id: str, amount: float) -> dict:
        print(f"[Adapter] Procesando pago de {amount} para tenant {tenant_id}.")
        return {"status": "success", "transaction_id": "txn-123"}

import requests
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

class NotificationServiceAdapter:
    def send_notification(self, tenant_id: str, message: str, recipient_email: str, subject: str):
        api_key = settings.MAILGUN_API_KEY
        domain = settings.MAILGUN_DOMAIN
        sender_email = settings.MAILGUN_SENDER_EMAIL

        if not api_key or not domain or not sender_email:
            logger.error("Mailgun API key, domain, or sender email not configured. Cannot send notification.")
            print(f"[Adapter] Mailgun no configurado. Notificación a tenant {tenant_id} no enviada.")
            return

        try:
            response = requests.post(
                f"https://api.mailgun.net/v3/{domain}/messages",
                auth=("api", api_key),
                data={
                    "from": f"Chatbot <{sender_email}>",
                    "to": [recipient_email],
                    "subject": subject,
                    "text": message
                },
                timeout=10,
            )
            response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
            logger.info(f"Mailgun notification sent successfully to {recipient_email} for tenant {tenant_id}.")
            print(f"[Adapter] Notificación Mailgun enviada a {recipient_email} para tenant {tenant_id}.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending Mailgun notification to {recipient_email} for tenant {tenant_id}: {e}", exc_info=True)
            print(f"[Adapter] Error al enviar notificación Mailgun a {recipient_email} para tenant {tenant_id}: {e}")
