"""LCEL chains."""
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableBranch
from langchain_openai import ChatOpenAI

CLASSIFY_TEMPLATE = """Classify the question as billing, technical, or other.

Question: {question}

Reply with one word."""

ANSWER_TEMPLATE = """You are an Acme support agent.
Answer the {topic} question below.

Question: {question}"""

classify_prompt = ChatPromptTemplate.from_template(CLASSIFY_TEMPLATE)
answer_prompt = ChatPromptTemplate.from_template(ANSWER_TEMPLATE)

router_model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
answer_model = ChatOpenAI(model="gpt-4o", temperature=0.2)

classify_chain = classify_prompt | router_model | StrOutputParser()

billing_chain = answer_prompt | answer_model | StrOutputParser()

technical_chain = answer_prompt | answer_model | StrOutputParser()

fallback_chain = answer_prompt | router_model | StrOutputParser()

support_chain = RunnableBranch(
    (lambda x: x["topic"] == "billing", billing_chain),
    (lambda x: x["topic"] == "technical", technical_chain),
    fallback_chain,
)
