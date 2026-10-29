-- Additive migration; safe to reapply without rebuilding public feeds.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='45s';
CREATE TABLE IF NOT EXISTS keyword_alerts (
 id BIGSERIAL PRIMARY KEY,
 email TEXT NOT NULL,
 keywords TEXT NOT NULL,
 excluded_words TEXT NOT NULL DEFAULT '',
 retailers TEXT NOT NULL DEFAULT '',
 min_price NUMERIC(10,2) NOT NULL DEFAULT 0,
 max_price NUMERIC(10,2) NOT NULL,
 min_discount INTEGER NOT NULL DEFAULT 0,
 token TEXT NOT NULL UNIQUE,
 confirm_token TEXT NOT NULL UNIQUE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 confirmed_at TIMESTAMPTZ,
 confirmation_sent_at TIMESTAMPTZ,
 cancelled_at TIMESTAMPTZ,
 checked_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS keyword_alert_deliveries (
 alert_id BIGINT NOT NULL REFERENCES keyword_alerts(id),
 -- IDs come from the worker's product query; avoid locking the busy products
 -- table during this additive migration.
 product_id BIGINT NOT NULL,
 price NUMERIC(10,2) NOT NULL,
 sent_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(alert_id, product_id)
);
CREATE INDEX IF NOT EXISTS keyword_alerts_pending ON keyword_alerts(created_at)
 WHERE confirmed_at IS NULL AND cancelled_at IS NULL;
CREATE INDEX IF NOT EXISTS keyword_alerts_email ON keyword_alerts(email);
ALTER TABLE keyword_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE keyword_alert_deliveries ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON keyword_alerts, keyword_alert_deliveries FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE keyword_alerts_id_seq FROM PUBLIC, anon, authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON keyword_alerts,keyword_alert_deliveries TO pricewatch;
GRANT USAGE,SELECT ON SEQUENCE keyword_alerts_id_seq TO pricewatch;
DROP POLICY IF EXISTS keyword_worker ON keyword_alerts;
CREATE POLICY keyword_worker ON keyword_alerts FOR ALL TO pricewatch USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS keyword_worker ON keyword_alert_deliveries;
CREATE POLICY keyword_worker ON keyword_alert_deliveries FOR ALL TO pricewatch USING (true) WITH CHECK (true);

CREATE OR REPLACE FUNCTION create_keyword_alert(
 p_email text, p_keywords text, p_excluded_words text, p_retailers text[],
 p_min_price numeric, p_max_price numeric, p_min_discount integer
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
 v_email text := lower(trim(p_email));
 v_keywords text := lower(regexp_replace(trim(p_keywords), '\s+', ' ', 'g'));
 v_excluded text := lower(regexp_replace(trim(coalesce(p_excluded_words,'')), '\s+', ' ', 'g'));
 v_retailers text;
 v_token text := gen_random_uuid()::text;
BEGIN
 IF v_email IS NULL OR length(v_email)>254 OR v_email !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' THEN
  RAISE EXCEPTION 'Enter a valid email address'; END IF;
 IF v_keywords IS NULL OR length(v_keywords)<2 OR length(v_keywords)>100 OR length(v_excluded)>100 THEN
  RAISE EXCEPTION 'Keywords must contain 2 to 100 characters'; END IF;
 IF p_max_price IS NULL OR p_max_price::text IN ('NaN','Infinity','-Infinity') OR p_max_price<=0 OR p_max_price>100000
  OR p_min_price IS NULL OR p_min_price::text IN ('NaN','Infinity','-Infinity') OR p_min_price<0 OR p_min_price>p_max_price
  OR p_min_discount IS NULL OR p_min_discount<0 OR p_min_discount>100 THEN
  RAISE EXCEPTION 'Enter a valid price and discount range'; END IF;
 IF p_retailers IS NULL OR cardinality(p_retailers)>13 OR EXISTS (
  SELECT 1 FROM unnest(p_retailers) r WHERE r IS NULL OR r NOT IN
  ('kmart','bigw','target','officeworks','jbhifi','goodguys','myer','supercheap','sephora','chemistwarehouse','booktopia','qbd','ikea')
 ) THEN RAISE EXCEPTION 'Choose valid stores'; END IF;
 SELECT coalesce(string_agg(r,',' ORDER BY r),'') INTO v_retailers FROM (SELECT DISTINCT unnest(p_retailers) r) stores;
 -- Serialize registrations so concurrent calls cannot bypass limits.
 PERFORM pg_advisory_xact_lock(64029101);
 IF (SELECT count(*) FROM keyword_alerts WHERE created_at>now()-interval '1 hour')>=100
  OR (SELECT count(*) FROM keyword_alerts WHERE email=v_email AND created_at>now()-interval '1 day')>=5
  OR (SELECT count(*) FROM keyword_alerts WHERE email=v_email AND cancelled_at IS NULL)>=20 THEN
  RAISE EXCEPTION 'Alert limit reached; try again later'; END IF;
 IF EXISTS (SELECT 1 FROM keyword_alerts WHERE email=v_email AND keywords=v_keywords
  AND excluded_words=v_excluded AND retailers=v_retailers AND min_price=p_min_price
  AND max_price=p_max_price AND min_discount=p_min_discount AND cancelled_at IS NULL
  AND (confirmed_at IS NOT NULL OR created_at>now()-interval '7 days')) THEN
  RAISE EXCEPTION 'This alert already exists'; END IF;
 INSERT INTO keyword_alerts(email,keywords,excluded_words,retailers,min_price,max_price,min_discount,token,confirm_token)
 VALUES(v_email,v_keywords,v_excluded,v_retailers,p_min_price,p_max_price,p_min_discount,v_token,gen_random_uuid()::text);
 -- Management token cannot confirm; only the emailed secret can activate.
 RETURN v_token;
END $$;

CREATE OR REPLACE FUNCTION confirm_keyword_alert(p_token text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE affected integer;
BEGIN
 UPDATE keyword_alerts SET confirmed_at=coalesce(confirmed_at,now())
 WHERE confirm_token=p_token AND cancelled_at IS NULL
 AND (confirmed_at IS NOT NULL OR created_at>now()-interval '7 days');
 GET DIAGNOSTICS affected=ROW_COUNT; RETURN affected>0;
END $$;
CREATE OR REPLACE FUNCTION cancel_keyword_alert(p_token text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE affected integer;
BEGIN
 UPDATE keyword_alerts SET cancelled_at=coalesce(cancelled_at,now()) WHERE token=p_token;
 GET DIAGNOSTICS affected=ROW_COUNT; RETURN affected>0;
END $$;
CREATE OR REPLACE FUNCTION keyword_alert_status(p_token text) RETURNS text
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
 SELECT CASE WHEN cancelled_at IS NOT NULL THEN 'cancelled'
 WHEN confirmed_at IS NOT NULL THEN 'active'
 WHEN created_at<now()-interval '7 days' THEN 'expired' ELSE 'pending' END
 FROM keyword_alerts WHERE token=p_token;
$$;
REVOKE ALL ON FUNCTION create_keyword_alert(text,text,text,text[],numeric,numeric,integer),
 confirm_keyword_alert(text),cancel_keyword_alert(text),keyword_alert_status(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION create_keyword_alert(text,text,text,text[],numeric,numeric,integer),
 confirm_keyword_alert(text),cancel_keyword_alert(text),keyword_alert_status(text) TO anon;
NOTIFY pgrst, 'reload schema';
COMMIT;
