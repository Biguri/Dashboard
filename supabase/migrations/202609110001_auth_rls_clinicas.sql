create extension if not exists pgcrypto with schema extensions;

create table public.clinicas (
    id uuid primary key default gen_random_uuid(),
    nome text not null unique check (length(btrim(nome)) between 1 and 120),
    criada_em timestamptz not null default now()
);

create table public.perfis (
    user_id uuid primary key references auth.users(id) on delete cascade,
    clinica_id uuid not null unique references public.clinicas(id),
    nome_usuario text not null unique check (
        nome_usuario ~ '^[a-z0-9_.-]{1,64}$'
        and nome_usuario = lower(nome_usuario)
    ),
    criado_em timestamptz not null default now()
);

create table public.dashboard_arquivos (
    clinica_id uuid not null references public.clinicas(id),
    id text not null check (id ~ '^[0-9a-f]{64}$'),
    nome text not null check (length(nome) between 1 and 255),
    importado_por uuid not null references auth.users(id),
    importado_em timestamptz not null default now(),
    linhas integer not null check (linhas > 0),
    conteudo bytea not null check (octet_length(conteudo) between 1 and 20971520),
    dados jsonb not null check (jsonb_typeof(dados) = 'array'),
    primary key (clinica_id, id)
);

create index dashboard_arquivos_clinica_data_idx
    on public.dashboard_arquivos (clinica_id, importado_em, id);

alter table public.clinicas enable row level security;
alter table public.perfis enable row level security;
alter table public.dashboard_arquivos enable row level security;
alter table public.clinicas force row level security;
alter table public.perfis force row level security;
alter table public.dashboard_arquivos force row level security;

revoke all on table public.clinicas from anon, authenticated;
revoke all on table public.perfis from anon, authenticated;
revoke all on table public.dashboard_arquivos from anon, authenticated;
grant select on table public.clinicas to authenticated;
grant select on table public.perfis to authenticated;
grant select, insert on table public.dashboard_arquivos to authenticated;

create policy "conta le o proprio perfil"
on public.perfis
for select
to authenticated
using (user_id = (select auth.uid()));

create policy "conta le a propria clinica"
on public.clinicas
for select
to authenticated
using (
    exists (
        select 1
        from public.perfis as p
        where p.user_id = (select auth.uid())
          and p.clinica_id = clinicas.id
    )
);

create policy "conta le arquivos da propria clinica"
on public.dashboard_arquivos
for select
to authenticated
using (
    exists (
        select 1
        from public.perfis as p
        where p.user_id = (select auth.uid())
          and p.clinica_id = dashboard_arquivos.clinica_id
    )
);

create policy "conta importa arquivos na propria clinica"
on public.dashboard_arquivos
for insert
to authenticated
with check (
    importado_por = (select auth.uid())
    and exists (
        select 1
        from public.perfis as p
        where p.user_id = (select auth.uid())
          and p.clinica_id = dashboard_arquivos.clinica_id
    )
);

create or replace function public.importar_dashboard_arquivos(p_arquivos jsonb)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_usuario uuid := auth.uid();
    v_clinica uuid;
    v_item jsonb;
    v_conteudo bytea;
    v_id text;
    v_nome text;
    v_linhas integer;
    v_dados jsonb;
    v_inserido integer;
    v_novos integer := 0;
    v_repetidos integer := 0;
begin
    if jsonb_typeof(p_arquivos) <> 'array' then
        raise exception 'Lote de arquivos invalido.' using errcode = '22023';
    end if;
    if jsonb_array_length(p_arquivos) < 1 or jsonb_array_length(p_arquivos) > 20 then
        raise exception 'O lote deve conter entre 1 e 20 arquivos.' using errcode = '22023';
    end if;
    if v_usuario is null then
        raise exception 'Autenticacao obrigatoria.' using errcode = '42501';
    end if;

    select p.clinica_id
      into v_clinica
      from public.perfis as p
     where p.user_id = v_usuario;
    if v_clinica is null then
        raise exception 'Perfil sem clinica associada.' using errcode = '42501';
    end if;

    for v_item in select value from jsonb_array_elements(p_arquivos)
    loop
        if jsonb_typeof(v_item) <> 'object' then
            raise exception 'Item de arquivo invalido.' using errcode = '22023';
        end if;

        v_id := v_item ->> 'id';
        v_nome := v_item ->> 'nome';
        v_linhas := (v_item ->> 'linhas')::integer;
        v_dados := v_item -> 'dados';
        v_conteudo := decode(v_item ->> 'conteudo_base64', 'base64');

        if v_id is null or v_id !~ '^[0-9a-f]{64}$'
           or v_nome is null or length(v_nome) not between 1 and 255
           or v_linhas is null or v_linhas < 1
           or jsonb_typeof(v_dados) <> 'array'
           or octet_length(v_conteudo) not between 1 and 20971520
           or encode(extensions.digest(v_conteudo, 'sha256'), 'hex') <> v_id then
            raise exception 'Conteudo de arquivo invalido.' using errcode = '22023';
        end if;

        insert into public.dashboard_arquivos (
            clinica_id, id, nome, importado_por, linhas, conteudo, dados
        ) values (
            v_clinica, v_id, v_nome, v_usuario, v_linhas, v_conteudo, v_dados
        )
        on conflict (clinica_id, id) do nothing;

        get diagnostics v_inserido = row_count;
        if v_inserido = 1 then
            v_novos := v_novos + 1;
        else
            v_repetidos := v_repetidos + 1;
        end if;
    end loop;

    return jsonb_build_object('novos', v_novos, 'repetidos', v_repetidos);
end;
$$;

revoke all on function public.importar_dashboard_arquivos(jsonb) from public;
revoke all on function public.importar_dashboard_arquivos(jsonb) from anon;
grant execute on function public.importar_dashboard_arquivos(jsonb) to authenticated;
