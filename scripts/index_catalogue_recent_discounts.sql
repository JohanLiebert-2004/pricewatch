-- The trailing last_seen key lets the index reject stale prices before
-- fetching full product rows while retaining the existing sort/tie order.
SET lock_timeout='0';
SET statement_timeout='15min';
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_discount_recent_desc ON products
((CASE WHEN current_rrp > current_price AND current_price >= 0 THEN (current_rrp-current_price)*100.0/current_rrp ELSE NULL END) DESC NULLS LAST,retailer,sku,last_seen)
WHERE current_price IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_discount_recent_asc ON products
((CASE WHEN current_rrp > current_price AND current_price >= 0 THEN (current_rrp-current_price)*100.0/current_rrp ELSE NULL END) ASC NULLS LAST,retailer,sku,last_seen)
WHERE current_price IS NOT NULL;
DO $$
BEGIN
  IF (SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
      WHERE c.relname IN ('idx_products_discount_recent_desc','idx_products_discount_recent_asc')
      AND i.indisvalid) <> 2 THEN
    RAISE EXCEPTION 'Both replacement discount indexes must be valid';
  END IF;
END $$;
DROP INDEX CONCURRENTLY IF EXISTS idx_products_discount_desc;
DROP INDEX CONCURRENTLY IF EXISTS idx_products_discount_asc;
