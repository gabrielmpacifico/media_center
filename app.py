from __future__ import annotations

from datetime import date
from pathlib import Path
import re
import zipfile

import plotly.express as px
import streamlit as st

from services.tvmaze_client import TVMazeLookupError, extract_imdb_id, fetch_title
from services.progress import duration_minutes, remaining_count, validate_position
from services.storage import BACKUP_DIR, ROOT_DIR, list_records, save_record

st.set_page_config(page_title="Media Center", page_icon="🎬", layout="wide")
st.markdown("""
<style>
    [data-testid="stSidebar"] { background: linear-gradient(180deg, #101827 0%, #17243a 100%); border-right: 1px solid #25334a; }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color: #d5deec; }
    [data-testid="stSidebar"] [role="radiogroup"] label { padding: .6rem .7rem; border: 1px solid transparent; border-radius: 12px; transition: all .15s ease; }
    [data-testid="stSidebar"] [role="radiogroup"] label:hover { background: rgba(255,255,255,.08); border-color: rgba(255,255,255,.12); }
    [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) { background: rgba(83, 126, 214, .25); border-color: rgba(135, 169, 240, .35); }
    [data-testid="stMetric"] { background: rgba(120, 145, 180, .08); padding: .8rem 1rem; border-radius: 14px; border: 1px solid rgba(120, 145, 180, .16); }
    div[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 16px; }
</style>
""", unsafe_allow_html=True)

STATUSES = ["Assistindo", "Finalizada", "Aguardando", "Parada", "Pendente", "Pausada", "Concluída", "Desistida"]
CATEGORIES = ["Série", "Anime", "Anime / Animação", "Dorama", "Desenho", "Animação", "SitCom", "Reality", "Filme"]


def make_backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"media-center-{date.today().isoformat()}.zip"
    counter = 2
    while target.exists():
        target = BACKUP_DIR / f"media-center-{date.today().isoformat()}-{counter}.zip"
        counter += 1
    data_dir = ROOT_DIR / "data"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if data_dir.exists():
            for path in data_dir.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(ROOT_DIR))
    return target


def record_is_movie(record: dict) -> bool:
    return "Filme" in [part.strip() for part in str(record.get("genero") or "").split("/")]


def record_category(record: dict) -> str:
    return str(record.get("genero") or "Série").split("/")[0].strip()


