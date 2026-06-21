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
    DAYS_WINDOW,
    MAX_DAYS,
    MIN_DAYS,
    _clamp_days,
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
_H4_SLOT_HOURS = (0, 4, 8, 12, 16, 20)
H4_SLOTS: tuple[str, ...] = tuple(f"{h:02d}H00" for h in _H4_SLOT_HOURS)
_SLOTS_PER_DAY = len(H4_SLOTS)
_REGIME_TEMPERATURE = 0.0

_GEOMETRY_PROMPT_BODY = """Agis comme un expert en analyse visuelle et en reconnaissance de formes.
Je vais te donner une image contenant un graphique temporel H4 découpé par des lignes
verticales grises. Chaque espace entre deux lignes verticales représente un jour
(la date est écrite en bas).

Ta mission : pour CHAQUE jour visible (colonne verticale), lire l'état du nuage à
CHACUN des 6 créneaux horaires H4 suivants, de haut en bas dans la colonne :
00H00 · 04H00 · 08H00 · 12H00 · 16H00 · 20H00.

Consignes strictes :
1. Ignore le contexte financier. Raisonne uniquement sur les formes et les couleurs.
2. Pour chaque créneau, regarde s'il y a un SEUL nuage (Bleu uniquement) ou DEUX
   nuages visibles (Vert ET Bleu superposés ou distincts dans ce créneau).
3. Verdict binaire par créneau :
   - « OUI » = un SEUL nuage (bleu seul visible).
   - « NON » = DEUX nuages (vert ET bleu visibles dans ce créneau).
4. Parcours colonne par colonne (gauche → droite), et dans chaque colonne les 6
   créneaux dans l'ordre 00H00 → 04H00 → 08H00 → 12H00 → 16H00 → 20H00.

⚠️ JOUR EN COURS (UTC) — règle impérative :
{today_rule}
- Ne renvoie JAMAIS un créneau dont l'heure de début est dans le futur.
- Pour les jours passés : les 6 créneaux. Pour aujourd'hui : uniquement ceux listés ci-dessous.

Jours attendus sur le chart (ordre chronologique, gauche → droite) :
{jours_list}

Créneaux à renvoyer ({n_points} lignes — créneaux déjà commencés, UTC) :
{creneaux_list}

⚠️ ANTI-PARESSE :
- Ne réponds pas « OUI » ou « NON » par défaut sur tous les créneaux.
- Juge chaque créneau séparément — il est normal d'avoir des OUI et des NON mélangés.
- Il est interdit de copier-coller la même observation sur toutes les lignes.

FORMAT DE SORTIE — JSON strict uniquement (pas de markdown, pas de texte libre) :
{{
  "creneaux": [
    {{
      "index": 1,
      "date": "{example_day_label}",
      "heure": "00H00",
      "observation": "bleu seul visible",
      "etat": "oui"
    }},
    {{
      "index": 2,
      "date": "{example_day_label}",
      "heure": "04H00",
      "observation": "vert et bleu visibles",
      "etat": "non"
    }}
  ]
}}

CONTRAINTES
- Exactement {n_points} entrées dans « creneaux » (liste ci-dessus, sans créneau futur).
- Chaque entrée DOIT avoir : index (entier 1…{n_points}), date, heure, etat (« oui » ou « non »).
- date au format « 14-juin » (jour-mois, mois en minuscules, sans année).
- heure EXACTEMENT l'une de : 00H00, 04H00, 08H00, 12H00, 16H00, 20H00 (H majuscule).
- Retourne UNIQUEMENT le JSON {{ "creneaux": [...] }}."""


@dataclass(frozen=True)
class BtcTransition:
    date: str
    heure: str
    type: str
    note: int


@dataclass(frozen=True)
class BtcRegimeRow:
    date: str
    heure: str
    etat: str
    transition: str
    transition_note: int | None
    point_index: float | None = None


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


def format_day_label(d: date) -> str:
    """Libellé « 14-juin » (format DATE ZONE du tableau)."""
    return f"{d.day}-{_FRENCH_MONTHS[d.month]}"


