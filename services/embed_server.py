"""Tiny internal embedding service for natural-language search.

Lazy-loads the same fastembed model used by embed_products.py (sentence-
transformers/all-MiniLM-L6-v2, 384-dim, no API key) so a search query can be
embedded into the same vector space as products.embedding and compared with
pgvector. Kept in its own systemd unit/cgroup rather than loaded inside
preview_app.py: the OCI VM has ~1GB RAM total and pricewatch-web runs under
a 300M MemoryMax - onnxruntime plus the model would risk OOM-killing the
live SSR/image-proxy service if it shared that process.

Localhost-only (127.0.0.1) - preview_app.py is the only caller, over
loopback, and this port is never exposed through nginx.
"""
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
_model = None


class EmbedRequest(BaseModel):
    text: str


@app.post("/embed")
async def embed(req: EmbedRequest):
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
    text = (req.text or "").strip()[:500]
    if not text:
        return {"vector": None}
    vector = next(_model.embed([text]))
    return {"vector": [float(x) for x in vector]}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "loaded": _model is not None}
