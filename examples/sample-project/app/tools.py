"""External tools available to the agent."""
import httpx

TICKET_API = "https://tickets.internal.acme.example/v1"


def lookup_ticket(ticket_id: str) -> dict:
    """Fetch the status and history of a support ticket by id."""
    response = httpx.get(f"{TICKET_API}/tickets/{ticket_id}", timeout=10)
    if response.status_code == 404:
        return {"error": "unknown ticket"}
    response.raise_for_status()
    return response.json()


TOOLS = [
    {
        "name": "lookup_ticket",
        "description": "Fetch the status and history of a support ticket by id.",
        "input_schema": {
            "type": "object",
            "properties": {"ticket_id": {"type": "string"}},
            "required": ["ticket_id"],
        },
    }
]
