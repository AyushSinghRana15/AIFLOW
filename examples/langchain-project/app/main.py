"""HTTP entrypoint."""
from fastapi import FastAPI

from app.chains import classify_chain, support_chain

api = FastAPI()


@api.post("/support")
def handle(question: str) -> dict:
    topic = classify_chain.invoke({"question": question})
    answer = support_chain.invoke({"question": question, "topic": topic})
    return {"topic": topic, "answer": answer}
