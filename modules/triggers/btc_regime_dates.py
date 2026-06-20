"""Analyse BTC H4 jour par jour — page Date ON/OFF (lecture géométrique Gemini)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from modules.agent.providers.base import AnalyzeContext
from modules.agent.providers.registry import analyze_with_strategy
from modules.capture.btc_h4_date_range import (
    BTC_REGIME_LAYOUT_ID,
    capture_btc_h4_regime_chart,
    compute_date_window,
)
from modules.selection.resolver import resolve_symbol_tv
from modules.triggers import db_manager
from modules.triggers.btc_context import (
    BTC_AGENT_ID,
    BTC_TOKEN,
    _strip_markdown_fence,
)

logger = logging.getLogger("visio_gemini.triggers.btc_regime_dates")

_VALID_ETATS = frozenset({"OUI", "NON", "LIMITE", "OUI/NON"})
_VALID_TRANSITIONS = frozenset({"ENTREE", "SORTIE"})
_VALID_ZONES = frozenset({"debut", "midi", "milieu", "fin"})
_ZONE_LABELS = {"debut": "00h", "midi": "12h", "milieu": "12h", "fin": "18h"}
_REGIME_TEMPERATURE = 0.0

_FRENCH_MONTHS: tuple[str, ...] = (
    "",
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)

_GEOMETRY_PROMPT_BODY = """RÔLE
Tu es un lecteur d'image précis. Tu analyses une chart TradingView BTC H4
(ligne cyan vs bloc bleu · layout Ten Kan) et tu extrais l'état à des points
temporels précis.

REPÈRES VISUELS
- Les TRAITS VERTICAUX sont les séparateurs de jours (00h00).
- Le QUADRILLAGE (grille) t'aide à viser précisément.
- Ligne CYAN = indicateur à lire · BLOC BLEU = zone de référence.

POINTS À LIRE — 2 par jour (OBLIGATOIRE)
Pour CHAQUE jour ci-dessous, produis EXACTEMENT 2 entrées dans "jours" :
- index X.0, zone "debut" → lecture PILE SUR le trait vertical (00h00)
- index X.5, zone "midi"  → lecture PILE AU MILIEU entre 2 traits (12h00)

Jours à couvrir (ordre chronologique) :
{jours_list}

RÈGLE D'ÉTAT (lis le graphique point par point)
Analyse CHAQUE point individuellement en regardant le graphique.
À cet instant précis : le prix / la ligne cyan est-elle AU-DESSUS ou EN DESSOUS
de la Tenkan (ou au-dessus vs dans/sous le bloc bleu) ?

- "oui"     → clairement AU-DESSUS (ligne au-dessus du bloc / prix > Tenkan)
- "non"     → clairement EN DESSOUS (ligne dans ou sous le bloc)
- "oui/non" → ambigu, illisible, ou pile sur le bord → ne pas trancher

⚠️ ANTI-PARESSE (CRUCIAL — respecte ces règles) :
- "etat": "oui" SEULEMENT si c'est clairement au-dessus. Ne réponds PAS "oui"
  par défaut sur tous les points.
- Il est ANORMAL que les {n_points} points soient tous "oui". Sur {n_days} jours
  de marché, il y a forcément des phases sous la Tenkan / sous le bloc.
  Cherche activement les "non".
- Ne moyenne pas la journée : 00h et 12h peuvent (et doivent parfois) différer.
- Ne JAMAIS inventer : point illisible → "oui/non".

TRANSITIONS
Pour chaque CHANGEMENT d'état entre deux points consécutifs (oui→non ou non→oui),
ajoute une entrée dans "transitions" :
- type "SORTIE" = passe SOUS la Tenkan / entre dans le bloc (oui → non)
- type "ENTREE"  = repasse AU-DESSUS / sort du bloc (non → oui)
- "note" = entier 1 à 5 (5 = franc et net, 1 = hésitant au bord)

FORMAT DE SORTIE (JSON strict — objet englobant, PAS un tableau plat)
{{
  "jours": [
    {{ "index": {index_example}.0, "date": "{example_day_label}", "zone": "debut", "etat": "non" }},
    {{ "index": {index_example}.5, "date": "{example_day_label}", "zone": "midi",  "etat": "oui" }},
    {{ "index": {index_example_next}.0, "date": "{example_day_label_next}", "zone": "debut", "etat": "oui" }},
    {{ "index": {index_example_next}.5, "date": "{example_day_label_next}", "zone": "midi",  "etat": "non" }}
  ],
  "transitions": [
    {{ "date": "{example_day_label}", "zone": "midi", "index": {index_example}.5, "type": "ENTREE", "note": 4 }},
    {{ "date": "{example_day_label_next}", "zone": "midi", "index": {index_example_next}.5, "type": "SORTIE", "note": 3 }}
  ]
}}

