"""Prompt constants."""

ROUTER_PROMPT = """Classify the incoming support query into exactly one of:
knowledge_base, ticket, direct.

Query: {query}

Respond with only the label."""

ANSWER_PROMPT = """You are an Acme support agent.
Use only the supplied context documents and cite their ids.

Context: {context}
Question: {question}"""
