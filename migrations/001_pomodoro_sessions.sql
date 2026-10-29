-- Independent Pomodoro schema. Run only in the dedicated pomodoro-matias project.
create extension if not exists pgcrypto;

create table if not exists public.pomodoro_sessions (
    id_estudio text primary key check (length(btrim(id_estudio)) between 1 and 80),
    user_id uuid not null references auth.users(id) on delete cascade,
    fecha date not null,
    materia text not null check (length(btrim(materia)) between 1 and 120),
    cantidad integer not null check (cantidad > 0 and cantidad <= 100),
    tiempo integer not null check (tiempo > 0 and tiempo <= 600),
    total_minutos integer generated always as (cantidad * tiempo) stored,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint pomodoro_sessions_total_limit check (cantidad * tiempo <= 60000)
);

create index if not exists pomodoro_sessions_user_fecha_idx
    on public.pomodoro_sessions (user_id, fecha desc);
create index if not exists pomodoro_sessions_user_materia_idx
    on public.pomodoro_sessions (user_id, materia);

alter table public.pomodoro_sessions enable row level security;
alter table public.pomodoro_sessions force row level security;

drop policy if exists "Users read their Pomodoro sessions" on public.pomodoro_sessions;
create policy "Users read their Pomodoro sessions"
    on public.pomodoro_sessions for select to authenticated
    using (auth.uid() = user_id);
drop policy if exists "Users create their Pomodoro sessions" on public.pomodoro_sessions;
create policy "Users create their Pomodoro sessions"
    on public.pomodoro_sessions for insert to authenticated
    with check (auth.uid() = user_id);
drop policy if exists "Users update their Pomodoro sessions" on public.pomodoro_sessions;
create policy "Users update their Pomodoro sessions"
    on public.pomodoro_sessions for update to authenticated
    using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists "Users delete their Pomodoro sessions" on public.pomodoro_sessions;
create policy "Users delete their Pomodoro sessions"
    on public.pomodoro_sessions for delete to authenticated
    using (auth.uid() = user_id);

revoke all on public.pomodoro_sessions from anon;
grant select, insert, update, delete on public.pomodoro_sessions to authenticated;

comment on table public.pomodoro_sessions is
    'Personal Matias Henquin Pomodoro study sessions; isolated from all other Supabase projects.';