def add_form() -> None:
    st.subheader("Cadastrar pelo TVmaze usando o link IMDb")
    st.caption("Séries, animes e doramas são buscados no TVmaze. Filmes são cadastrados manualmente.")
    kind = st.selectbox("Categoria", CATEGORIES, key="add_media_kind")
    manual_mode = st.checkbox("Cadastrar sem consultar o TVmaze (dados locais)", key="manual_catalog_mode")
    with st.form("new_title_form", clear_on_submit=True):
        link = st.text_input("Link do IMDb", placeholder="https://www.imdb.com/title/tt11823088/")
        c1, c2 = st.columns(2)
        status = c1.selectbox("Status inicial", STATUSES, index=2)
        times_done = c2.number_input("Vezes completada", min_value=0, value=0, step=1)
        manual_title = st.text_input("Título para cadastro manual") if kind == "Filme" or manual_mode else ""
        manual_year = st.number_input("Ano de lançamento", min_value=0, value=0, step=1) if kind == "Filme" or manual_mode else 0
        c4, c5 = st.columns(2)
        season = c4.number_input("Temporada atual", min_value=0, value=0, step=1)
        episode = c5.number_input("Episódio atual", min_value=0, value=0 if kind == "Filme" else 1, step=1)
        is_manual = kind == "Filme" or manual_mode
        submit_label = "Cadastrar manualmente" if is_manual else "Buscar no TVmaze e cadastrar"
        submitted = st.form_submit_button(submit_label, type="primary")
    if submitted:
        if not link.strip():
            st.error("Cole o link do IMDb para vincular o título.")
            return
        try:
            imdb_id = extract_imdb_id(link)
            existing = next((item for item in list_records() if extract_imdb_id(item.get("link_imdb", "")) == imdb_id), None)
            if existing:
                st.session_state["selected_id"] = existing["_id"]
                st.session_state["flash"] = ("warning", f"{existing['titulo']} já está na sua lista. Abri os detalhes existentes.")
                st.rerun()
            if is_manual:
                if not manual_title.strip():
                    st.error("Informe o título para o cadastro manual.")
                    return
                record = {
                    "titulo": manual_title.strip(), "link_imdb": f"https://www.imdb.com/title/{imdb_id}/",
                    "foto_capa": "", "nota_imdb": 0, "genero": "Filme" if kind == "Filme" else kind,
                    "ano_lancamento": int(manual_year),
                    "duracao_media_ep": "", "total_temporadas": 0, "total_episodios": 0,
                    "imdb_atualizavel": False,
                }
            else:
                with st.spinner("Consultando TVmaze sob demanda…"):
                    record = fetch_title(link, kind)
                position_errors = validate_position(int(season), int(episode), record["total_temporadas"],
                                                    record["total_episodios"], episodes_by_season=record["_episodes_by_season"])
                if position_errors:
                    for error in position_errors:
                        st.error(error)
                    return
            episodes_by_season = record.get("_episodes_by_season") or {}
            final_season = max(episodes_by_season, default=0)
            final_episode = max(episodes_by_season.get(final_season, []), default=0)
            watched_final_episode = (bool(record.get("_show_ended")) and int(season) == final_season
                                     and int(episode) == final_episode and final_episode > 0)
            record.update({
                "status": "Finalizada" if watched_final_episode else status,
                "vezes_completada": int(times_done),
                "temporada_atual": int(season),
                "episodio_atual": int(episode),
                "episodios_nao_assistidos": remaining_count(int(season), int(episode), record["total_temporadas"], record["total_episodios"]),
                "imdb_atualizavel": not is_manual and not watched_final_episode,
                "data_atualizacao": date.today().isoformat(),
            })
            if watched_final_episode and status not in ("Finalizada", "Concluída"):
                record["vezes_completada"] += 1
            save_record(record)
            st.session_state["flash"] = ("success", f"{record['titulo']} foi cadastrado com sucesso.")
            st.rerun()
        except TVMazeLookupError as exc:
            st.error(str(exc))


