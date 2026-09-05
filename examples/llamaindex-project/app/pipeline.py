"""Query pipeline."""
from llama_index.core import VectorStoreIndex
from llama_index.core.llms import OpenAI
from llama_index.core.prompts import PromptTemplate
from llama_index.core.query_pipeline import InputComponent, QueryPipeline

ANSWER_TEMPLATE = PromptTemplate(
    "Answer the question using only the context.\n"
    "Context: {context_str}\n"
    "Question: {query_str}\n"
)

index = VectorStoreIndex.from_documents([])
retriever = index.as_retriever(similarity_top_k=6)
llm = OpenAI(model="gpt-4o-mini", temperature=0)

pipeline = QueryPipeline(verbose=True)

pipeline.add_modules({
    "input": InputComponent(),
    "retriever": retriever,
    "prompt": ANSWER_TEMPLATE,
    "llm": llm,
})

pipeline.add_link("input", "retriever")
pipeline.add_link("retriever", "prompt", dest_key="context_str")
pipeline.add_link("input", "prompt", dest_key="query_str")
pipeline.add_link("prompt", "llm")
