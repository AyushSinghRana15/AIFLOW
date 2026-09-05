"""Detection signatures for the Python analyzer.

Kept as data rather than scattered through the walker so that adding support
for a provider is an edit to a table, and so that the confidence attached to
each rule is visible and reviewable in one place.

Confidences are deliberately conservative. A call whose shape is unambiguous
(``client.messages.create``) scores high; a heuristic (a module-level string
that merely looks like a prompt) scores low. Everything the analyzer emits
carries `static_analysis` provenance and the confidence of the rule that
matched, so a consumer can always tell how firm a claim is.
"""
from __future__ import annotations

# -- LLM invocations ---------------------------------------------------------
# Matched against the dotted attribute chain of a call, by suffix.
LLM_CALLS = [
    ("messages.create",            "anthropic", 0.97),
    ("messages.stream",            "anthropic", 0.97),
    ("beta.messages.create",       "anthropic", 0.95),
    ("chat.completions.create",    "openai",    0.97),
    ("responses.create",           "openai",    0.93),
    ("completions.create",         "openai",    0.90),
    ("generate_content",           "google",    0.90),
    ("chat.send_message",          "google",    0.85),
    ("converse",                   "bedrock",   0.80),
    ("invoke_model",               "bedrock",   0.85),
]

# -- LLM client constructors -------------------------------------------------
# Matched only when a `model=` argument is present. `OpenAI()` is the raw SDK
# client; `OpenAI(model="gpt-4o")` is a configured model, and the difference is
# the argument. Framework wrappers are unambiguous and score higher.
LLM_CLIENTS = [
    ("ChatAnthropic",          "anthropic", 0.93),
    ("ChatOpenAI",             "openai",    0.93),
    ("AzureChatOpenAI",        "azure",     0.91),
    ("ChatGoogleGenerativeAI", "google",    0.91),
    ("ChatVertexAI",           "google",    0.90),
    ("ChatBedrock",            "bedrock",   0.90),
    ("ChatMistralAI",          "mistral",   0.90),
    ("ChatOllama",             "ollama",    0.90),
    ("ChatCohere",             "cohere",    0.90),
    ("ChatGroq",               "groq",      0.90),
    ("Anthropic",              "anthropic", 0.78),
    ("OpenAI",                 "openai",    0.78),
    ("Gemini",                 "google",    0.80),
    ("Ollama",                 "ollama",    0.80),
]

# -- Vector stores -----------------------------------------------------------
VECTOR_STORES = [
    ("chromadb.PersistentClient",       "chroma",     0.92),
    ("chromadb.Client",                 "chroma",     0.92),
    ("chromadb.HttpClient",             "chroma",     0.92),
    ("get_or_create_collection",        "chroma",     0.88),
    ("create_collection",               "chroma",     0.80),
    ("Pinecone",                        "pinecone",   0.90),
    ("pinecone.Index",                  "pinecone",   0.90),
    ("QdrantClient",                    "qdrant",     0.90),
    ("weaviate.connect_to_local",       "weaviate",   0.90),
    ("weaviate.connect_to_weaviate_cloud", "weaviate", 0.90),
    ("FAISS.from_documents",            "faiss",      0.90),
    ("FAISS.load_local",                "faiss",      0.90),
    ("PGVector",                        "pgvector",   0.88),
    ("VectorStoreIndex.from_documents",  "llamaindex", 0.88),
    ("VectorStoreIndex.from_vector_store", "llamaindex", 0.88),
]

# -- Retrieval operations ----------------------------------------------------
RETRIEVAL_CALLS = [
    ("similarity_search",           0.92),
    ("similarity_search_with_score", 0.92),
    ("as_retriever",                0.90),
    ("get_relevant_documents",      0.92),
    ("max_marginal_relevance_search", 0.90),
    ("query",                       0.70),   # generic; only counts on a known store
    ("search",                      0.60),
]

# -- Tool declarations -------------------------------------------------------
TOOL_DECORATORS = {
    "tool": 0.95, "function_tool": 0.95, "tool_function": 0.9,
    "openai_function": 0.9, "register_tool": 0.9, "agent_tool": 0.9,
}

# Keys that identify a dict literal as a tool schema.
TOOL_SCHEMA_KEYS = ({"name", "input_schema"}, {"name", "parameters"},
                    {"name", "description", "input_schema"})

# -- Web entrypoints ---------------------------------------------------------
ROUTE_DECORATORS = ("get", "post", "put", "patch", "delete", "route",
                    "websocket", "on_event")
ROUTE_FRAMEWORKS = {"fastapi": "FastAPI", "flask": "Flask", "starlette": "Starlette"}

# -- Model identifiers -------------------------------------------------------
MODEL_PREFIXES = {
    "claude-": "anthropic", "gpt-": "openai", "o1-": "openai", "o3-": "openai",
    "gemini-": "google", "mistral-": "mistral", "llama": "meta",
    "deepseek-": "deepseek", "grok-": "xai",
}

# -- Frameworks, detected from imports --------------------------------------
FRAMEWORKS = {
    "langgraph": "langgraph", "langchain": "langchain",
    "langchain_core": "langchain", "langchain_community": "langchain",
    "crewai": "crewai", "llama_index": "llamaindex",
    "agents": "openai-agents", "openai_agents": "openai-agents", "autogen": "autogen",
    "semantic_kernel": "semantic-kernel", "haystack": "haystack",
    "dspy": "dspy", "pydantic_ai": "pydantic-ai",
}

PROVIDER_MODULES = {
    "anthropic": "anthropic", "openai": "openai", "cohere": "cohere",
    "google": "google", "mistralai": "mistral", "boto3": "bedrock",
}

# Names that suggest a string constant is a prompt. Used only as a weak signal;
# a placeholder-bearing multi-line string scores on its shape instead.
PROMPT_NAME_HINTS = ("prompt", "template", "instruction", "system", "persona",
                     "preamble", "directive")

CONF_PROMPT_BY_NAME = 0.85
CONF_PROMPT_BY_SHAPE = 0.55
CONF_AGENT_CLASS = 0.88
CONF_RETRIEVER_CLASS = 0.85
CONF_ENTRYPOINT = 0.80
CONF_EDGE_SAME_SCOPE = 0.85
