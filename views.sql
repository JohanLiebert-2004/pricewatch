-- Website read views (Supabase Postgres).
-- The anon role can SELECT only these views; base tables stay locked (RLS).
-- Re-run this file after recreating the database. Grants are lost when a
-- view is dropped, so every view is followed by its grant.
--
-- IMPORTANT: every `create view`/`create materialized view` below is
-- immediately preceded by a `revoke all ... from anon, authenticated` on
-- that object, before the single `grant select` it actually needs. This
-- isn't defensive paranoia — Supabase grants anon/authenticated full
-- INSERT/UPDATE/DELETE/TRUNCATE on newly created public objects by default,
-- and a plain `grant select` layered on top does NOT remove that. This was
-- exploited during testing: product_search is a simple single-table view
-- (no join/aggregate), which Postgres auto-updates through to `products`;
-- since the view is owned by `postgres` (BYPASSRLS), an anon PATCH request
-- through product_search silently bypassed products' RLS entirely and
-- rewrote a live row. Always revoke-then-grant on every new view here.

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
revoke all on deal_feed from anon, authenticated;
grant select on deal_feed to anon;

-- Every discounted product at any depth: powers the deal page's 0-99% slider
-- and its text search. Materialized (refreshed by every `run.py detect`) so
-- anon ilike/filter queries stay milliseconds under Supabase's timeout.
drop materialized view if exists discount_feed;
create materialized view discount_feed as
with hist as (
  select product_id,
         max(price) as hi,
         min(price) filter (where scraped_at > now() - interval '30 days') as low_30
  from price_snapshots
  where scraped_at > now() - interval '90 days'
  group by product_id
)
select p.retailer, p.sku, p.title, p.brand,
       coalesce(nullif(p.category,''),'other') as category,
       p.subcategory,
       p.url, p.image_url, p.is_marketplace,
       p.current_price as price,
       greatest(coalesce(p.current_rrp,0), coalesce(h.hi,0)) as reference_price,
       round((1 - p.current_price/greatest(coalesce(p.current_rrp,0), coalesce(h.hi,0)))*100) as pct_off,
       ((1 - p.current_price/greatest(coalesce(p.current_rrp,0), coalesce(h.hi,0))) >= 0.8) as error_tier,
       case when coalesce(p.current_rrp,0) >= coalesce(h.hi,0)
            then 'rrp_gap' else 'history_drop' end as signal,
       case when coalesce(p.current_rrp,0) >= coalesce(h.hi,0)
            then 'Retailer RRP' else '90-day price high' end as reference_source,
       (h.low_30 is not null and p.current_price <= h.low_30) as is_30d_low,
       coalesce(p.price_updated_at, p.last_seen::text) as price_updated_at,
       p.first_seen
from products p
left join hist h on h.product_id = p.id
where p.current_price is not null
  and p.current_price > 0     -- $0 = sold-out placeholder, not a 100% discount
  and greatest(coalesce(p.current_rrp,0), coalesce(h.hi,0)) >= 40
  and greatest(coalesce(p.current_rrp,0), coalesce(h.hi,0)) > p.current_price
  -- A retailer's old catalogue record is not a live deal. Each fast feed
  -- runs at least daily; keep only items confirmed in the last 36 hours.
  and p.last_seen > now() - interval '36 hours'
  and round((1 - p.current_price/greatest(coalesce(p.current_rrp,0), coalesce(h.hi,0)))*100) >= 1;
create unique index discount_feed_pk on discount_feed (retailer, sku);
revoke all on discount_feed from anon, authenticated;
grant select on discount_feed to anon;

-- Search page: latest price + date for any tracked product.
drop view if exists product_search;
create view product_search as
select p.retailer, p.sku, p.title, p.brand, p.category, p.subcategory, p.url,
       p.image_url, p.is_marketplace, p.current_price, p.current_rrp,
       coalesce(p.price_updated_at, p.last_seen::text) as price_updated_at
from products p
where p.current_price is not null;
revoke all on product_search from anon, authenticated;
grant select on product_search to anon;

