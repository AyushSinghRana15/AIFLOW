"""Vector store."""
import chromadb

chroma = chromadb.PersistentClient(path="./.chroma")

kb = chroma.get_or_create_collection(name="acme-kb", metadata={"hnsw:space": "cosine"})
