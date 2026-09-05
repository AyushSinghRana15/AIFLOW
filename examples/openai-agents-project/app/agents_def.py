"""Agent definitions and handoffs."""
from agents import Agent

from app.tools import lookup_ticket, search_kb

KB_INSTRUCTIONS = """Answer the customer's question using only the knowledge base.
Cite the article ids you used."""

TICKET_INSTRUCTIONS = """Look up the referenced ticket and summarise its status."""

TRIAGE_INSTRUCTIONS = """Decide whether the question is about a known issue, a
specific ticket, or neither, and hand off accordingly."""

kb_agent = Agent(
    name="Knowledge base agent",
    instructions=KB_INSTRUCTIONS,
    model="gpt-4o-mini",
    tools=[search_kb],
)

ticket_agent = Agent(
    name="Ticket agent",
    instructions=TICKET_INSTRUCTIONS,
    model="gpt-4o-mini",
    tools=[lookup_ticket],
)

triage_agent = Agent(
    name="Triage agent",
    instructions=TRIAGE_INSTRUCTIONS,
    model="gpt-4o",
    handoffs=[kb_agent, ticket_agent],
)
