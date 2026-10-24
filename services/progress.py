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


def watched_count_by_season(season: int, episode: int, episodes_by_season: dict[int, list[int]]) -> int:
    """Count released episodes using TVmaze's actual season/episode numbers."""
    if season <= 0 or episode <= 0:
        return 0
    watched_before = sum(len(numbers) for season_number, numbers in episodes_by_season.items()
                         if int(season_number) < season)
    watched_in_season = sum(1 for number in episodes_by_season.get(season, []) if int(number) <= episode)
    return watched_before + watched_in_season


def remaining_count_by_season(season: int, episode: int, episodes_by_season: dict[int, list[int]]) -> int:
    total = sum(len(numbers) for numbers in episodes_by_season.values())
    return max(0, total - watched_count_by_season(season, episode, episodes_by_season))


def next_episode_to_watch(
    season: int,
    episode: int,
    episodes_by_season: dict[int, list[int]],
) -> tuple[int, int] | None:
    if not episodes_by_season:
        return (1, 1) if season <= 0 or episode <= 0 else (season, episode + 1)
    seasons = sorted(int(value) for value, numbers in episodes_by_season.items() if numbers)
    if not seasons:
        return None
    if season <= 0 or episode <= 0:
        first_season = seasons[0]
        return first_season, min(episodes_by_season[first_season])
    next_numbers = [number for number in episodes_by_season.get(season, []) if number > episode]
    if next_numbers:
        return season, min(next_numbers)
    for next_season in seasons:
        if next_season > season:
            return next_season, min(episodes_by_season[next_season])
    return None


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
