SET lock_timeout='0';
SET statement_timeout='15min';
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_crawl_queue_due
ON crawl_queue(retailer,last_scraped ASC NULLS FIRST,url) WHERE fails<3;