@st.dialog("Detalhes do título", width="large")
def show_details(selected: dict) -> None:
    selected_id = selected["_id"]
    flash = st.session_state.get("flash")
    if flash:
        level, message = flash
        if level == "success":
            st.success(message)
        elif level == "warning":
            st.warning(message)
        else:
            st.error(message)
        st.session_state.pop("flash", None)

    cover_col, info_col = st.columns([1, 2])
    with cover_col:
        if selected.get("foto_capa"):
            st.image(selected["foto_capa"], width=145)
        else:
            st.markdown("<div style='height:165px;display:grid;place-items:center;background:#17243a;border-radius:14px;font-size:3rem'>🎬</div>", unsafe_allow_html=True)
    with info_col:
        st.markdown(f"### {selected.get('titulo', 'Sem título')}")
        st.caption(f"⭐ {selected.get('nota_imdb', 0)} · {selected.get('genero', '')} · {selected.get('ano_lancamento', '')}")
        st.markdown(f"[Abrir página do IMDb ↗]({selected.get('link_imdb')})")
        st.caption(f"{selected.get('total_temporadas', 0)} temporadas · {selected.get('total_episodios', 0)} episódios · {selected.get('episodios_nao_assistidos', 0)} pendentes")

    st.divider()
    with st.form(f"edit_{selected_id}"):
        e1, e2, e3 = st.columns(3)
        current_status = selected.get("status")
        options = STATUSES.copy()
        if current_status and current_status not in options:
            options.insert(0, current_status)
        new_status = e1.selectbox("Status", options, index=options.index(current_status) if current_status in options else 0,
                                  key=f"detail_status_{selected_id}")
        new_season = e2.number_input("Temporada atual", min_value=0, value=int(selected.get("temporada_atual") or 0), step=1,
                                     key=f"detail_season_{selected_id}")
        new_episode = e3.number_input("Episódio atual", min_value=0, value=int(selected.get("episodio_atual") or 0), step=1,
                                      key=f"detail_episode_{selected_id}")
        new_times = st.number_input("Vezes completada", min_value=0, value=int(selected.get("vezes_completada") or 0), step=1,
                                    key=f"detail_times_{selected_id}")
        new_notes = st.text_area("Minhas anotações", value=selected.get("_notes", ""), height=72,
                                 key=f"detail_notes_{selected_id}")
        save_col, refresh_col = st.columns(2)
        saved = save_col.form_submit_button("Salvar alterações", type="primary", use_container_width=True)
        refresh = refresh_col.form_submit_button("↻ Salvar e atualizar dados do TVmaze", type="secondary",
                                                  use_container_width=True, disabled=not selected.get("imdb_atualizavel"))

    if saved or refresh:
        position_errors = validate_position(
            int(new_season), int(new_episode), int(selected.get("total_temporadas") or 0),
            int(selected.get("total_episodios") or 0), is_movie=record_is_movie(selected),
        )
        if position_errors:
            for error in position_errors:
                st.warning(error)
            if refresh:
                st.caption("A consulta do TVmaze não foi feita; corrija a posição e tente novamente.")
        else:
            # Persist manual edits before the optional request so an API error never loses the form.
            previous_position = (int(selected.get("temporada_atual") or 0), int(selected.get("episodio_atual") or 0))
            previous_pending = int(selected.get("episodios_nao_assistidos") or 0)
            previous_status = str(selected.get("status") or "")
            selected.update({"status": new_status, "temporada_atual": int(new_season), "episodio_atual": int(new_episode),
                             "vezes_completada": int(new_times),
                             "episodios_nao_assistidos": remaining_count(int(new_season), int(new_episode), int(selected.get("total_temporadas") or 0), int(selected.get("total_episodios") or 0)),
                             "data_atualizacao": date.today().isoformat()})
            save_record(selected, new_notes, selected_id)
            if not refresh:
                st.session_state["flash"] = ("success", "Alterações salvas com sucesso.")
                st.rerun()
            try:
                with st.spinner("Consultando TVmaze agora…"):
                    fresh = fetch_title(selected["link_imdb"], record_category(selected))
                old_status = previous_status
                old_pending = previous_pending
                # Keep known local values when the official summary omits these fields.
                for key in ("titulo", "link_imdb", "foto_capa", "nota_imdb", "genero", "ano_lancamento", "duracao_media_ep"):
                    if fresh.get(key) not in (None, "") or key in ("nota_imdb", "foto_capa"):
                        selected[key] = fresh[key]
                if fresh.get("total_temporadas"):
                    selected["total_temporadas"] = fresh["total_temporadas"]
                if fresh.get("total_episodios"):
                    selected["total_episodios"] = fresh["total_episodios"]
                total_seasons = int(selected.get("total_temporadas") or 0)
                total_episodes = int(selected.get("total_episodios") or 0)
                season_now, episode_now = int(new_season), int(new_episode)
                refreshed_errors = validate_position(season_now, episode_now, total_seasons, total_episodes,
                                                     episodes_by_season=fresh.get("_episodes_by_season"))
                if refreshed_errors:
                    # Metadata is still useful, but reject the now-invalid progress position.
                    selected["temporada_atual"], selected["episodio_atual"] = previous_position
                    selected["episodios_nao_assistidos"] = old_pending
                else:
                    selected["episodios_nao_assistidos"] = (remaining_count(season_now, episode_now, total_seasons, total_episodes)
                                                            if total_seasons and total_episodes else old_pending)
                if fresh.get("_show_ended"):
                    episodes_by_season = fresh.get("_episodes_by_season") or {}
                    last_season = max(episodes_by_season, default=0)
                    last_episode = max(episodes_by_season.get(last_season, []), default=0)
                    watched_final_episode = (not refreshed_errors and season_now == last_season
                                             and episode_now == last_episode and last_episode > 0)
                    if watched_final_episode and old_status not in ("Finalizada", "Concluída"):
                        selected["vezes_completada"] = int(selected.get("vezes_completada") or 0) + 1
                    if watched_final_episode:
                        selected["status"] = "Finalizada"
                        selected["imdb_atualizavel"] = False
                        result_message = "Você já assistiu ao último episódio lançado; marquei a série como Finalizada e desativei novas atualizações."
                        if old_status not in ("Finalizada", "Concluída"):
                            result_message += " Somei uma conclusão."
                    else:
                        result_message = (f"O TVmaze informa que a série terminou, mas sua posição está em T{season_now} Ep {episode_now}. "
                                          f'Mantive o status "{selected.get("status")}". Assista ao último episódio e atualize novamente para concluir automaticamente.')
                else:
                    result_message = "Dados e formulário atualizados com sucesso usando o TVmaze."
                selected["data_atualizacao"] = date.today().isoformat()
                save_record(selected, new_notes, selected_id)
                st.session_state["flash"] = ("warning" if refreshed_errors else "success",
                                               result_message if not refreshed_errors else
                                               "Dados do TVmaze atualizados; o progresso informado não existe nessa série. Ajuste temporada/episódio.")
                st.rerun()
            except TVMazeLookupError as exc:
                st.session_state["flash"] = ("error", f"As alterações locais foram salvas, mas o TVmaze falhou: {exc}")
                st.rerun()

    if not selected.get("imdb_atualizavel"):
        st.caption("A atualização pelo TVmaze está desativada para este título.")

    if st.button("Fechar", key=f"close_{selected_id}", use_container_width=True):
        st.session_state.pop("selected_id", None)
        st.rerun()


