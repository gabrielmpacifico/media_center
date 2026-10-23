from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

import requests


BASE_URL = "https://api.tvmaze.com"
USER_AGENT = "MediaCenter/1.0 (personal local library)"


class TVMazeLookupError(RuntimeError):
    pass


def extract_imdb_id(url: str) -> str:
    match = re.search(r"/title/(tt\d+)", url.strip(), re.IGNORECASE)
    if not match:
        raise TVMazeLookupError("Informe um link válido do IMDb no formato https://www.imdb.com/title/tt1234567/")
    parsed = urlparse(url.strip())
    if parsed.netloc and not (parsed.netloc.lower() == "imdb.com" or parsed.netloc.lower().endswith(".imdb.com")):
        raise TVMazeLookupError("O domínio do link precisa ser imdb.com.")
    return match.group(1).lower()


def _get_json(url: str, *, params: dict | None = None):
    try:
        response = requests.get(url, params=params, timeout=20, headers={"User-Agent": USER_AGENT})
    except requests.Timeout as exc:
        raise TVMazeLookupError("O TVmaze demorou para responder. Tente novamente em instantes.") from exc
    except requests.RequestException as exc:
        raise TVMazeLookupError(f"Não foi possível acessar o TVmaze: {exc}") from exc
    if response.status_code == 404:
        raise TVMazeLookupError("Não encontrei essa série no TVmaze pelo ID do IMDb. Tente novamente em modo de cadastro manual.")
    if response.status_code == 429:
        raise TVMazeLookupError("O TVmaze limitou temporariamente as consultas. Aguarde alguns segundos e tente novamente.")
    if not response.ok:
        raise TVMazeLookupError(f"O TVmaze respondeu com HTTP {response.status_code}.")
    try:
        return response.json()
    except ValueError as exc:
        raise TVMazeLookupError("O TVmaze retornou uma resposta inválida.") from exc


def fetch_title(url: str, media_kind: str | None = None) -> dict:
    """Fetch one TVmaze show by its linked IMDb ID. Call only on a user action."""
    imdb_id = extract_imdb_id(url)
    if media_kind == "Filme":
        raise TVMazeLookupError("O TVmaze é voltado a séries e programas de TV. Cadastre filmes manualmente.")
    matched = _get_json(f"{BASE_URL}/lookup/shows", params={"imdb": imdb_id})
    show_id = matched.get("id")
    if not show_id:
        raise TVMazeLookupError("O TVmaze não encontrou uma série associada a esse ID do IMDb. Você pode cadastrá-la manualmente.")

    details = _get_json(f"{BASE_URL}/shows/{show_id}", params={"embed": "episodes"})
    embedded = details.get("_embedded") or {}
    episodes = embedded.get("episodes") or []
    today = date.today().isoformat()
    per_season: dict[int, list[int]] = {}
    for episode in episodes:
        season_number, episode_number = episode.get("season"), episode.get("number")
        airdate = episode.get("airdate")
        if not isinstance(season_number, int) or season_number < 1 or not isinstance(episode_number, int):
            continue
        # The list can include announced future episodes; only released episodes count toward progress.
        if airdate and airdate > today:
            continue
        if not airdate and episode.get("airstamp"):
            if str(episode["airstamp"])[:10] > today:
                continue
        per_season.setdefault(season_number, []).append(episode_number)
    for season_number in per_season:
        per_season[season_number] = sorted(set(per_season[season_number]))

    seasons = sorted(per_season)
    total_episodes = sum(len(numbers) for numbers in per_season.values())
    runtime = details.get("averageRuntime") or details.get("runtime") or 0
    premiered = str(details.get("premiered") or "")
    genres = [str(genre).strip() for genre in details.get("genres") or [] if str(genre).strip()]
    category = media_kind or "Série"
    genre_text = " / ".join(dict.fromkeys([category, *genres]))
    ended = str(details.get("status") or "").casefold() == "ended"

    return {
        "titulo": details.get("name") or matched.get("name") or imdb_id,
        "link_imdb": f"https://www.imdb.com/title/{imdb_id}/",
        "foto_capa": (details.get("image") or {}).get("original") or (details.get("image") or {}).get("medium") or "",
        "nota_imdb": round(float(((details.get("rating") or {}).get("average")) or 0), 1),
        "genero": genre_text,
        "ano_lancamento": int(premiered[:4]) if premiered[:4].isdigit() else 0,
        "duracao_media_ep": f"{int(runtime)} min" if runtime else "",
        "total_temporadas": max(seasons, default=0),
        "total_episodios": total_episodes,
        "_episodes_by_season": per_season,
        "_tvmaze_status": details.get("status") or "",
        "_show_ended": ended,
        "data_atualizacao": today,
    }
