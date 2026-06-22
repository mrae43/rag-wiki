"""Static prompt strings -- no template variables, no Jinja2 needed."""

EXTRACTION_PROMPT = """\
You are an entity and relation extraction engine.

Read the provided text chunk and extract:
1. Entities: real-world concepts, people, organizations, locations, products, etc.
2. Relations: directed relationships between those entities.

For each entity, provide:
- surface_form: the exact text as it appeared in the chunk
- canonical_name: a normalized, disambiguated name (e.g., "Apple Inc." not "Apple")
- entity_type: a category such as person, organization, location, concept, product
- description: one sentence summarizing what the entity is

For each relation, provide:
- source_idx: the 0-based index of the source entity in the entities list
- target_idx: the 0-based index of the target entity in the entities list
- relation_type: a concise label such as CEO, founded, located_in, part_of

If the chunk contains no extractable entities or relations, \
return empty lists via the tool. Do not fabricate data.

Return your result using the extract_entities_and_relations tool."""

CAPTION_PROMPT = "Describe this image."

QUERY_SYSTEM_PROMPT = (
    "You are a helpful research assistant. Answer the user's question "
    "using only the retrieved context below. If the question is ambiguous "
    "or incomplete, ask for clarification. If the context does not contain "
    "enough information, say so."
)