def media_page() -> None:
    st.title("🎬 Séries e Filmes")
    flash = st.session_state.get("flash")
    if flash and not st.session_state.get("selected_id"):
        level, message = flash
        if level == "success":
            st.success(message)
        elif level == "warning":
            st.warning(message)
        else:
            st.error(message)
        st.session_state.pop("flash", None)
    with st.expander("＋ Cadastrar uma série, anime ou filme", expanded=False):
        add_form()
    records = list_records()
    if not records:
        st.info("Ainda não há títulos. Use o formulário acima para cadastrar o primeiro.")
        return

    for item in records:
        item["_faltantes"] = int(item.get("episodios_nao_assistidos") or 0)
        item["_nota"] = float(item.get("nota_imdb") or 0)
    total_hours = sum(duration_minutes(item.get("duracao_media_ep")) * max(
        0, int(item.get("total_episodios") or 0) - int(item.get("episodios_nao_assistidos") or 0)
    ) for item in records) / 60
    pending_count = sum(item["_faltantes"] for item in records)
    status_counts = {}
    for item in records:
        status = item.get("status") or "Sem status"
        status_counts[status] = status_counts.get(status, 0) + 1

    metric_col, chart_col = st.columns([2.2, 1])
    with metric_col:
        m1, m2, m3 = st.columns(3)
        m1.metric("Títulos", len(records))
        m2.metric("Horas assistidas (estimativa)", f"{total_hours:.1f} h")
        m3.metric("Episódios pendentes", pending_count)
    with chart_col:
        figure = px.pie(names=list(status_counts), values=list(status_counts.values()), hole=0.52)
        figure.update_layout(height=235, margin=dict(l=0, r=0, t=5, b=0), showlegend=True,
                             legend=dict(orientation="h", yanchor="bottom", y=-0.18, xanchor="center", x=0.5),
                             annotations=[dict(text="Status", x=0.5, y=0.5, showarrow=False, font_size=12)])
        st.plotly_chart(figure, use_container_width=True, config={"displayModeBar": False})

    search_text = st.text_input("Pesquisar por nome", placeholder="Digite parte do título ou nome alternativo")
    with st.expander("Filtros", expanded=True):
        genres = sorted({genre.strip() for item in records for genre in re.split(r"\s*[/,]\s*", str(item.get("genero") or "")) if genre.strip()})
        statuses = sorted({str(item.get("status")) for item in records if item.get("status")})
        selected_genres = st.multiselect("Categoria / gênero", genres, placeholder="Todos (sem filtro)")
        selected_status = st.multiselect("Status", statuses, placeholder="Todos (sem filtro)")
        min_rating, max_rating = st.slider("Nota TVmaze", 0.0, 10.0, (0.0, 10.0), 0.1)
        only_pending = st.checkbox("Mostrar apenas títulos com episódios pendentes")

    query = search_text.casefold().strip()
    visible = [item for item in records if
        (not query or query in str(item.get("titulo", "")).casefold())
        and (not selected_genres or any(genre.strip() in selected_genres for genre in re.split(r"\s*[/,]\s*", str(item.get("genero") or ""))))
        and (not selected_status or item.get("status") in selected_status)
        and min_rating <= item["_nota"] <= max_rating
        and (not only_pending or item["_faltantes"] > 0)]

    st.subheader(f"Catálogo · {len(visible)} título(s)")
    for start in range(0, len(visible), 3):
        cols = st.columns(3)
        for col, item in zip(cols, visible[start:start + 3]):
            with col:
                with st.container(border=True):
                    if item.get("foto_capa"):
                        st.image(item["foto_capa"], use_container_width=True)
                    else:
                        st.markdown("<div style='height:180px;display:grid;place-items:center;background:#17243a;border-radius:12px;font-size:3rem'>🎬</div>", unsafe_allow_html=True)
                    st.markdown(f"**{item.get('titulo', 'Sem título')}**")
                    st.caption(f"⭐ {item.get('nota_imdb', 0)} · {item.get('genero', '')} · {item.get('ano_lancamento', '')}")
                    position = f"T{item.get('temporada_atual', 0)} · Ep {item.get('episodio_atual', 0)}" if int(item.get("temporada_atual") or 0) else "Posição não informada"
                    st.write(f"**{item.get('status', '—')}** · {position}")
                    st.caption(f"{item['_faltantes']} episódios pendentes")
                    if st.button("Ver detalhes", key=f"open_{item['_id']}", use_container_width=True):
                        st.session_state["selected_id"] = item["_id"]
                        st.rerun()

    selected_id = st.session_state.get("selected_id")
    selected = next((item for item in records if item["_id"] == selected_id), None)
    if selected:
        show_details(selected)


