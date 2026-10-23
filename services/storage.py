from __future__ import annotations

import re
import unicodedata
from datetime import date
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SERIES_DIR = ROOT_DIR / "data" / "series"
BACKUP_DIR = ROOT_DIR / "backups"

FRONTMATTER_FIELDS = (
    "titulo", "link_imdb", "foto_capa", "nota_imdb", "genero", "ano_lancamento",
    "duracao_media_ep", "status", "vezes_completada", "temporada_atual",
    "episodio_atual", "total_temporadas", "total_episodios",
    "episodios_nao_assistidos", "imdb_atualizavel", "data_atualizacao",
)


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value.lower()).strip("-")
    return slug or "midia"


def ensure_data_dirs() -> None:
    SERIES_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _safe_yaml_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_markdown(record: dict, notes: str = "") -> str:
    data = {key: record.get(key) for key in FRONTMATTER_FIELDS}
    lines = ["---"]
    for key in FRONTMATTER_FIELDS:
        lines.append(f"{key}: {_safe_yaml_value(data[key])}")
    lines += ["---", "", "# Minhas Anotações", notes.strip(), ""]
    return "\n".join(lines)


def _parse_file(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---\n"):
            return None
        _, frontmatter, body = raw.split("---", 2)
        record = {}
        for line in frontmatter.splitlines():
            if not line.strip():
                continue
            key, separator, value = line.partition(":")
            if not separator:
                continue
            value = value.strip()
            if value.startswith('"'):
                parsed = json.loads(value)
            elif value in ("true", "false"):
                parsed = value == "true"
            elif value == "null":
                parsed = None
            else:
                try:
                    parsed = float(value) if "." in value else int(value)
                except ValueError:
                    parsed = value
            record[key.strip()] = parsed
        record["_id"] = path.stem
        record["_path"] = str(path)
        record["_notes"] = body.split("# Minhas Anotações", 1)[-1].strip()
        return record
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def list_records() -> list[dict]:
    ensure_data_dirs()
    records = [record for path in SERIES_DIR.glob("*.md") if (record := _parse_file(path))]
    return sorted(records, key=lambda item: str(item.get("titulo", "")).casefold())


def save_record(record: dict, notes: str = "", record_id: str | None = None) -> Path:
    ensure_data_dirs()
    if record_id:
        path = SERIES_DIR / f"{slugify(record_id)}.md"
    else:
        base = slugify(str(record.get("titulo", "midia")))
        path = SERIES_DIR / f"{base}.md"
        suffix = 2
        while path.exists():
            existing = _parse_file(path)
            if existing and existing.get("link_imdb") == record.get("link_imdb"):
                break
            path = SERIES_DIR / f"{base}-{suffix}.md"
            suffix += 1
    normalized = {key: record.get(key) for key in FRONTMATTER_FIELDS}
    normalized["data_atualizacao"] = str(normalized.get("data_atualizacao") or date.today().isoformat())
    path.write_text(render_markdown(normalized, notes), encoding="utf-8")
    return path


def delete_record(record_id: str) -> None:
    path = SERIES_DIR / f"{slugify(record_id)}.md"
    if path.exists():
        path.unlink()
