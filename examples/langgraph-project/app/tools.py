"""External tools."""
import httpx

TICKET_API = "https://tickets.internal.acme.example/v1"


def fetch_ticket(ticket_id: str) -> dict:
    """Fetch a support ticket by id."""
    response = httpx.get(f"{TICKET_API}/tickets/{ticket_id}", timeout=10)
    if response.status_code == 404:
        return {"error": "unknown ticket"}
    return response.json()
