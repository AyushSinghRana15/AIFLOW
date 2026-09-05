"""Support crew."""
from crewai import Agent, Crew, Process, Task

RESEARCH_GOAL = "Find every knowledge-base article relevant to the customer question."
WRITER_GOAL = "Write a grounded reply citing the articles the researcher found."
REVIEW_GOAL = "Check the reply cites only articles that were actually retrieved."

researcher = Agent(
    role="Support researcher",
    goal=RESEARCH_GOAL,
    backstory="Knows the knowledge base inside out.",
    llm="gpt-4o-mini",
)

writer = Agent(
    role="Support writer",
    goal=WRITER_GOAL,
    backstory="Writes clear, cited answers.",
    llm="gpt-4o",
)

reviewer = Agent(
    role="Reviewer",
    goal=REVIEW_GOAL,
    backstory="Catches ungrounded claims.",
    llm="gpt-4o-mini",
)

research_task = Task(
    description="Search the knowledge base for {question}.",
    expected_output="A list of relevant article ids with excerpts.",
    agent=researcher,
)

draft_task = Task(
    description="Draft a reply to {question} using the retrieved articles.",
    expected_output="A drafted reply with citations.",
    agent=writer,
    context=[research_task],
)

review_task = Task(
    description="Verify every citation in the draft.",
    expected_output="The approved reply, or a list of problems.",
    agent=reviewer,
    context=[draft_task],
)

crew = Crew(
    agents=[researcher, writer, reviewer],
    tasks=[research_task, draft_task, review_task],
    process=Process.sequential,
)