-- Catalogue page chip counts.
-- Precomputed so the homepage header never has to aggregate the full products
-- table before showing its product total.
do $$
declare kind "char";
begin
  select c.relkind into kind
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relname = 'catalogue_stats';
  if kind = 'v' then
    execute 'drop view catalogue_stats';
  elsif kind = 'm' then
    execute 'drop materialized view catalogue_stats';
  end if;
end $$;
create materialized view catalogue_stats as
select retailer, coalesce(nullif(category,''),'other') as category,
       count(*) as items
from products
where current_price is not null
group by 1, 2;
create unique index catalogue_stats_pk on catalogue_stats (retailer, category);
revoke all on catalogue_stats from anon, authenticated;
grant select on catalogue_stats to anon;

-- Retailer status shown on the homepage after a shopper selects a store.
-- Keeping it materialized makes the freshness check cheap for every visit.
do $$
declare kind "char";
begin
  select c.relkind into kind
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relname = 'retailer_freshness';
  if kind = 'v' then
    execute 'drop view retailer_freshness';
  elsif kind = 'm' then
    execute 'drop materialized view retailer_freshness';
  end if;
end $$;
create materialized view retailer_freshness as
select retailer,
       count(*) filter (where current_price is not null) as products,
       count(*) filter (where current_price is not null
                          and last_seen > now() - interval '36 hours') as fresh_products,
       max(last_seen) filter (where current_price is not null) as last_seen
from products
group by retailer;
create unique index retailer_freshness_pk on retailer_freshness (retailer);
revoke all on retailer_freshness from anon, authenticated;
grant select on retailer_freshness to anon;

-- Per-store category chips: each retailer's own (native or title-derived)
-- subcategory labels with item counts, so the site can render a store's chip
-- row from real data and skip empty chips.
drop view if exists subcategory_stats;
create view subcategory_stats as
select retailer, subcategory, count(*) as items
from products
where current_price is not null and subcategory is not null
group by 1, 2;
revoke all on subcategory_stats from anon, authenticated;
grant select on subcategory_stats to anon;

-- Growth page: per-day new products and price changes (UTC days).
-- Precompute this scan: price_snapshots is large enough that calculating it per
-- browser visit can exceed Supabase's statement timeout.
do $$
declare kind "char";
begin
  select c.relkind into kind
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relname = 'growth_daily';
  if kind = 'v' then
    execute 'drop view growth_daily';
  elsif kind = 'm' then
    execute 'drop materialized view growth_daily';
  end if;
end $$;
create materialized view growth_daily as
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
create unique index growth_daily_pk on growth_daily (day, retailer);
revoke all on growth_daily from anon, authenticated;
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
revoke all on product_history from anon, authenticated;
grant select on product_history to anon;

-- Watches: anon can never touch the table directly (no SELECT/INSERT/UPDATE
-- grants at all) — both creating and cancelling a watch go through
-- SECURITY DEFINER RPCs owned by the table owner (BYPASSRLS, RLS isn't
-- FORCEd), same pattern for both directions.
--
-- create_watch() replaced a plain `insert ... with check (true)` policy on
-- 2026-07-09: that policy let anon set *every* column via a raw PostgREST
-- POST, including email — meaning anyone could insert watches for arbitrary
-- third-party addresses with no ownership check. Harmless while
-- RESEND_API_KEY is unset (see project_pricewatch_deferred), but the moment
-- email sending is switched on that's an open spam relay against a verified
-- domain. create_watch() now validates the email/price/product server-side,
-- generates the unguessable token itself (never client-supplied), and caps
-- both total watches per email and duplicate active watches on the same
-- product — narrows the door PostgREST exposes down to "one legitimate
-- watch, one email format, one reasonable price" instead of "any row".
alter table watches enable row level security;
revoke all on watches from anon, authenticated;
drop policy if exists watches_insert_anon on watches;

create or replace function create_watch(p_product_id bigint, p_email text, p_target_price numeric)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_token text;
  v_email text := lower(trim(p_email));
