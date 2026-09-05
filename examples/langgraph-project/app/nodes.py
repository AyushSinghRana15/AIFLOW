"""Graph node implementations."""
import anthropic

from app.prompts import ANSWER_PROMPT, CLASSIFY_PROMPT
from app.state import SupportState
from app.stores import kb
from app.tools import fetch_ticket

client = anthropic.Anthropic()

ROUTER_MODEL = "claude-haiku-4-5-20251001"
ANSWER_MODEL = "claude-sonnet-4-5"
TOP_K = 6


def classify(state: SupportState) -> SupportState:
    """Decide how the incoming question should be handled."""
    message = client.messages.create(
        model=ROUTER_MODEL,
        max_tokens=16,
        messages=[{"role": "user",
                   "content": CLASSIFY_PROMPT.format(question=state["question"])}],
    )
    label = message.content[0].text.strip()
    return {"route": label if label in {"knowledge_base", "ticket", "direct"} else "direct"}


def retrieve(state: SupportState) -> SupportState:
    """Pull the most similar knowledge-base articles."""
    results = kb.query(query_texts=[state["question"]], n_results=TOP_K)
    return {"context": [{"id": i, "text": t}
                        for i, t in zip(results["ids"][0], results["documents"][0])]}


def lookup(state: SupportState) -> SupportState:
    """Fetch the referenced support ticket."""
    return {"ticket": fetch_ticket(state["question"])}


def answer(state: SupportState) -> SupportState:
    """Compose the final grounded reply."""
    message = client.messages.create(
        model=ANSWER_MODEL,
        max_tokens=1024,
        messages=[{"role": "user",
                   "content": ANSWER_PROMPT.format(
                       context=state.get("context") or state.get("ticket") or "",
                       question=state["question"])}],
    )
    return {"answer": message.content[0].text}


def route_question(state: SupportState) -> str:
    """Branch selector for the classifier."""
    return state["route"]
