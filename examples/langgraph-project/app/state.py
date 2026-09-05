"""Graph state."""
from typing import TypedDict


class SupportState(TypedDict, total=False):
    question: str
    route: str
    context: list[dict]
    ticket: dict
    answer: str
