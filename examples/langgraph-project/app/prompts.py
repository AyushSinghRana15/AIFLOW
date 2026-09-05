"""Prompt constants."""

CLASSIFY_PROMPT = """Classify the support question into exactly one of:
knowledge_base, ticket, direct.

Question: {question}

Reply with only the label."""

ANSWER_PROMPT = """You are an Acme support agent.
Ground every claim in the supplied context and cite document ids.

Context: {context}
Question: {question}"""
