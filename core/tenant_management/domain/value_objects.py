import uuid
from dataclasses import dataclass, field
from enum import Enum


class SubscriptionPlan(Enum):
    FREE = "free"
    BASIC = "basic"
    PREMIUM = "premium"


@dataclass(frozen=True)
class UsageLimits:
    max_conversations: int
    max_messages_per_month: int


@dataclass(frozen=True)
class BillingInfo:
    card_holder: str
    last_4_digits: str
    expiry_date: str