CONTRAINTES
- Exactement {n_points} entrées dans "jours" ({n_days} jours × 2).
- Chaque entrée "jours" DOIT avoir : index, date, zone, etat.
- index = numéro du jour + .0 ou .5 (ex. 12.0 et 12.5 pour le 12).
- zone = uniquement "debut" (00h) ou "midi" (12h).
- Retourne UNIQUEMENT le JSON {{ "jours": [...], "transitions": [...] }},
  rien d'autre (pas de texte, pas de markdown).
- "transitions" peut être vide [] si aucun changement net, mais en pratique
  il y en a presque toujours plusieurs sur {n_days} jours."""


@dataclass(frozen=True)
class BtcTransition:
    date: str
    zone: str
    type: str
    note: int


@dataclass(frozen=True)
class BtcRegimeRow:
    date: str
    etat: str
    zone: str
    transition: str
    transition_note: int | None
    point_index: float | None = None


def format_day_label(d: date) -> str:
    """Libellé français « 10 juin »."""
    return f"{d.day} {_FRENCH_MONTHS[d.month]}"


def compute_fixed_days() -> tuple[str, ...]:
    """Liste figée des jours visibles (1er → dernier jour à l'écran)."""
    start_iso, end_iso = compute_date_window()
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    days: list[str] = []
    cur = start
    while cur <= end:
        days.append(format_day_label(cur))
        cur += timedelta(days=1)
    return tuple(days)


def build_regime_prompt(fixed_days: tuple[str, ...]) -> str:
    jours_list = "\n".join(f"- {d}" for d in fixed_days)
    example_day = _day_number_from_label(fixed_days[0]) if fixed_days else 10
    example_next = (
        _day_number_from_label(fixed_days[1])
        if len(fixed_days) > 1
        else (example_day + 1)
    )
    example_day_label = fixed_days[0] if fixed_days else "3 juin"
    example_day_label_next = fixed_days[1] if len(fixed_days) > 1 else "4 juin"
    return _GEOMETRY_PROMPT_BODY.format(
        jours_list=jours_list,
        index_example=example_day,
        index_example_next=example_next,
        example_day_label=example_day_label,
        example_day_label_next=example_day_label_next,
        n_days=len(fixed_days),
        n_points=len(fixed_days) * 2,
    )


def _day_number_from_label(label: str) -> int | None:
    m = re.match(r"^(\d{1,2})\b", label.strip())
    return int(m.group(1)) if m else None


def _extract_json_payload(text: str) -> dict | list | None:
    """Extrait un tableau JSON ou un objet {jours, transitions} depuis Gemini."""
    raw = _strip_markdown_fence(text)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, (list, dict)):
            return data
    except json.JSONDecodeError:
        pass
    arr_match = re.search(r"\[[\s\S]*\]", raw)
    if arr_match:
        try:
            data = json.loads(arr_match.group(0))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    obj_match = re.search(r"\{[\s\S]*\}", raw)
    if obj_match:
        try:
            data = json.loads(obj_match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None


def _points_from_payload(payload: dict | list | None) -> list[dict]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if isinstance(payload, dict):
        for key in ("jours", "points"):
            val = payload.get(key)
            if isinstance(val, list):
                return [p for p in val if isinstance(p, dict)]
    return []


def _transitions_from_payload(payload: dict | list | None) -> object:
    if isinstance(payload, dict):
        return payload.get("transitions")
    return None


def _point_sort_key(item: dict) -> float:
    idx = item.get("index")
    if idx is not None:
        try:
            return float(idx)
        except (TypeError, ValueError):
            pass
    zone = str(item.get("zone") or "").lower()
    if zone == "midi":
        return 0.5
    return 0.0


def _date_from_day_number(day_num: int, fixed_days: tuple[str, ...]) -> str | None:
    for label in fixed_days:
        n = _day_number_from_label(label)
        if n == day_num:
            return label
    return None


def _date_for_point(
    item: dict,
    point_idx: int,
    fixed_days: tuple[str, ...],
) -> str:
    date_label = str(item.get("date") or "").strip()
    if date_label:
        return date_label
    idx = item.get("index")
    if idx is not None:
        try:
            day_num = int(float(idx))
            found = _date_from_day_number(day_num, fixed_days)
            if found:
                return found
        except (TypeError, ValueError):
            pass
    day_idx = point_idx // 2
    if 0 <= day_idx < len(fixed_days):
        return fixed_days[day_idx]
    return "—"


def _day_key(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip().lower())


def _normalize_etat(raw: object) -> str | None:
    if raw is None:
        return None
    val = str(raw).strip()
    lower = val.lower()
    aliases = {
        "oui": "OUI",
        "non": "NON",
        "oui/non": "OUI/NON",
        "limite": "LIMITE",
    }
    if lower in aliases:
        return aliases[lower]
    upper = val.upper()
    if upper in _VALID_ETATS:
        return upper
    if upper == "LIMITE":
        return "LIMITE"
    return None


def _normalize_transition_type(raw: object) -> str | None:
    if raw is None:
        return None
    val = str(raw).strip().upper()
    return val if val in _VALID_TRANSITIONS else None


def _normalize_zone(raw: object) -> str:
    if raw is None:
        return ""
    val = str(raw).strip().lower()
    return val if val in _VALID_ZONES else ""


def _normalize_note(raw: object) -> int | None:
    if raw is None:
        return None
    try:
        note = int(raw)
    except (TypeError, ValueError):
        return None
    return note if 1 <= note <= 5 else None


def _transition_for_point(
    day_label: str,
    zone: str,
    transitions: list[BtcTransition],
) -> tuple[str, int | None]:
    key = _day_key(day_label)
    zone_norm = _normalize_zone(zone)
    for tr in transitions:
        if _day_key(tr.date) != key:
            continue
        if tr.zone and zone_norm and tr.zone != zone_norm:
            continue
        return tr.type, tr.note
    return "—", None


def _parse_transitions(
    raw_list: object,
    fixed_days: tuple[str, ...],
) -> list[BtcTransition]:
    if not isinstance(raw_list, list):
        return []
    out: list[BtcTransition] = []
    for idx, item in enumerate(raw_list):
        if not isinstance(item, dict):
            continue
        date_label = str(item.get("date") or "").strip()
        if not date_label:
            day_idx = item.get("index")
            if isinstance(day_idx, int) and 1 <= day_idx <= len(fixed_days):
                date_label = fixed_days[day_idx - 1]
            elif idx < len(fixed_days):
                date_label = fixed_days[idx]
        tr_type = _normalize_transition_type(item.get("type"))
        note = _normalize_note(item.get("note"))
        zone = _normalize_zone(item.get("zone"))
        if date_label and tr_type and note is not None:
            out.append(BtcTransition(date=date_label, zone=zone, type=tr_type, note=note))
    return out


def parse_regime_geometry_response(
    text: str,
    fixed_days: tuple[str, ...],
) -> tuple[list[BtcRegimeRow], dict]:
    """Parse la réponse Gemini — 1 entrée JSON = 1 ligne (00h + 12h par jour)."""
    payload = _extract_json_payload(text)
    points = _points_from_payload(payload)
    transitions = _parse_transitions(_transitions_from_payload(payload), fixed_days)

    legacy_jours = isinstance(payload, dict) and "jours" in payload
    n_dot5 = sum(
        1
        for p in points
        if p.get("index") is not None and abs(float(p["index"]) % 1 - 0.5) < 0.01
    )
    n_days = len(fixed_days) or 1
    diag = {
        "n_points_json": len(points),
        "n_days_expected": len(fixed_days),
        "n_points_expected": len(fixed_days) * 2,
        "n_dot5": n_dot5,
        "n_dot0": sum(
            1
            for p in points
            if p.get("index") is not None and abs(float(p["index"]) % 1) < 0.01
        ),
        "entries_per_day": round(len(points) / n_days, 2),
        "format_legacy_jours": legacy_jours,
        "prompt_issue": legacy_jours or (len(points) > 0 and n_dot5 == 0),
    }

    if not points:
        return [], diag

    sorted_points = sorted(points, key=_point_sort_key)

    rows: list[BtcRegimeRow] = []
    for point_idx, item in enumerate(sorted_points):
        date_label = _date_for_point(item, point_idx, fixed_days)
        etat = _normalize_etat(item.get("etat"))
        if etat is None:
            logger.warning("[Date ON/OFF] État invalide point %s : %r", point_idx, item)
            continue
        zone = _normalize_zone(item.get("zone"))
        if not zone:
            idx = item.get("index")
            if idx is not None:
                try:
                    zone = "midi" if abs(float(idx) % 1 - 0.5) < 0.01 else "debut"
                except (TypeError, ValueError):
                    zone = "debut" if point_idx % 2 == 0 else "midi"
            else:
                zone = "debut" if point_idx % 2 == 0 else "midi"
        transition, note = _transition_for_point(date_label, zone, transitions)
        point_index = None
        if item.get("index") is not None:
            try:
                point_index = float(item["index"])
            except (TypeError, ValueError):
                pass
        rows.append(
            BtcRegimeRow(
                date=date_label,
                etat=etat,
                zone=zone,
                transition=transition,
                transition_note=note,
                point_index=point_index,
            )
        )

    diag["n_rows_parsed"] = len(rows)
    logger.info("[Date ON/OFF] Diagnostic parsing : %s", diag)
    return rows, diag


def analyze_regime_dates(image_path: Path) -> tuple[list[BtcRegimeRow], str, dict]:
    """Analyse géométrique ligne cyan / bloc bleu — temperature=0."""
    fixed_days = compute_fixed_days()
    raw_text = ""
    diag: dict = {}
    try:
        symbol_tv = resolve_symbol_tv(BTC_TOKEN)
        context = AnalyzeContext(
            agent_id=BTC_AGENT_ID,
            symbol_key=BTC_TOKEN,
            symbol_tv=symbol_tv,
            timeframe_label="H4",
            layout_id=BTC_REGIME_LAYOUT_ID,
        )
        prompt = build_regime_prompt(fixed_days)
        vision = analyze_with_strategy(
            image_path,
            prompt,
            context=context,
            temperature=_REGIME_TEMPERATURE,
        )
        raw_text = vision.text or ""
        logger.info("[Date ON/OFF] Gemini brut (avant parsing) :\n%s", raw_text)
        print(f"[Date ON/OFF] Gemini brut (avant parsing):\n{raw_text}")
    except Exception as exc:
        logger.error("❌ Analyse géométrique Date ON/OFF échouée : %s", exc)
        return [], raw_text, diag

    if not raw_text:
        return [], raw_text, diag
    rows, diag = parse_regime_geometry_response(raw_text, fixed_days)
    return rows, raw_text, diag


def regime_row_to_db(row: BtcRegimeRow) -> dict:
    return {
        "date": row.date,
        "etat": row.etat,
        "zone": row.zone,
        "transition": row.transition,
        "transition_note": row.transition_note,
    }


def etat_badge(etat: str) -> tuple[str, str]:
    if etat == "OUI":
        return "OUI", "green"
    if etat in ("LIMITE", "OUI/NON"):
        return etat, "yellow"
    return "NON", "red"


def zone_display_label(zone: str) -> str:
    z = zone.strip().lower()
    return _ZONE_LABELS.get(z, zone or "—")


def ensure_row_dates(
    rows: list[dict],
    fixed_days: tuple[str, ...],
) -> list[dict]:
    """Garantit la clé ``date`` sur chaque ligne (index → jour figé côté code)."""
    total = len(rows)
    cleaned: list[dict] = []
    for i, row in enumerate(rows):
        out = {k: v for k, v in row.items() if k not in ("plage", "dateRange", "range", "date_range")}
        day_label = out.get("date")
        if not day_label:
            sort_order = out.get("sort_order", i)
            try:
                sort_idx = int(sort_order)
            except (TypeError, ValueError):
                sort_idx = i
            day_label = _date_for_sort_order(sort_idx, fixed_days, total)
        if not day_label and i < len(fixed_days):
            day_label = fixed_days[i]
        out["date"] = day_label or "—"
        cleaned.append(out)
    return cleaned


def _date_for_sort_order(
    sort_order: int,
    fixed_days: tuple[str, ...],
    total_rows: int,
) -> str | None:
    """Mappe sort_order → libellé jour (1 ligne/jour ou paires .0/.5)."""
    n_days = len(fixed_days)
    if n_days == 0:
        return None
    if total_rows == n_days and 0 <= sort_order < n_days:
        return fixed_days[sort_order]
    if total_rows == 2 * n_days:
        day_idx = sort_order // 2
        if 0 <= day_idx < n_days:
            return fixed_days[day_idx]
    if 0 <= sort_order < n_days:
        return fixed_days[sort_order]
    return None


def row_display(
    raw: dict,
    *,
    index: int | None = None,
    fixed_days: tuple[str, ...] | None = None,
    total_rows: int | None = None,
) -> dict:
    etat = str(raw.get("etat") or "—").upper()
    etat_label, etat_color = etat_badge(etat) if etat in _VALID_ETATS else (etat, "muted")
    transition = str(raw.get("transition") or "—")
    note = raw.get("transition_note")
    note_display = f"{note}/5" if note is not None and transition != "—" else "—"
    zone_raw = str(raw.get("zone") or "").strip().lower()
    zone_display = zone_display_label(zone_raw)
    sort_order = raw.get("sort_order", index)
    day_label = raw.get("date") or raw.get("date_range")
    if not day_label and fixed_days is not None:
        try:
            sort_idx = int(sort_order if sort_order is not None else index if index is not None else -1)
        except (TypeError, ValueError):
            sort_idx = index if index is not None else -1
        if sort_idx >= 0:
            total = total_rows if total_rows is not None else len(fixed_days)
            day_label = _date_for_sort_order(sort_idx, fixed_days, total)
        if not day_label and index is not None and index < len(fixed_days):
            day_label = fixed_days[index]
    return {
        "date": day_label or "—",
        "sort_order": sort_order,
        "etat": etat_label,
        "etat_color": etat_color,
        "zone": zone_display,
        "transition": transition,
        "transition_note": note,
        "note_display": note_display,
    }


def fetch_regime_table(conn) -> tuple[list[dict], dict[str, str | None], tuple[str, ...]]:
    meta = db_manager.fetch_btc_regime_meta(conn)
    fixed_days = compute_fixed_days()
    db_rows = db_manager.fetch_btc_regime_dates(conn)
    total = len(db_rows)
    display = [
        row_display(dict(r), index=idx, fixed_days=fixed_days, total_rows=total)
        for idx, r in enumerate(db_rows)
    ]
    return ensure_row_dates(display, fixed_days), meta, fixed_days


def run_regime_dates_update() -> dict:
    """Capture Ten Kan (layout épuré) + Gemini géométrique + persistance."""
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    try:
        png = capture_btc_h4_regime_chart()
        chart_path = str(png.resolve())
        logger.info(
            "[Date ON/OFF] Capture layout Ten Kan (%s) · %s jour(s) visibles : %s",
            BTC_REGIME_LAYOUT_ID,
            len(compute_fixed_days()),
            chart_path,
        )

        conn = db_manager.connect()
        try:
            db_manager.save_btc_regime_state(conn, chart_path=chart_path, last_error=None)
        finally:
            conn.close()

        parsed, gemini_raw, parse_diag = analyze_regime_dates(png)
        if not parsed:
            err = "Aucun point parsé depuis la réponse Gemini."
            conn = db_manager.connect()
            try:
                db_manager.save_btc_regime_state(conn, chart_path=chart_path, last_error=err)
            finally:
                conn.close()
            return {
                "ok": False,
                "error": err,
                "run_id": run_id,
                "chart_path": chart_path,
                "gemini_raw": gemini_raw,
                "parse_diag": parse_diag,
            }

        conn = db_manager.connect()
        try:
            n = db_manager.replace_btc_regime_dates(
                conn,
                run_id=run_id,
                chart_path=chart_path,
                rows=[regime_row_to_db(r) for r in parsed],
            )
            db_manager.save_btc_regime_state(conn, chart_path=chart_path, last_error=None)
        finally:
            conn.close()

        logger.info("✅ Tableau Date ON/OFF mis à jour — %s ligne(s), run=%s", n, run_id)
        return {
            "ok": True,
            "run_id": run_id,
            "n_rows": n,
            "chart_path": chart_path,
            "gemini_raw": gemini_raw,
            "parse_diag": parse_diag,
        }
    except Exception as exc:
        logger.error("❌ Mise à jour Date ON/OFF échouée : %s", exc)
        return {"ok": False, "error": str(exc), "run_id": run_id}
