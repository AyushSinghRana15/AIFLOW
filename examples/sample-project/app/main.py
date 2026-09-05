"""HTTP entrypoint."""
from fastapi import FastAPI

from app.agents import AnswererAgent, SupervisorAgent

api = FastAPI()
supervisor = SupervisorAgent()
answerer = AnswererAgent()


@api.post("/support")
def handle_support_request(query: str) -> dict:
    route = supervisor.decide(query)
    answer = answerer.answer(query, route)
    return {"route": route, "answer": answer}
