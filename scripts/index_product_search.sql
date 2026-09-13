-- Run as the PostgreSQL administrator with psql, outside a transaction.
-- Supports product_search title/SKU substring predicates plus existing GTIN index.
\set ON_ERROR_STOP on
SET statement_timeout = '30min';
-- Concurrent validation can wait for long-running crawler transactions.
SET lock_timeout = '15min';
SET maintenance_work_mem = '64MB';
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- Resume safely if an earlier concurrent build was interrupted.
SELECT format('REINDEX INDEX CONCURRENTLY %I.%I', n.nspname, c.relname)
FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND NOT i.indisvalid
  AND c.relname IN ('idx_products_search_title_trgm','idx_products_search_sku_trgm')
\gexec
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_search_title_trgm
  ON products USING gin (title gin_trgm_ops) WHERE current_price IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_search_sku_trgm
  ON products USING gin (sku gin_trgm_ops) WHERE current_price IS NOT NULL;
ANALYZE products (retailer, title, sku, gtin, current_price);
