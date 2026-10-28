-- Run outside a transaction; both directions retain unknown discounts last.
SET lock_timeout='5s';
SET statement_timeout='15min';
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_discount_desc ON products
((CASE WHEN current_rrp > current_price AND current_price >= 0 THEN (current_rrp-current_price)*100.0/current_rrp ELSE NULL END) DESC NULLS LAST, retailer ASC, sku ASC,last_seen)
WHERE current_price IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_discount_asc ON products
((CASE WHEN current_rrp > current_price AND current_price >= 0 THEN (current_rrp-current_price)*100.0/current_rrp ELSE NULL END) ASC NULLS LAST, retailer ASC, sku ASC,last_seen)
WHERE current_price IS NOT NULL;