def normalize_date_label(raw: object) -> str:
    """Normalise « 14 juin » ou « 14-juin » → « 14-juin »."""
    s = str(raw or "").strip()
    m = re.match(r"^(\d{1,2})[\s\-]+(.+)$", s, re.I)
    if not m:
        return s
    return f"{int(m.group(1))}-{m.group(2).strip().lower()}"


def normalize_heure(raw: object) -> str | None:
    """Normalise vers 00H00, 04H00, … (H majuscule, 2 chiffres)."""
    if raw is None:
        return None
    s = re.sub(r"\s+", "", str(raw).strip().upper())
    m = re.match(r"^(\d{1,2})H(\d{2})$", s)
    if m:
        candidate = f"{int(m.group(1)):02d}H{m.group(2)}"
        return candidate if candidate in H4_SLOTS else None
    m = re.match(r"^(\d{1,2})H?$", s)
    if m:
        candidate = f"{int(m.group(1)):02d}H00"
        return candidate if candidate in H4_SLOTS else None
    return None


def _h4_floor(now: datetime) -> datetime:
    """Début du créneau H4 UTC en cours (00 / 04 / 08 / 12 / 16 / 20)."""
    hour = (now.hour // 4) * 4
    return now.replace(hour=hour, minute=0, second=0, microsecond=0)


def _date_label_to_date(label: str, *, ref: date | None = None) -> date | None:
    """Convertit « 14-juin » → date (année = ref.year)."""
    ref = ref or datetime.now(timezone.utc).date()
    m = re.match(r"^(\d{1,2})[\s\-]+(.+)$", label.strip(), re.I)
    if not m:
        return None
    day_num = int(m.group(1))
    month_key = m.group(2).strip().lower()
    for month_idx, name in enumerate(_FRENCH_MONTHS):
        if name and name == month_key:
            try:
                return date(ref.year, month_idx, day_num)
            except ValueError:
                return None
    return None


def is_slot_persistable(date_label: str, heure: str, now: datetime | None = None) -> bool:
    """True si le créneau H4 a déjà commencé (UTC) — exclut 16H00/20H00 avant l'heure."""
    now = now or datetime.now(timezone.utc)
    heure_norm = normalize_heure(heure)
    if heure_norm is None:
        return False
    day = _date_label_to_date(normalize_date_label(date_label), ref=now.date())
    if day is None:
        return True
    today = now.date()
    if day > today:
        return False
    if day < today:
        return True
    slot_hour = int(heure_norm[:2])
    slot_start = datetime(
        today.year, today.month, today.day, slot_hour, tzinfo=timezone.utc
    )
    return slot_start <= _h4_floor(now)


def compute_elapsed_slots(
    fixed_days: tuple[str, ...],
    now: datetime | None = None,
) -> tuple[tuple[str, str], ...]:
    """Créneaux (date, heure) éligibles — jours passés complets, jour courant partiel."""
    now = now or datetime.now(timezone.utc)
    out: list[tuple[str, str]] = []
    for day in fixed_days:
        for heure in H4_SLOTS:
            if is_slot_persistable(day, heure, now):
                out.append((day, heure))
    return tuple(out)


def filter_persistable_rows(
    rows: list[BtcRegimeRow],
    now: datetime | None = None,
) -> tuple[list[BtcRegimeRow], int]:
    """Retire les créneaux futurs (jour courant ou jours à venir)."""
    now = now or datetime.now(timezone.utc)
    kept: list[BtcRegimeRow] = []
    skipped = 0
    for row in rows:
        if is_slot_persistable(row.date, row.heure, now):
            kept.append(row)
        else:
            skipped += 1
    return kept, skipped


def resolve_days_window(raw: object | None = None) -> int:
    """Normalise days_window UI → entier clampé [MIN_DAYS, MAX_DAYS], défaut DAYS_WINDOW."""
    if raw is None:
        return DAYS_WINDOW
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DAYS_WINDOW
    return _clamp_days(value)


def compute_fixed_days(days_window: int = DAYS_WINDOW) -> tuple[str, ...]:
    """Liste figée des jours visibles (1er → dernier jour à l'écran)."""
    start_iso, end_iso = compute_date_window(days_window)
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    days: list[str] = []
    cur = start
    while cur <= end:
        days.append(format_day_label(cur))
        cur += timedelta(days=1)
    return tuple(days)


def compute_fixed_slots(days_window: int = DAYS_WINDOW) -> tuple[tuple[str, str], ...]:
    """Paires (date, heure) attendues — 6 créneaux H4 par jour (sans filtre temps)."""
    return tuple(
        (day, heure)
        for day in compute_fixed_days(days_window)
        for heure in H4_SLOTS
    )


def build_regime_prompt(
    fixed_days: tuple[str, ...],
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    today_label = format_day_label(now.date())
    floor = _h4_floor(now)
    floor_heure = f"{floor.hour:02d}H00"
    elapsed = compute_elapsed_slots(fixed_days, now)
    jours_list = "\n".join(f"- {d}" for d in fixed_days)
    example_day_label = fixed_days[0] if fixed_days else "14-juin"
    creneaux_list = "\n".join(f"- {d} · {h}" for d, h in elapsed)
    n_days = len(fixed_days)
    n_points = len(elapsed)
    if today_label in fixed_days:
        today_rule = (
            f"- Aujourd'hui = {today_label} · créneau H4 UTC en cours = {floor_heure}.\n"
            f"- Pour {today_label} : uniquement les créneaux ≤ {floor_heure} "
            f"(pas de 16H00 / 20H00 tant qu'ils ne sont pas commencés)."
        )
    else:
        today_rule = "- Aucun jour « aujourd'hui » dans la fenêtre — tous les créneaux listés sont passés."
    return _GEOMETRY_PROMPT_BODY.format(
        jours_list=jours_list,
        creneaux_list=creneaux_list,
        example_day_label=example_day_label,
        n_days=n_days,
        n_points=n_points,
        today_rule=today_rule,
    )


def _day_number_from_label(label: str) -> int | None:
    m = re.match(r"^(\d{1,2})[\s\-]?", label.strip())
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
        for key in ("creneaux", "jours", "points"):
            val = payload.get(key)
            if isinstance(val, list):
                return [p for p in val if isinstance(p, dict)]
    return []


def _parse_markdown_table(text: str) -> list[dict]:
    """Extrait DATE ZONE / Heure / ÉTAT depuis un tableau markdown Gemini."""
    rows: list[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if re.match(r"^\|[\s\-:|]+\|$", stripped):
            continue
        parts = [p.strip() for p in stripped.strip("|").split("|")]
        if len(parts) < 3:
            continue
        date_col, heure_col, etat_col = parts[0], parts[1], parts[2]
        header = date_col.lower()
        if header in ("date", "dates", "date zone"):
            continue
        if etat_col.lower() in ("état", "etat", "réponse", "reponse"):
            continue
        etat = _normalize_etat(etat_col)
        heure = normalize_heure(heure_col)
        if etat is None or not date_col or heure is None:
            continue
        rows.append(
            {
                "date": normalize_date_label(date_col),
                "heure": heure,
                "etat": etat.lower(),
            }
        )
    return rows


def _infer_transitions_from_sequence(
    sequence: list[tuple[str, str, str]],
) -> list[BtcTransition]:
    """Infère ENTREE/SORTIE entre créneaux consécutifs."""
    out: list[BtcTransition] = []
    for i in range(1, len(sequence)):
        prev_date, prev_heure, prev_etat = sequence[i - 1]
        date_label, heure, etat = sequence[i]
        if prev_etat == "OUI" and etat == "NON":
            out.append(
                BtcTransition(date=date_label, heure=heure or prev_heure, type="SORTIE", note=3)
            )
        elif prev_etat == "NON" and etat == "OUI":
            out.append(
                BtcTransition(date=date_label, heure=heure or prev_heure, type="ENTREE", note=3)
            )
    return out


def _transitions_from_payload(payload: dict | list | None) -> object:
    if isinstance(payload, dict):
        return payload.get("transitions")
    return None


def _slot_index(heure: str) -> int:
    try:
        return H4_SLOTS.index(heure)
    except ValueError:
        return 0


def _point_sort_key(item: dict) -> float:
    idx = item.get("index")
    if idx is not None:
        try:
            return float(idx)
        except (TypeError, ValueError):
            pass
    heure = normalize_heure(item.get("heure"))
    if heure:
        return float(_slot_index(heure))
    return 0.0


def _date_from_day_number(day_num: int, fixed_days: tuple[str, ...]) -> str | None:
    for label in fixed_days:
        n = _day_number_from_label(label)
        if n == day_num:
            return label
    return None


def _heure_for_point(
    item: dict,
    point_idx: int,
    fixed_days: tuple[str, ...],
) -> str:
    heure = normalize_heure(item.get("heure"))
    if heure:
        return heure
    idx = item.get("index")
    if idx is not None:
        try:
            slot_idx = (int(float(idx)) - 1) % _SLOTS_PER_DAY
            if 0 <= slot_idx < _SLOTS_PER_DAY:
                return H4_SLOTS[slot_idx]
        except (TypeError, ValueError):
            pass
    slot_idx = point_idx % _SLOTS_PER_DAY
    return H4_SLOTS[slot_idx]


def _date_for_point(
    item: dict,
    point_idx: int,
    fixed_days: tuple[str, ...],
) -> str:
    date_label = normalize_date_label(item.get("date") or "")
    if date_label and date_label != "—":
        return date_label
    idx = item.get("index")
    if idx is not None:
        try:
            linear = int(float(idx)) - 1
            day_idx = linear // _SLOTS_PER_DAY
            if 0 <= day_idx < len(fixed_days):
                return fixed_days[day_idx]
            day_num = int(float(idx))
            found = _date_from_day_number(day_num, fixed_days)
            if found:
                return found
        except (TypeError, ValueError):
            pass
    day_idx = point_idx // _SLOTS_PER_DAY
    if 0 <= day_idx < len(fixed_days):
        return fixed_days[day_idx]
    return "—"


def _day_key(label: str) -> str:
    return normalize_date_label(label).lower()


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
    heure: str,
    transitions: list[BtcTransition],
) -> tuple[str, int | None]:
    key = _day_key(day_label)
    for tr in transitions:
        if _day_key(tr.date) != key:
            continue
        if tr.heure and heure and tr.heure != heure:
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
        date_label = normalize_date_label(item.get("date") or "")
        if not date_label:
            day_idx = item.get("index")
            if isinstance(day_idx, int) and 1 <= day_idx <= len(fixed_days):
                date_label = fixed_days[day_idx - 1]
            elif idx < len(fixed_days):
                date_label = fixed_days[idx]
        tr_type = _normalize_transition_type(item.get("type"))
        note = _normalize_note(item.get("note"))
        heure = normalize_heure(item.get("heure")) or "00H00"
        if date_label and tr_type and note is not None:
            out.append(BtcTransition(date=date_label, heure=heure, type=tr_type, note=note))
    return out


def parse_regime_geometry_response(
    text: str,
    fixed_days: tuple[str, ...],
    *,
    now: datetime | None = None,
) -> tuple[list[BtcRegimeRow], dict]:
    """Parse la réponse Gemini — 1 entrée = 1 créneau H4 (date + heure)."""
    now = now or datetime.now(timezone.utc)
    payload = _extract_json_payload(text)
    points = _points_from_payload(payload)
    if not points:
        points = _parse_markdown_table(text)
    transitions = _parse_transitions(_transitions_from_payload(payload), fixed_days)

    n_days = len(fixed_days) or 1
    n_expected = len(compute_elapsed_slots(fixed_days, now))
    observations = [str(p.get("observation") or "").strip().lower() for p in points if p.get("observation")]
    etats_raw = [_normalize_etat(p.get("etat")) for p in points]
    unique_obs = set(o for o in observations if o)
    unique_etats = set(e for e in etats_raw if e)
    diag = {
        "n_points_json": len(points),
        "n_days_expected": n_days,
        "n_points_expected": n_expected,
        "entries_per_day": round(len(points) / n_days, 2),
        "format_legacy_jours": isinstance(payload, dict) and "jours" in payload,
        "prompt_issue": len(points) > 0 and len(points) < n_expected,
        "lazy_all_same_etat": len(points) > 2 and len(unique_etats) == 1,
        "lazy_all_same_observation": len(observations) > 2 and len(unique_obs) == 1,
    }

    if not points:
        return [], diag

    sorted_points = sorted(points, key=_point_sort_key)

    parsed_sequence: list[tuple[str, str, str]] = []
    rows: list[BtcRegimeRow] = []
    for point_idx, item in enumerate(sorted_points):
        date_label = _date_for_point(item, point_idx, fixed_days)
        heure = _heure_for_point(item, point_idx, fixed_days)
        etat = _normalize_etat(item.get("etat"))
        if etat is None:
            logger.warning("[Date ON/OFF] État invalide point %s : %r", point_idx, item)
            continue
        parsed_sequence.append((date_label, heure, etat))
        point_index = None
        if item.get("index") is not None:
            try:
                point_index = float(item["index"])
            except (TypeError, ValueError):
                pass
        rows.append(
            BtcRegimeRow(
                date=normalize_date_label(date_label),
                heure=heure,
                etat=etat,
                transition="—",
                transition_note=None,
                point_index=point_index,
            )
        )

    if not transitions and parsed_sequence:
        transitions = _infer_transitions_from_sequence(parsed_sequence)

    final_rows: list[BtcRegimeRow] = []
    for row in rows:
        transition, note = _transition_for_point(row.date, row.heure, transitions)
        final_rows.append(
            BtcRegimeRow(
                date=row.date,
                heure=row.heure,
                etat=row.etat,
                transition=transition,
                transition_note=note,
                point_index=row.point_index,
            )
        )
    rows = final_rows

    diag["n_rows_parsed"] = len(rows)
    diag["format_markdown_table"] = not bool(_points_from_payload(payload)) and bool(points)
    logger.info("[Date ON/OFF] Diagnostic parsing : %s", diag)
    return rows, diag


def analyze_regime_dates(
    image_path: Path,
    *,
    days_window: int = DAYS_WINDOW,
) -> tuple[list[BtcRegimeRow], str, dict]:
    """Analyse visuelle nuage bleu seul vs vert+bleu — temperature=0."""
    now = datetime.now(timezone.utc)
    fixed_days = compute_fixed_days(days_window)
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
        prompt = build_regime_prompt(fixed_days, now)
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
    rows, diag = parse_regime_geometry_response(raw_text, fixed_days, now=now)
    rows, n_skipped = filter_persistable_rows(rows, now)
    diag["n_skipped_future"] = n_skipped
    diag["n_rows_persistable"] = len(rows)
    return rows, raw_text, diag


def _day_sort_order(label: str, *, ref: date | None = None) -> int:
    """Clé de tri chronologique pour libellé « 14-juin » (année = ref.year)."""
    ref = ref or datetime.now(timezone.utc).date()
    m = re.match(r"^(\d{1,2})[\s\-]+(.+)$", label.strip(), re.I)
    if not m:
        return 0
    day_num = int(m.group(1))
    month_key = m.group(2).strip().lower()
    for month_idx, name in enumerate(_FRENCH_MONTHS):
        if name and name == month_key:
            try:
                return date(ref.year, month_idx, day_num).toordinal()
            except ValueError:
                return 0
    return 0


def _slot_sort_order(date_label: str, heure: str, *, ref: date | None = None) -> int:
    return _day_sort_order(date_label, ref=ref) * _SLOTS_PER_DAY + _slot_index(heure)


def regime_row_to_db(row: BtcRegimeRow) -> dict:
    return {
        "date": normalize_date_label(row.date),
        "heure": row.heure,
        "etat": row.etat,
        "zone": row.heure,
        "transition": row.transition,
        "transition_note": row.transition_note,
        "sort_order": _slot_sort_order(row.date, row.heure),
    }


def etat_badge(etat: str) -> tuple[str, str]:
    if etat == "OUI":
        return "OUI", "green"
    if etat in ("LIMITE", "OUI/NON"):
        return etat, "yellow"
    return "NON", "red"


def _date_for_sort_order(
    sort_order: int,
    fixed_days: tuple[str, ...],
    total_rows: int,
) -> str | None:
    """Mappe sort_order → libellé jour (6 créneaux/jour)."""
    n_days = len(fixed_days)
    if n_days == 0:
        return None
    day_idx = sort_order // _SLOTS_PER_DAY
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
    heure = normalize_heure(raw.get("heure")) or normalize_heure(raw.get("zone")) or "—"
    sort_order = raw.get("sort_order", index)
    day_label = raw.get("date") or raw.get("date_range")
    if day_label:
        day_label = normalize_date_label(day_label)
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
        "heure": heure,
        "sort_order": sort_order,
        "etat": etat_label,
        "etat_color": etat_color,
    }


def fetch_regime_table(conn) -> tuple[list[dict], dict[str, str | None], tuple[str, ...]]:
    meta = db_manager.fetch_btc_regime_meta(conn)
    db_rows = db_manager.fetch_btc_regime_dates(conn)
    now = datetime.now(timezone.utc)
    visible_rows = [
        r for r in db_rows
        if is_slot_persistable(str(r["date_range"]), str(r["heure"]), now)
    ]
    total = len(visible_rows)
    accumulated_days = tuple(
        dict.fromkeys(normalize_date_label(str(r["date_range"])) for r in visible_rows)
    )
    display = [
        row_display(dict(r), index=idx, fixed_days=accumulated_days, total_rows=total)
        for idx, r in enumerate(visible_rows)
    ]
    return display, meta, accumulated_days


def run_regime_dates_update(*, days_window: int = DAYS_WINDOW) -> dict:
    """Capture Ten Kan (layout épuré) + Gemini géométrique + persistance."""
    effective = resolve_days_window(days_window)
    fixed_days = compute_fixed_days(effective)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    try:
        png = capture_btc_h4_regime_chart(days_window=effective)
        chart_path = str(png.resolve())
        logger.info(
            "[Date ON/OFF] Capture layout Ten Kan (%s) · %s jour(s) · days_window=%s · %s",
            BTC_REGIME_LAYOUT_ID,
            len(fixed_days),
            effective,
            chart_path,
        )

        conn = db_manager.connect()
        try:
            db_manager.save_btc_regime_state(conn, chart_path=chart_path, last_error=None)
        finally:
            conn.close()

        parsed, gemini_raw, parse_diag = analyze_regime_dates(png, days_window=effective)
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
                "days_window": effective,
            }

        conn = db_manager.connect()
        try:
            db_manager.prune_future_btc_regime_slots(conn)
            n_inserted, n_total = db_manager.upsert_btc_regime_dates(
                conn,
                run_id=run_id,
                chart_path=chart_path,
                rows=[regime_row_to_db(r) for r in parsed],
            )
            db_manager.save_btc_regime_state(conn, chart_path=chart_path, last_error=None)
        finally:
            conn.close()

        logger.info(
            "✅ Date ON/OFF insert — %s nouveau(x) créneau(x), %s au total, run=%s",
            n_inserted,
            n_total,
            run_id,
        )
        return {
            "ok": True,
            "run_id": run_id,
            "n_rows": n_total,
            "n_upserted": n_inserted,
            "n_inserted": n_inserted,
            "chart_path": chart_path,
            "gemini_raw": gemini_raw,
            "parse_diag": parse_diag,
            "days_window": effective,
        }
    except Exception as exc:
        logger.error("❌ Mise à jour Date ON/OFF échouée : %s", exc)
        return {"ok": False, "error": str(exc), "run_id": run_id, "days_window": effective}
