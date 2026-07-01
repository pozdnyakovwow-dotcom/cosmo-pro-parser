from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from dateutil import parser as date_parser


def clean_rating(value) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    text = str(value).strip().replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def clean_datetime(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return date_parser.parse(str(value), dayfirst=True)
    except (ValueError, TypeError, OverflowError):
        return None


def validate_review_payload(payload: dict) -> list[str]:
    errors: list[str] = []
    if not payload.get("doctor_name"):
        errors.append("doctor_name is required")
    if not payload.get("profile_url"):
        errors.append("profile_url is required")
    if not payload.get("review_text") and payload.get("rating") is None and not payload.get("published_at"):
        errors.append("review payload must contain text, rating, or publication date")
    return errors
