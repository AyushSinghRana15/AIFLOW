"""Workflow wiring."""
from langgraph.graph import END, START, StateGraph

from app.nodes import answer, classify, lookup, retrieve, route_question
from app.state import SupportState

builder = StateGraph(SupportState)

builder.add_node("classify", classify)
builder.add_node("retrieve", retrieve)
builder.add_node("lookup", lookup)
builder.add_node("answer", answer)

builder.add_edge(START, "classify")

builder.add_conditional_edges(
    "classify",
    route_question,
    {
        "knowledge_base": "retrieve",
        "ticket": "lookup",
        "direct": "answer",
    },
)

builder.add_edge("retrieve", "answer")
builder.add_edge("lookup", "answer")
builder.add_edge("answer", END)

graph = builder.compile()
