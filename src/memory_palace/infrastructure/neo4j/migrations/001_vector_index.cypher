CREATE VECTOR INDEX memory_embeddings IF NOT EXISTS
FOR (m:Memory) 
ON m.embedding
OPTIONS {indexConfig: {
  `vector.dimensions`: 1024,
  `vector.similarity_function`: 'cosine'
}}