begin
  if v_email !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' then
    raise exception 'invalid email';
  end if;
  if p_target_price is null or p_target_price <= 0 or p_target_price > 100000 then
    raise exception 'invalid target price';
  end if;
  if not exists (select 1 from products where id = p_product_id) then
    raise exception 'invalid product';
  end if;
  if (select count(*) from watches where email = v_email) >= 25 then
    raise exception 'too many watches for this email';
  end if;
  if exists (select 1 from watches where product_id = p_product_id and email = v_email
             and cancelled_at is null and fired_at is null) then
    raise exception 'already watching this product';
  end if;

  v_token := gen_random_uuid()::text;
  insert into watches (product_id, email, target_price, token)
  values (p_product_id, v_email, p_target_price, v_token);
  return v_token;
end;
$$;
revoke all on function create_watch(bigint, text, numeric) from public;
grant execute on function create_watch(bigint, text, numeric) to anon;

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

-- Double opt-in for email watches. New watches do not receive price alerts
-- until their recipient opens the unguessable confirmation link. The runtime
-- migration in db.py marks legacy watches confirmed once, preserving them.
alter table watches add column if not exists confirmed_at timestamptz;
alter table watches add column if not exists confirmation_sent_at timestamptz;
create index if not exists idx_watches_confirmed_unfired on watches(product_id)
  where confirmed_at is not null and fired_at is null and cancelled_at is null;

create or replace function create_watch(p_product_id bigint, p_email text, p_target_price numeric)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_token text;
  v_email text := lower(trim(p_email));
begin
  if v_email !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' then
    raise exception 'invalid email';
  end if;
  if p_target_price is null or p_target_price <= 0 or p_target_price > 100000 then
    raise exception 'invalid target price';
  end if;
  if not exists (select 1 from products where id = p_product_id) then
    raise exception 'invalid product';
  end if;
  if (select count(*) from watches where email = v_email) >= 25 then
    raise exception 'too many watches for this email';
  end if;
  if exists (select 1 from watches where product_id = p_product_id and email = v_email
             and cancelled_at is null and fired_at is null) then
    raise exception 'already watching this product';
  end if;
  v_token := gen_random_uuid()::text;
  insert into watches (product_id, email, target_price, token)
  values (p_product_id, v_email, p_target_price, v_token);
  return v_token;
end;
$$;
revoke all on function create_watch(bigint, text, numeric) from public;
grant execute on function create_watch(bigint, text, numeric) to anon;

create or replace function confirm_watch(p_token text)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare affected int;
begin
  update watches set confirmed_at = now()
  where token = p_token and confirmed_at is null and cancelled_at is null and fired_at is null;
  get diagnostics affected = row_count;
  return affected > 0;
end;
$$;
revoke all on function confirm_watch(text) from public;
grant execute on function confirm_watch(text) to anon;
-- Trending searches: anonymous term counting, no visitor identity of any
-- kind (term + timestamp only). search.html fires log_search() on every
-- query (fire-and-forget) and shows trending_searches as suggestion chips.
-- Same anon-write pattern as create_watch: no table grants, a narrow
-- SECURITY DEFINER RPC is the only door, server-side sanitation inside it.
-- detect prunes rows older than 30 days; the table itself is in schema.sql.

create or replace function log_search(p_term text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  t text;
begin
  t := lower(btrim(coalesce(p_term, '')));
  -- printable, short, non-empty; drop anything else silently
  if t = '' or length(t) > 60 or t !~ '^[[:print:]]+$' then
    return;
  end if;
  insert into search_terms (term) values (t);
end;
$$;
revoke all on function log_search(text) from public;
grant execute on function log_search(text) to anon;

drop view if exists trending_searches;
create view trending_searches as
select term, count(*) as searches
from search_terms
where searched_at > now() - interval '7 days'
group by term
having count(*) >= 2          -- one person's typo isn't a trend
order by searches desc
limit 12;
revoke all on trending_searches from anon, authenticated;
grant select on trending_searches to anon;