from __future__ import annotations

from datetime import date
from pathlib import Path
import re
import json
import zipfile

import plotly.express as px
import streamlit as st

from services.tvmaze_client import TVMazeLookupError, extract_imdb_id, fetch_title
from services.progress import (
    duration_minutes,
    next_episode_to_watch,
    remaining_count,
    remaining_count_by_season,
    validate_position,
)
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
NEXT_EPISODE_PREFIX = "Próximo episódio (TVmaze): "
EPISODE_MAP_PREFIX = "<!-- TVmaze episódios por temporada: "


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


def get_next_episode_note(notes: str) -> str:
    return next((line.strip() for line in str(notes or "").splitlines()
                 if line.strip().startswith(NEXT_EPISODE_PREFIX)), "")


def get_episode_map_note(notes: str) -> str:
    return next((line.strip() for line in str(notes or "").splitlines()
                 if line.strip().startswith(EPISODE_MAP_PREFIX)), "")


def parse_episode_map(notes: str) -> dict[int, list[int]]:
    line = get_episode_map_note(notes)
    if not line or not line.endswith(" -->"):
        return {}
    try:
        serialized = line[len(EPISODE_MAP_PREFIX):-4]
        data = json.loads(serialized)
        if isinstance(data, dict) and "episodes" in data:
            data = data["episodes"]
        return {int(season): sorted({int(number) for number in numbers})
                for season, numbers in data.items()}
    except (ValueError, TypeError, AttributeError):
        return {}


def tvmaze_status_from_notes(notes: str) -> str:
    line = get_episode_map_note(notes)
    if not line or not line.endswith(" -->"):
        return ""
    try:
        data = json.loads(line[len(EPISODE_MAP_PREFIX):-4])
        return str(data.get("status") or "") if isinstance(data, dict) else ""
    except (ValueError, TypeError, AttributeError):
        return ""


def episode_map_note(episodes_by_season: dict[int, list[int]] | None, tvmaze_status: str = "") -> str:
    if not episodes_by_season:
        return ""
    compact = {str(season): sorted({int(number) for number in numbers})
               for season, numbers in episodes_by_season.items()}
    serialized = json.dumps({"episodes": compact, "status": tvmaze_status}, separators=(",", ":"), ensure_ascii=True)
    return f"{EPISODE_MAP_PREFIX}{serialized} -->"


def user_notes_without_airing(notes: str) -> str:
    return "\n".join(line for line in str(notes or "").splitlines()
                      if not line.strip().startswith((NEXT_EPISODE_PREFIX, EPISODE_MAP_PREFIX))).strip()


def next_episode_note(episode: dict | None) -> str:
    if not episode:
        return ""
    date_text = episode.get("airdate")
    if date_text:
        try:
            date_text = date.fromisoformat(date_text).strftime("%d/%m/%Y")
        except ValueError:
            pass
    else:
        date_text = "data ainda não informada"
    parts = []
    if episode.get("season") and episode.get("number"):
        parts.append(f"T{episode['season']} Ep {episode['number']}")
    if episode.get("name"):
        parts.append(str(episode["name"]))
    parts.append(str(date_text))
    return NEXT_EPISODE_PREFIX + " — ".join(parts)


def combine_notes_with_airing(notes: str, airing_line: str, episode_map_line: str = "") -> str:
    clean_notes = user_notes_without_airing(notes)
    parts = [part for part in (clean_notes, airing_line.strip(), episode_map_line.strip()) if part]
    return "\n\n".join(parts)


def persist_status_filter() -> None:
    st.session_state["catalog_status_filter"] = st.session_state["_catalog_status_filter"]


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
                "episodios_nao_assistidos": (remaining_count_by_season(int(season), int(episode), episodes_by_season)
                                             if episodes_by_season else remaining_count(int(season), int(episode), record["total_temporadas"], record["total_episodios"])),
                "imdb_atualizavel": not is_manual and not watched_final_episode,
                "data_atualizacao": date.today().isoformat(),
            })
            if watched_final_episode and status not in ("Finalizada", "Concluída"):
                record["vezes_completada"] += 1
            save_record(record, combine_notes_with_airing(
                "", next_episode_note(record.get("_next_episode")),
                episode_map_note(record.get("_episodes_by_season"), record.get("_tvmaze_status", ""))))
            st.session_state["flash"] = ("success", f"{record['titulo']} foi cadastrado com sucesso.")
            st.rerun()
        except TVMazeLookupError as exc:
            st.error(str(exc))


