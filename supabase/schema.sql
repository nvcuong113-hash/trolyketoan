-- Trợ lý Kế toán SME: account, subscription and payment metadata only.
-- Never create tables for bank statements, invoices, journals or customer accounting records.

create extension if not exists pgcrypto;

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  full_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  plan text not null check (plan in ('internal','service')),
  status text not null default 'active' check (status in ('active','expired','cancelled')),
  started_at timestamptz not null default now(),
  expires_at timestamptz not null,
  tax_codes text[] not null default '{}',
  created_at timestamptz not null default now()
);
create index if not exists subscriptions_user_expiry_idx on public.subscriptions(user_id, expires_at desc);

create table if not exists public.payments (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  payment_code text not null unique,
  plan text not null check (plan in ('internal','service')),
  amount bigint not null check (amount > 0),
  status text not null default 'pending' check (status in ('pending','paid','expired','rejected')),
  expires_at timestamptz not null default (now() + interval '30 minutes'),
  transaction_id bigint unique,
  paid_at timestamptz,
  provider_payload jsonb,
  created_at timestamptz not null default now()
);
create index if not exists payments_user_created_idx on public.payments(user_id, created_at desc);

alter table public.profiles enable row level security;
alter table public.subscriptions enable row level security;
alter table public.payments enable row level security;

drop policy if exists "profile owner read" on public.profiles;
create policy "profile owner read" on public.profiles for select to authenticated using (id = auth.uid());
drop policy if exists "profile owner update" on public.profiles;
create policy "profile owner update" on public.profiles for update to authenticated using (id = auth.uid()) with check (id = auth.uid());
drop policy if exists "subscription owner read" on public.subscriptions;
create policy "subscription owner read" on public.subscriptions for select to authenticated using (user_id = auth.uid());
drop policy if exists "payment owner read" on public.payments;
create policy "payment owner read" on public.payments for select to authenticated using (user_id = auth.uid());

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public
as $$
begin
  insert into public.profiles(id,email,full_name)
  values(new.id,new.email,coalesce(new.raw_user_meta_data->>'full_name',''))
  on conflict(id) do update set email=excluded.email;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
for each row execute procedure public.handle_new_user();

create or replace function public.create_payment_order(p_plan text)
returns table(payment_code text, amount bigint, expires_at timestamptz)
language plpgsql security definer set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_code text;
  v_amount bigint;
  v_expiry timestamptz := now() + interval '30 minutes';
begin
  if v_user is null then raise exception 'Authentication required'; end if;
  update public.payments set status='expired'
    where user_id=v_user and status='pending' and expires_at<=now();
  if p_plan = 'internal' then v_amount := 69000;
  elsif p_plan = 'service' then v_amount := 249000;
  else raise exception 'Invalid plan'; end if;
  v_code := 'TKPRO' || upper(substr(replace(gen_random_uuid()::text,'-',''),1,12));
  insert into public.payments(user_id,payment_code,plan,amount,expires_at)
  values(v_user,v_code,p_plan,v_amount,v_expiry);
  return query select v_code,v_amount,v_expiry;
end;
$$;

create or replace function public.apply_sepay_payment(
  p_transaction_id bigint,
  p_content text,
  p_transfer_amount bigint,
  p_payload jsonb
)
returns jsonb language plpgsql security definer set search_path = public
as $$
declare
  v_payment public.payments%rowtype;
  v_code text;
  v_expiry timestamptz;
