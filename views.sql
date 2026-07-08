-- Website read views (Supabase Postgres).
-- The anon role can SELECT only these views; base tables stay locked (RLS).
-- Re-run this file after recreating the database. Grants are lost when a
-- view is dropped, so every view is followed by its grant.

-- Anomaly-engine deals (50%+ drops recorded by `run.py detect`).
drop view if exists deal_feed;
create view deal_feed as
select d.price, d.reference_price, d.signal, d.score, d.status, d.detected_at,
       p.title, p.retailer, p.sku, p.url, p.is_marketplace,
       round(d.score*100) as pct_off,
       (d.score >= 0.80) as error_tier
from deals d join products p on p.id = d.product_id
where d.status <> 'expired' and coalesce(d.reference_price, 0) >= 40
order by d.score desc;
grant select on deal_feed to anon;

-- Every discounted product at any depth: powers the deal page's 0-99% slider
-- and its text search. Materialized (refreshed by every `run.py detect`) so
-- anon ilike/filter queries stay milliseconds under Supabase's timeout.
drop materialized view if exists discount_feed;
create materialized view discount_feed as
with hist as (
  select product_id, max(price) as hi
  from price_snapshots
  where scraped_at > now() - interval '90 days'
  group by product_id
)
select p.retailer, p.sku, p.title, p.brand,
       coalesce(nullif(p.category,''),'other') as category,
       p.url, p.image_url, p.is_marketplace,
       p.current_price as price,
       greatest(coalesce(p.current_rrp,0), coalesce(h.hi,0)) as reference_price,
       round((1 - p.current_price/greatest(coalesce(p.current_rrp,0), coalesce(h.hi,0)))*100) as pct_off,
       ((1 - p.current_price/greatest(coalesce(p.current_rrp,0), coalesce(h.hi,0))) >= 0.8) as error_tier,
       case when coalesce(p.current_rrp,0) >= coalesce(h.hi,0)
            then 'rrp_gap' else 'history_drop' end as signal,
       coalesce(p.price_updated_at, p.last_seen::text) as price_updated_at
from products p
left join hist h on h.product_id = p.id
where p.current_price is not null
  and greatest(coalesce(p.current_rrp,0), coalesce(h.hi,0)) >= 40
  and greatest(coalesce(p.current_rrp,0), coalesce(h.hi,0)) > p.current_price
  and p.last_seen > now() - interval '10 days'
  and round((1 - p.current_price/greatest(coalesce(p.current_rrp,0), coalesce(h.hi,0)))*100) >= 1;
create unique index discount_feed_pk on discount_feed (retailer, sku);
grant select on discount_feed to anon;

-- Search page: latest price + date for any tracked product.
drop view if exists product_search;
create view product_search as
select p.retailer, p.sku, p.title, p.brand, p.category, p.url,
       p.image_url, p.is_marketplace, p.current_price, p.current_rrp,
       coalesce(p.price_updated_at, p.last_seen::text) as price_updated_at
from products p
where p.current_price is not null;
grant select on product_search to anon;

-- Catalogue page chip counts.
drop view if exists catalogue_stats;
create view catalogue_stats as
select retailer, coalesce(nullif(category,''),'other') as category,
       count(*) as items
from products
where current_price is not null
group by 1, 2;
grant select on catalogue_stats to anon;

-- Growth page: per-day new products and price changes (UTC days).
drop view if exists growth_daily;
create view growth_daily as
with np as (
  select (first_seen at time zone 'utc')::date::text as day,
         retailer, count(*) as new_products
  from products group by 1, 2
),
sn as (
  select (ps.scraped_at at time zone 'utc')::date::text as day,
         p.retailer, count(*) as price_checks
  from price_snapshots ps join products p on p.id = ps.product_id
  group by 1, 2
)
select coalesce(np.day, sn.day) as day,
       coalesce(np.retailer, sn.retailer) as retailer,
       coalesce(np.new_products, 0) as new_products,
       coalesce(sn.price_checks, 0) as price_checks
from np full outer join sn on np.day = sn.day and np.retailer = sn.retailer;
grant select on growth_daily to anon;

-- Product history page: full snapshot series for one product (filter by
-- retailer+sku via PostgREST). Not materialized — single-product lookups are
-- cheap and should always be fresh, unlike discount_feed's aggregate scan.
drop view if exists product_history;
create view product_history as
select p.id as product_id, p.retailer, p.sku, p.title, p.brand, p.category,
       p.url, p.image_url, p.is_marketplace, p.current_price, p.current_rrp,
       coalesce(p.price_updated_at, p.last_seen::text) as price_updated_at,
       ps.price, ps.rrp as snapshot_rrp, ps.in_stock, ps.scraped_at
from price_snapshots ps
join products p on p.id = ps.product_id;
grant select on product_history to anon;

-- Watches: anon can create a watch (product.html's signup form) via a plain
-- INSERT policy, but can never SELECT the table back — emails stay private.
-- Cancelling (the unsubscribe link) can't be a plain anon UPDATE policy: for
-- UPDATE, Postgres also requires the target row to be visible under a SELECT
-- policy (there isn't one here), so `USING (true)` alone silently matches
-- zero rows. Instead, cancel_watch() is a SECURITY DEFINER RPC — owned by
-- the table owner (which has BYPASSRLS and RLS isn't FORCEd), so it can
-- update by token without any anon-facing SELECT/UPDATE grant at all. The
-- unguessable per-row token, not RLS, is what gates which row a visitor can
-- reach — same trust level as the rest of the public site's anon key.
alter table watches enable row level security;

grant insert on watches to anon;
drop policy if exists watches_insert_anon on watches;
create policy watches_insert_anon on watches for insert to anon with check (true);

create or replace function cancel_watch(p_token text)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  affected int;
begin
  update watches set cancelled_at = now()
  where token = p_token and cancelled_at is null and fired_at is null;
  get diagnostics affected = row_count;
  return affected > 0;
end;
$$;
revoke all on function cancel_watch(text) from public;
grant execute on function cancel_watch(text) to anon;
