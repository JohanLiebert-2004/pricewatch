-- Run once against a fresh self-hosted Postgres instance, BEFORE schema.sql
-- and views.sql. On Supabase, the `anon` and `authenticated` roles are
-- pre-created by their platform - schema.sql/views.sql only ever
-- REVOKE/GRANT against them, never CREATE them. Self-hosting needs this
-- prerequisite step schema.sql was never responsible for.
--
-- `authenticator` is PostgREST's own connecting role (see
-- postgrest.conf.example's db-uri): it only needs LOGIN plus permission to
-- SET ROLE into anon per-request. It is NOT a superuser and NOT the same
-- role the crawler/detector use (they keep using the `postgres` role
-- directly via DATABASE_URL, unchanged from today).

CREATE ROLE anon NOLOGIN NOINHERIT;
CREATE ROLE authenticated NOLOGIN NOINHERIT;

CREATE ROLE authenticator NOINHERIT LOGIN PASSWORD 'REPLACE_ME_WITH_GENERATED_PASSWORD';
GRANT anon TO authenticator;
GRANT authenticated TO authenticator;