begin
  if p_transaction_id is null or p_transfer_amount <= 0 then
    raise exception 'Invalid transaction';
  end if;
  -- SePay may retry the same webhook. The unique transaction_id makes processing idempotent.
  if exists(select 1 from public.payments where transaction_id=p_transaction_id) then
    return jsonb_build_object('status','duplicate','transaction_id',p_transaction_id);
  end if;
  v_code := (regexp_match(upper(coalesce(p_content,'')), 'TKPRO[A-Z0-9]{8,20}'))[1];
  if v_code is null then return jsonb_build_object('status','ignored','reason','payment_code_not_found'); end if;
  select * into v_payment from public.payments
    where payment_code=v_code and status='pending' and expires_at > now()
    for update;
  if not found then return jsonb_build_object('status','ignored','reason','order_not_found_or_expired'); end if;
  if v_payment.amount <> p_transfer_amount then
    update public.payments set status='rejected', provider_payload=p_payload where id=v_payment.id;
    return jsonb_build_object('status','rejected','reason','amount_mismatch');
  end if;
  update public.payments set status='paid', transaction_id=p_transaction_id,
    paid_at=now(), provider_payload=p_payload where id=v_payment.id;
  select greatest(now(),coalesce(max(expires_at),now())) + interval '30 days'
    into v_expiry from public.subscriptions
    where user_id=v_payment.user_id and status='active' and expires_at>now();
  update public.subscriptions set status='expired'
    where user_id=v_payment.user_id and status='active' and expires_at<=now();
  insert into public.subscriptions(user_id,plan,status,started_at,expires_at)
  values(v_payment.user_id,v_payment.plan,'active',now(),v_expiry);
  return jsonb_build_object('status','paid','user_id',v_payment.user_id,'plan',v_payment.plan,'expires_at',v_expiry);
end;
$$;

revoke all on function public.apply_sepay_payment(bigint,text,bigint,jsonb) from public, anon, authenticated;
grant execute on function public.apply_sepay_payment(bigint,text,bigint,jsonb) to service_role;
revoke all on function public.create_payment_order(text) from public, anon;
grant execute on function public.create_payment_order(text) to authenticated;

create or replace function public.set_subscription_tax_codes(p_tax_codes text[])
returns jsonb language plpgsql security definer set search_path = public
as $
declare
  v_user uuid := auth.uid();
  v_subscription public.subscriptions%rowtype;
  v_codes text[];
begin
  if v_user is null then raise exception 'Authentication required'; end if;
  select * into v_subscription from public.subscriptions
    where user_id=v_user and status='active' and expires_at>now()
    order by expires_at desc limit 1 for update;
  if not found then raise exception 'No active subscription'; end if;
  select array_agg(distinct regexp_replace(trim(x), '\\s+', '', 'g'))
    into v_codes from unnest(coalesce(p_tax_codes, '{}'::text[])) x
    where trim(x) <> '';
  v_codes := coalesce(v_codes, '{}'::text[]);
  if v_subscription.plan='internal' and cardinality(v_codes)>1 then
    raise exception 'Internal plan supports one tax code';
  end if;
  if cardinality(v_codes)>100 then raise exception 'Too many tax codes'; end if;
  update public.subscriptions set tax_codes=v_codes where id=v_subscription.id;
  return jsonb_build_object('updated',true,'tax_code_count',cardinality(v_codes));
end;
$;

revoke all on function public.set_subscription_tax_codes(text[]) from public, anon;
grant execute on function public.set_subscription_tax_codes(text[]) to authenticated;


-- Durable queue for provider webhook events. It contains payment metadata only.
create table if not exists public.webhook_events (
  id uuid primary key default gen_random_uuid(),
  provider text not null default 'sepay',
  transaction_id bigint not null unique,
  payload jsonb not null,
  status text not null default 'pending' check (status in ('pending','processing','processed','failed')),
  attempts integer not null default 0,
  last_error text,
  processing_result jsonb,
  created_at timestamptz not null default now(),
  processed_at timestamptz
);
create index if not exists webhook_events_status_created_idx on public.webhook_events(status,created_at);
alter table public.webhook_events enable row level security;
revoke all on public.webhook_events from public, anon, authenticated;
grant all on public.webhook_events to service_role;


-- Public legal texts are reference corpus, not customer accounting data.
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values(
  'legal-corpus','legal-corpus',false,20971520,
  array['application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document','text/plain','text/markdown']
)
on conflict(id) do update set public=false,file_size_limit=20971520,
  allowed_mime_types=excluded.allowed_mime_types;

create table if not exists public.legal_documents (
  id uuid primary key default gen_random_uuid(),
  number text not null,
  title text not null,
  article text not null default '',
  effective_from date not null,
  effective_to date,
  status text not null check(status in ('Còn hiệu lực','Hết hiệu lực','Chưa xác minh')),
  source_url text not null,
  storage_path text not null unique,
  filename text not null,
  content_hash text not null unique,
  uploaded_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists legal_documents_effective_idx on public.legal_documents(status,effective_from,effective_to);
alter table public.legal_documents enable row level security;
revoke all on public.legal_documents from public,anon,authenticated;
grant all on public.legal_documents to service_role;
