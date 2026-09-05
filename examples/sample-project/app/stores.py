"""Vector store wiring."""
import chromadb

chroma_client = chromadb.PersistentClient(path="./.chroma")

kb_collection = chroma_client.get_or_create_collection(
    name="acme-support-kb",
    metadata={"hnsw:space": "cosine"},
)
