"""Tools available to the agents."""
import httpx
from agents import function_tool

TICKET_API = "https://tickets.internal.acme.example/v1"


@function_tool
def lookup_ticket(ticket_id: str) -> dict:
    """Fetch the status and history of a support ticket by id."""
    return httpx.get(f"{TICKET_API}/tickets/{ticket_id}", timeout=10).json()


@function_tool
def search_kb(query: str) -> list[dict]:
    """Search the support knowledge base."""
    return httpx.get(f"{TICKET_API}/kb", params={"q": query}, timeout=10).json()