def settings_page() -> None:
    st.title("🔄 Backup & Configurações")
    st.subheader("Catálogo TVmaze")
    st.success("Integração pública configurada. Não é necessária chave de API ou conta.")
    st.markdown(
        "Séries, animes e doramas são localizados pelo ID do IMDb no TVmaze. A nota exibida é a nota do TVmaze, "
        "e pode diferir da nota IMDb. Filmes são cadastrados manualmente. O TVmaze exige atribuição e publica "
        "seus dados sob CC BY-SA. [Documentação oficial da API](https://www.tvmaze.com/api)."
    )
    st.divider()
    st.write("Gera um ZIP com os arquivos de `data/`, incluindo todos os registros Markdown.")
    if st.button("Gerar Backup Zip", type="primary"):
        target = make_backup()
        st.success(f"Backup criado: {target.relative_to(ROOT_DIR)}")
        st.download_button("Baixar backup", target.read_bytes(), file_name=target.name, mime="application/zip")
    st.caption(f"Pasta de dados local: {ROOT_DIR / 'data'}")


st.sidebar.title("Media Center")
page = st.sidebar.radio("Navegação", ["🎬 Séries e Filmes", "📊 Finanças", "🔄 Backup & Configurações"])
if page == "🎬 Séries e Filmes":
    media_page()
elif page == "📊 Finanças":
    st.title("📊 Finanças")
    st.info("Este módulo fica reservado para uma próxima etapa.")
else:
    settings_page()