def dismiss_details_dialog() -> None:
    st.session_state.pop("selected_id", None)


@st.dialog("Detalhes do título", width="large", on_dismiss=dismiss_details_dialog)
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
        airing_line = get_next_episode_note(selected.get("_notes", ""))
        if airing_line:
            st.info(airing_line.removeprefix(NEXT_EPISODE_PREFIX))

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
        new_notes = st.text_area("Minhas anotações", value=user_notes_without_airing(selected.get("_notes", "")), height=72,
                                 key=f"detail_notes_{selected_id}")
        save_col, refresh_col = st.columns(2)
        saved = save_col.form_submit_button("Salvar alterações", type="primary", use_container_width=True)
        refresh = refresh_col.form_submit_button("↻ Salvar e atualizar dados do TVmaze", type="secondary",
                                                  use_container_width=True, disabled=not selected.get("imdb_atualizavel"))

    if saved or refresh:
        local_position_errors = validate_position(
            int(new_season), int(new_episode), int(selected.get("total_temporadas") or 0),
            int(selected.get("total_episodios") or 0), is_movie=record_is_movie(selected),
        )
        # A refresh may discover newer seasons than the local Markdown currently knows.
        position_errors = [] if refresh else local_position_errors
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
            previous_airing_line = get_next_episode_note(selected.get("_notes", ""))
            previous_episode_map_line = get_episode_map_note(selected.get("_notes", ""))
            progress_unchanged = (int(new_season), int(new_episode)) == previous_position
            if new_status in ("Finalizada", "Concluída"):
                pending_on_save = 0
            elif progress_unchanged:
                pending_on_save = previous_pending
            else:
                pending_on_save = remaining_count(int(new_season), int(new_episode), int(selected.get("total_temporadas") or 0), int(selected.get("total_episodios") or 0))
            selected.update({"status": new_status, "temporada_atual": int(new_season), "episodio_atual": int(new_episode),
                             "vezes_completada": int(new_times),
                             "episodios_nao_assistidos": pending_on_save,
                             "data_atualizacao": date.today().isoformat()})
            save_record(selected, combine_notes_with_airing(new_notes, previous_airing_line, previous_episode_map_line), selected_id)
            if not refresh:
                st.session_state["flash"] = ("success", "Alterações salvas com sucesso.")
                st.rerun()
            try:
                with st.spinner("Consultando TVmaze agora…"):
                    fresh = fetch_title(selected["link_imdb"], record_category(selected), title_hint=selected.get("titulo"))
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
                    season_episode_map = fresh.get("_episodes_by_season") or {}
                    if season_episode_map:
                        selected["episodios_nao_assistidos"] = remaining_count_by_season(season_now, episode_now, season_episode_map)
                    elif total_seasons and total_episodes:
                        selected["episodios_nao_assistidos"] = remaining_count(season_now, episode_now, total_seasons, total_episodes)
                    else:
                        selected["episodios_nao_assistidos"] = old_pending
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
                    caught_up = (not refreshed_errors and bool(fresh.get("_episodes_by_season"))
                                 and season_now == max(fresh["_episodes_by_season"])
                                 and episode_now == max(fresh["_episodes_by_season"][max(fresh["_episodes_by_season"])]))
                    if caught_up and str(selected.get("status") or "").casefold() == "assistindo":
                        selected["status"] = "Aguardando"
                        next_episode = fresh.get("_next_episode")
                        next_date = (next_episode or {}).get("airdate")
                        if next_date:
                            try:
                                next_date = date.fromisoformat(next_date).strftime("%d/%m/%Y")
                            except ValueError:
                                pass
                            result_message = f"Você chegou ao último episódio disponível. Status alterado para Aguardando; o próximo está previsto para {next_date}."
                        else:
                            result_message = "Você chegou ao último episódio disponível. Status alterado para Aguardando; o TVmaze ainda não informa a data do próximo episódio."
                    else:
                        result_message = "Dados e formulário atualizados com sucesso usando o TVmaze."
                selected["data_atualizacao"] = date.today().isoformat()
                updated_airing_line = next_episode_note(fresh.get("_next_episode"))
                save_record(selected, combine_notes_with_airing(
                    new_notes, updated_airing_line,
                    episode_map_note(fresh.get("_episodes_by_season"), fresh.get("_tvmaze_status", ""))), selected_id)
                st.session_state["flash"] = ("warning" if refreshed_errors else "success",
                                               result_message if not refreshed_errors else
                                               "Dados do TVmaze atualizados; o progresso informado não existe nessa série. Ajuste temporada/episódio.")
                st.rerun()
            except TVMazeLookupError as exc:
                if local_position_errors:
                    # Do not leave a position in the file that could not be checked against TVmaze.
                    selected["temporada_atual"], selected["episodio_atual"] = previous_position
                    selected["episodios_nao_assistidos"] = previous_pending
                    save_record(selected, combine_notes_with_airing(
                        new_notes, previous_airing_line, previous_episode_map_line), selected_id)
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
    records = list_records()
    if not records:
        with st.expander("＋ Cadastrar uma série, anime ou filme", expanded=False):
            add_form()
        st.info("Ainda não há títulos. Use o formulário acima para cadastrar o primeiro.")
        return

    with st.expander("＋ Cadastrar uma série, anime ou filme", expanded=False):
        add_form()

    for item in records:
        item["_faltantes"] = int(item.get("episodios_nao_assistidos") or 0)
        item["_nota"] = float(item.get("nota_imdb") or 0)
    total_hours = sum(duration_minutes(item.get("duracao_media_ep")) * max(
        0, int(item.get("total_episodios") or 0) - int(item.get("episodios_nao_assistidos") or 0)
    ) for item in records) / 60
    pending_count = sum(item["_faltantes"] for item in records)
    watched_episodes = sum(max(0, int(item.get("total_episodios") or 0) - int(item.get("episodios_nao_assistidos") or 0))
                           for item in records)
    currently_watching = sum(str(item.get("status") or "").casefold() == "assistindo" for item in records)
    status_counts = {}
    for item in records:
        status = item.get("status") or "Sem status"
        status_counts[status] = status_counts.get(status, 0) + 1

    metrics = st.columns(5)
    metrics[0].metric("Títulos", len(records))
    metrics[1].metric("Episódios assistidos (estim.)", watched_episodes)
    metrics[2].metric("Horas assistidas (estim.)", f"{total_hours:.1f} h")
    metrics[3].metric("Episódios pendentes", pending_count)
    metrics[4].metric("Assistindo", currently_watching)

    chart_col, _ = st.columns([1, 2.5])
    with chart_col:
        figure = px.pie(names=list(status_counts), values=list(status_counts.values()), hole=0.52)
        figure.update_layout(height=235, margin=dict(l=0, r=0, t=5, b=0), showlegend=True,
                             legend=dict(orientation="h", yanchor="bottom", y=-0.18, xanchor="center", x=0.5),
                             annotations=[dict(text="Status", x=0.5, y=0.5, showarrow=False, font_size=12)])
        st.plotly_chart(figure, use_container_width=True, config={"displayModeBar": False})

    search_text = st.text_input("Pesquisar por nome", placeholder="Digite parte do título ou nome alternativo",
                                key="catalog_search")
    with st.expander("Filtros", expanded=True):
        genres = sorted({genre.strip() for item in records for genre in re.split(r"\s*[/,]\s*", str(item.get("genero") or "")) if genre.strip()})
        statuses = sorted({str(item.get("status")) for item in records if item.get("status")})
        selected_genres = st.multiselect("Categoria / gênero", genres, placeholder="Todos (sem filtro)",
                                         key="catalog_genres")
        if "catalog_status_filter" not in st.session_state:
            st.session_state["catalog_status_filter"] = ["Assistindo"] if "Assistindo" in statuses else []
        if "_catalog_status_filter" not in st.session_state:
            st.session_state["_catalog_status_filter"] = st.session_state["catalog_status_filter"]
        selected_status = st.multiselect("Status", statuses, placeholder="Todos (sem filtro)",
                                         key="_catalog_status_filter", on_change=persist_status_filter)
        min_rating, max_rating = st.slider("Nota TVmaze", 0.0, 10.0, (0.0, 10.0), 0.1,
                                           key="catalog_rating_range")
        only_pending = st.checkbox("Mostrar apenas títulos com episódios pendentes", key="catalog_only_pending")
        sort_order = st.selectbox("Ordenar por", ["Título (A–Z)", "Episódios pendentes (maior primeiro)",
                                                   "Episódios pendentes (menor primeiro)"], key="catalog_sort")

    query = search_text.casefold().strip()
    visible = [item for item in records if
        (not query or query in str(item.get("titulo", "")).casefold())
        and (not selected_genres or any(genre.strip() in selected_genres for genre in re.split(r"\s*[/,]\s*", str(item.get("genero") or ""))))
        and (not selected_status or item.get("status") in selected_status)
        and min_rating <= item["_nota"] <= max_rating
        and (not only_pending or item["_faltantes"] > 0)]
    if sort_order == "Episódios pendentes (maior primeiro)":
        visible.sort(key=lambda item: (-item["_faltantes"], str(item.get("titulo", "")).casefold()))
    elif sort_order == "Episódios pendentes (menor primeiro)":
        visible.sort(key=lambda item: (item["_faltantes"], str(item.get("titulo", "")).casefold()))

    selected_id = st.session_state.get("selected_id")
    selected = next((item for item in records if item["_id"] == selected_id), None)
    if selected:
        # Keep search and filter widgets rendered, but skip rebuilding the catalog cards.
        show_details(selected)
        st.stop()

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
                    next_airing = get_next_episode_note(item.get("_notes", ""))
                    if next_airing and item.get("status") == "Aguardando":
                        st.caption(next_airing)
                    if item.get("status") == "Assistindo":
                        episodes_by_season = parse_episode_map(item.get("_notes", ""))
                        next_position = next_episode_to_watch(
                            int(item.get("temporada_atual") or 0), int(item.get("episodio_atual") or 0), episodes_by_season)
                        if next_position:
                            st.caption(f"Próximo episódio: T{next_position[0]} Ep {next_position[1]}")
                        else:
                            st.caption("Nenhum próximo episódio lançado no catálogo")
                        if st.button("Marcar próximo episódio como assistido", key=f"watch_next_{item['_id']}",
                                     use_container_width=True, disabled=next_position is None):
                            next_season, next_episode = next_position
                            item["temporada_atual"] = next_season
                            item["episodio_atual"] = next_episode
                            if episodes_by_season:
                                item["episodios_nao_assistidos"] = remaining_count_by_season(
                                    next_season, next_episode, episodes_by_season)
                            else:
                                item["episodios_nao_assistidos"] = max(0, item["_faltantes"] - 1)
                            latest_season = max(episodes_by_season, default=0)
                            latest_episode = max(episodes_by_season.get(latest_season, []), default=0)
                            caught_up = (next_season == latest_season and next_episode == latest_episode
                                         and latest_episode > 0)
                            catalog_status = tvmaze_status_from_notes(item.get("_notes", "")).casefold()
                            if caught_up and catalog_status == "ended":
                                item["status"] = "Finalizada"
                                item["episodios_nao_assistidos"] = 0
                                item["imdb_atualizavel"] = False
                                item["vezes_completada"] = int(item.get("vezes_completada") or 0) + 1
                                confirmation = f"T{next_season} Ep {next_episode} marcado como assistido. Série finalizada; conclusão registrada."
                            elif caught_up:
                                item["status"] = "Aguardando"
                                airing_date = get_next_episode_note(item.get("_notes", ""))
                                airing_date = airing_date.removeprefix(NEXT_EPISODE_PREFIX) if airing_date else "data do próximo episódio ainda não informada"
                                confirmation = f"T{next_season} Ep {next_episode} marcado como assistido. Status alterado para Aguardando; {airing_date}."
                            else:
                                confirmation = f"T{next_season} Ep {next_episode} marcado como assistido."
                            save_record(item, combine_notes_with_airing(
                                user_notes_without_airing(item.get("_notes", "")), next_airing,
                                get_episode_map_note(item.get("_notes", ""))), item["_id"])
                            st.session_state["flash"] = ("success", confirmation)
                            st.rerun()
                    if st.button("Ver detalhes", key=f"open_{item['_id']}", use_container_width=True):
                        st.session_state["selected_id"] = item["_id"]
                        st.rerun()

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
