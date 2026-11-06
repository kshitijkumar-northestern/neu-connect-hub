-- The application database (POSTGRES_DB) is created by the image itself.
-- The API test suite runs against a separate database so tests never touch dev data.
-- The pgvector extension is enabled by the first Alembic migration, not here.
CREATE DATABASE nu_hub_test;
