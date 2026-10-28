-- Run outside a transaction. Preserve application query timeouts.
SET lock_timeout='0';
SET statement_timeout='15min';
-- Remove only our own incomplete builds before retrying, never a valid index.
SELECT 'DROP INDEX CONCURRENTLY ' || quote_ident(c.relname) || ';'
FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
WHERE NOT i.indisvalid AND c.relname IN
('idx_products_retailer_seen','idx_products_missing_subcategory',
 'idx_products_missing_category','idx_snapshots_scraped_product')
AND NOT EXISTS (SELECT 1 FROM pg_stat_progress_create_index p WHERE p.index_relid=c.oid)
\gexec
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_retailer_seen
ON products(retailer,last_seen,sku) WHERE current_price IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_missing_subcategory
ON products(retailer,id) WHERE subcategory IS NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_missing_category
ON products(id) WHERE category IS NULL OR category='';
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_snapshots_scraped_product
ON price_snapshots(scraped_at,product_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_book_review
ON products(id) WHERE category='books'
AND (gtin IS NULL OR (gtin NOT LIKE '978%' AND gtin NOT LIKE '979%'));
