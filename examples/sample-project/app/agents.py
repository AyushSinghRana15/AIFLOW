"""Agents."""
from app.config import ANSWER_MODEL, ROUTER_MODEL, client
from app.prompts import ANSWER_PROMPT, ROUTER_PROMPT
from app.retrieval import KBRetriever
from app.tools import TOOLS, lookup_ticket


class SupervisorAgent:
    """Decides how an incoming query should be handled."""

    def decide(self, query: str) -> str:
        message = client.messages.create(
            model=ROUTER_MODEL,
            max_tokens=16,
            messages=[{"role": "user", "content": ROUTER_PROMPT.format(query=query)}],
        )
        label = message.content[0].text.strip()
        if label not in {"knowledge_base", "ticket", "direct"}:
            return "direct"
        return label


class AnswererAgent:
    """Composes the final grounded reply."""

    def __init__(self):
        self.retriever = KBRetriever()

    def answer(self, query: str, route: str) -> str:
        context = ""
        if route == "knowledge_base":
            context = self.retriever.retrieve(query)
        elif route == "ticket":
            context = lookup_ticket(query)

        message = client.messages.create(
            model=ANSWER_MODEL,
            max_tokens=1024,
            tools=TOOLS,
            messages=[
                {
                    "role": "user",
                    "content": ANSWER_PROMPT.format(context=context, question=query),
                }
            ],
        )
        return message.content[0].text
