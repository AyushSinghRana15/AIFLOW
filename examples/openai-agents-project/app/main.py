"""Entrypoint."""
import asyncio

from agents import Runner

from app.agents_def import triage_agent


async def main(question: str) -> str:
    result = await Runner.run(triage_agent, question)
    return result.final_output


if __name__ == "__main__":
    print(asyncio.run(main("where is my order?")))
