from __future__ import annotations

import re


def duration_minutes(value: str | None) -> int:
    if not value:
        return 0
    match = re.search(r"(\d+)", str(value))
    return int(match.group(1)) if match else 0


def watched_count(season: int, episode: int, total_seasons: int, total_episodes: int) -> int:
    if total_episodes <= 0:
        return 0
    if total_seasons <= 0:
        return min(max(episode, 0), total_episodes)
    per_season = total_episodes / total_seasons
    return min(total_episodes, max(0, round((max(season - 1, 0) * per_season) + max(episode, 0))))


def remaining_count(season: int, episode: int, total_seasons: int, total_episodes: int) -> int:
    return max(0, total_episodes - watched_count(season, episode, total_seasons, total_episodes))


def validate_position(
    season: int,
    episode: int,
    total_seasons: int,
    total_episodes: int,
    is_movie: bool = False,
    episodes_by_season: dict[int, list[int]] | None = None,
) -> list[str]:
    errors = []
    if is_movie:
        if season != 0 or episode != 0:
            errors.append("Filmes devem ficar com temporada e episódio iguais a 0.")
        return errors
    # A zero/zero position represents an intentionally unknown position in imported records.
    if season == 0 and episode == 0:
        return errors
    if season < 1:
        errors.append("Informe uma temporada maior que 0, ou use 0/0 para posição desconhecida.")
    if episode < 1:
        errors.append("Informe um episódio maior que 0.")
    if episodes_by_season is None:
        if total_seasons > 0 and season > total_seasons:
            errors.append(f"A temporada {season} não existe: o catálogo informa {total_seasons} temporada(s).")
        if total_episodes > 0 and episode > total_episodes:
            errors.append(f"O episódio {episode} ultrapassa os {total_episodes} episódios lançados no total.")
    if episodes_by_season is not None and season > 0:
        if season not in episodes_by_season:
            errors.append(f"A temporada {season} não tem episódios lançados no catálogo TVmaze.")
        elif episode > 0 and episode not in episodes_by_season[season]:
            maximum = max(episodes_by_season[season], default=0)
            errors.append(
                f"O episódio {episode} não existe na temporada {season}; o catálogo lista episódios de 1 a {maximum}."
                if maximum else f"A temporada {season} ainda não tem episódios lançados no catálogo."
            )
    return errors
