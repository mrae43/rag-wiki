# ADR-0003: Caption non-text chunks to text and embed in a single vector space

## Status
Accepted

## Context
With the hybrid parsing pipeline (ADR-0002), chunks may be text, tables, images,
or equations when the optional MinerU path is used. These need to be embedded for
vector similarity search as part of retrieval.

Three approaches were considered:

1. **Caption-to-text**: a vision-capable LLM generates a textual description of
   each image (and tables/equations are serialized to text, e.g. markdown table
   or LaTeX), and all chunks — regardless of original modality — are embedded
   with a single text embedding model into one vector column.
2. **Multimodal embeddings**: use a model such as CLIP for images, stored in a
   separate embedding space/table from text chunks, requiring retrieval to query
   multiple spaces and merge results.
3. **No embeddings for non-text chunks**: store them as graph nodes/metadata only,
   reachable via entity/relation links but not vector similarity search.

## Decision
Use **caption-to-text** (option 1): every chunk, regardless of original modality,
ends up with a text representation that is embedded by a single text embedding
model into a single `embedding vector(N)` column in the `chunks` table.

## Rationale
- **Single schema, single index**: one `chunks` table with one `embedding` column
  and one pgvector index (e.g. HNSW) serves all retrieval, regardless of chunk
  type. Avoids maintaining separate embedding pipelines/spaces.
- **Uniform hybrid retrieval**: vector search + graph traversal both operate over
  the same `chunks`/`entities`/`relations` tables without special-casing by
  modality.
- **Reuses the captioning step**: the same LLM call that captions an image (or
  describes a table/equation) can also surface candidate entities/relations for
  the knowledge graph (ADR-0001), avoiding a second extraction pass.

## Consequences
- Image retrieval quality depends on caption quality — a caption is a lossy
  summary of an image, not the image itself. Acceptable at portfolio scale; not
  suitable for tasks needing fine-grained visual similarity search.
- Captioning adds an LLM call per non-text chunk during ingest (cost/latency),
  on top of any entity/relation extraction calls.
- If finer-grained visual search becomes a requirement later, a CLIP-based
  secondary embedding column could be added without breaking this schema — but
  is deliberately out of scope for now.
