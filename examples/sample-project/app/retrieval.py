"""Knowledge-base retrieval."""
from app.stores import kb_collection

TOP_K = 6


class KBRetriever:
    """Embeds the query and pulls the most similar support articles."""

    def __init__(self, top_k: int = TOP_K):
        self.top_k = top_k

    def retrieve(self, query: str) -> list[dict]:
        results = kb_collection.query(query_texts=[query], n_results=self.top_k)
        return [
            {"id": doc_id, "text": text}
            for doc_id, text in zip(results["ids"][0], results["documents"][0])
        ]
