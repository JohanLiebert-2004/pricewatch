-- Focused, additive migration. No materialized-view rebuild or new index.
-- Apply as the PostgreSQL administrator, not through the public API.
BEGIN;
CREATE OR REPLACE FUNCTION public.feed_price_history(p_items jsonb)
RETURNS TABLE(retailer text, sku text, points jsonb)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF jsonb_typeof(p_items) IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_items) > 24 THEN
    RAISE EXCEPTION 'Supply an array of at most 24 products' USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
  WITH requested AS (
    SELECT DISTINCT x->>'retailer' AS store, x->>'sku' AS code
    FROM jsonb_array_elements(p_items) x
    WHERE length(x->>'retailer') BETWEEN 1 AND 40
      AND length(x->>'sku') BETWEEN 1 AND 200
  )
  SELECT p.retailer, p.sku, coalesce(h.points, '[]'::jsonb)
  FROM requested r
  CROSS JOIN LATERAL (
    SELECT pr.id, pr.retailer, pr.sku, pr.current_price, pr.last_seen
    FROM public.products pr
    WHERE pr.retailer = r.store AND pr.sku = r.code
      AND coalesce(pr.region, '') = '' AND pr.current_price > 0
    ORDER BY pr.region NULLS LAST LIMIT 1
  ) p
  CROSS JOIN LATERAL (
    WITH recent AS MATERIALIZED (
      SELECT ps.scraped_at AS t, ps.price AS v
      FROM public.price_snapshots ps
      WHERE ps.product_id = p.id AND ps.scraped_at >= now() - interval '30 days'
        AND ps.scraped_at <= now()
      ORDER BY ps.scraped_at DESC LIMIT 1000
    ), samples AS (
      SELECT * FROM recent
      UNION ALL
      -- Snapshots record changes: carry the last known price into the window.
      -- If the read cap was reached, omit this anchor rather than imply the
      -- omitted interval was flat. There is no catalogue-wide snapshot scan.
      (SELECT now() - interval '30 days', ps.price
       FROM public.price_snapshots ps
       WHERE ps.product_id = p.id AND ps.scraped_at < now() - interval '30 days'
         AND (SELECT count(*) FROM recent) < 1000
       ORDER BY ps.scraped_at DESC LIMIT 1)
      UNION ALL
      SELECT p.last_seen, p.current_price::numeric
      WHERE p.last_seen BETWEEN now() - interval '30 days' AND now()
    ), daily AS (
      SELECT DISTINCT ON ((t AT TIME ZONE 'UTC')::date) t, v
      FROM samples WHERE v > 0 AND v < 'Infinity'::numeric
      ORDER BY (t AT TIME ZONE 'UTC')::date, t DESC, v DESC
    )
    SELECT jsonb_agg(jsonb_build_array(t, v) ORDER BY t) AS points FROM daily
  ) h;
END;
$$;
REVOKE ALL ON FUNCTION public.feed_price_history(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.feed_price_history(jsonb) TO anon;
NOTIFY pgrst, 'reload schema';
COMMIT;
