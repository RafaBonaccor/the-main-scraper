import math
import re
import unicodedata
from dataclasses import dataclass

from .models import ScrapeOutcome


@dataclass(frozen=True)
class GeoPlace:
    name: str
    lat: float
    lon: float
    area_type: str
    aliases: tuple[str, ...]


REFERENCE_PLACES: tuple[GeoPlace, ...] = (
    GeoPlace("Morlupo", 42.1497, 12.5036, "municipality", ("morlupo", "morlupo rm")),
    GeoPlace("Riano", 42.0937, 12.5210, "municipality", ("riano", "riano rm")),
    GeoPlace("Castelnuovo di Porto", 42.1246, 12.5012, "municipality", ("castelnuovo di porto", "castelnuovo di porto rm")),
    GeoPlace("Sacrofano", 42.1048, 12.4477, "municipality", ("sacrofano", "sacrofano rm")),
    GeoPlace("Formello", 42.0802, 12.3949, "municipality", ("formello", "formello rm")),
    GeoPlace("Campagnano di Roma", 42.1372, 12.3798, "municipality", ("campagnano di roma", "campagnano", "campagnano di roma rm")),
    GeoPlace("Capena", 42.1437, 12.5452, "municipality", ("capena", "capena rm")),
    GeoPlace("Fiano Romano", 42.1604, 12.5945, "municipality", ("fiano romano", "fiano", "fiano romano rm")),
    GeoPlace("Monterotondo", 42.0515, 12.6204, "municipality", ("monterotondo", "monterotondo rm")),
    GeoPlace("Cesano", 42.0728, 12.3308, "district", ("cesano", "cesano di roma")),
    GeoPlace("Olgiata", 42.0608, 12.3567, "district", ("olgiata",)),
    GeoPlace("La Storta", 42.0318, 12.3708, "district", ("la storta",)),
    GeoPlace("La Giustiniana", 42.0338, 12.4041, "district", ("la giustiniana", "giustiniana")),
    GeoPlace("Prima Porta", 42.0018, 12.4924, "district", ("prima porta",)),
    GeoPlace("Labaro", 42.0363, 12.4658, "district", ("labaro",)),
    GeoPlace("Saxa Rubra", 42.0238, 12.4818, "district", ("saxa rubra",)),
    GeoPlace("Grottarossa", 42.0116, 12.4730, "district", ("grottarossa", "grotta rossa")),
    GeoPlace("Due Ponti", 42.0025, 12.4780, "district", ("due ponti",)),
    GeoPlace("Tomba di Nerone", 42.0233, 12.4395, "district", ("tomba di nerone",)),
    GeoPlace("Cassia", 42.0135, 12.4310, "district", ("cassia", "via cassia")),
    GeoPlace("Fleming", 41.9685, 12.4635, "district", ("fleming",)),
    GeoPlace("Vigna Clara", 41.9708, 12.4526, "district", ("vigna clara",)),
    GeoPlace("Tor di Quinto", 41.9537, 12.4872, "district", ("tor di quinto",)),
    GeoPlace("Ponte Milvio", 41.9664, 12.4669, "district", ("ponte milvio",)),
    GeoPlace("Monte Mario", 41.9398, 12.4319, "district", ("monte mario",)),
    GeoPlace("Balduina", 41.9200, 12.4379, "district", ("balduina",)),
    GeoPlace("Trionfale", 41.9313, 12.4386, "district", ("trionfale",)),
    GeoPlace("Cortina d'Ampezzo", 41.9566, 12.4305, "district", ("cortina d ampezzo", "cortina dampezzo")),
)

DECISION_ORDER = {
    "accepted": 0,
    "maybe": 1,
    "rejected": 2,
}

GENERIC_ROMA_MARKERS = {
    "roma",
    "roma rm",
}

_ALIAS_INDEX: dict[str, GeoPlace] = {}
for place in REFERENCE_PLACES:
    for alias in place.aliases:
        _ALIAS_INDEX[alias] = place


def apply_geo_sorting_to_outcome(
    outcome: ScrapeOutcome,
    anchor_place: str = "Morlupo",
    max_distance_km: float = 30.0,
    nearby_only: bool = False,
) -> ScrapeOutcome:
    anchor = _resolve_known_place(anchor_place)
    if anchor is None:
        raise ValueError(f"Unknown anchor place: {anchor_place}")

    annotated_rows: list[dict] = []
    counts = {"accepted": 0, "maybe": 0, "rejected": 0}

    for row in outcome.rows:
        annotated = _annotate_row(row, anchor=anchor, max_distance_km=max_distance_km)
        counts[annotated["geo_decision"]] += 1
        annotated_rows.append(annotated)

    annotated_rows.sort(key=_sorting_key)
    if nearby_only:
        annotated_rows = [row for row in annotated_rows if row.get("geo_decision") == "accepted"]

    meta = dict(outcome.meta)
    meta.update(
        {
            "geo_anchor_place": anchor.name,
            "geo_anchor_lat": anchor.lat,
            "geo_anchor_lon": anchor.lon,
            "geo_max_distance_km": max_distance_km,
            "geo_nearby_only": nearby_only,
            "geo_counts": counts,
            "row_count": len(annotated_rows),
        }
    )

    return ScrapeOutcome(source=outcome.source, rows=annotated_rows, meta=meta)


def _annotate_row(row: dict, anchor: GeoPlace, max_distance_km: float) -> dict:
    annotated = dict(row)
    location = str(row.get("location", "") or "")
    title = str(row.get("title", row.get("name", "")) or "")
    raw_text = str(row.get("raw_text", "") or "")

    resolved_place = _resolve_place_from_location(location)
    geo_source = "location" if resolved_place else ""
    confidence = "high" if resolved_place else "low"

    if resolved_place is None:
        text_place = _resolve_place_from_text(" ".join([location, title, raw_text]))
        if text_place is not None:
            resolved_place = text_place
            geo_source = "text"
            confidence = "medium"

    distance_km = None
    decision = "maybe"
    reason = "Location too generic to classify."

    if resolved_place is not None:
        distance_km = round(_haversine_km(anchor.lat, anchor.lon, resolved_place.lat, resolved_place.lon), 1)
        if distance_km <= max_distance_km:
            decision = "accepted"
            reason = f"{resolved_place.name} is within {max_distance_km:.0f} km of {anchor.name}."
        else:
            decision = "rejected"
            reason = f"{resolved_place.name} is farther than {max_distance_km:.0f} km from {anchor.name}."
    elif _is_generic_roma(location):
        decision = "maybe"
        reason = "Generic Roma location without a recognized north-side area."
    elif location.strip():
        decision = "maybe"
        reason = "Location found but not mapped to a known reference area yet."

    annotated.update(
        {
            "geo_anchor_place": anchor.name,
            "geo_max_distance_km": max_distance_km,
            "resolved_place": resolved_place.name if resolved_place else "",
            "resolved_area_type": resolved_place.area_type if resolved_place else "",
            "distance_km": distance_km,
            "geo_confidence": confidence,
            "geo_source": geo_source,
            "geo_decision": decision,
            "geo_decision_reason": reason,
        }
    )
    return annotated


def _sorting_key(row: dict) -> tuple:
    decision = row.get("geo_decision", "maybe")
    distance = row.get("distance_km")
    return (
        DECISION_ORDER.get(decision, 99),
        distance is None,
        distance if distance is not None else 9999,
        str(row.get("title", row.get("name", ""))).lower(),
    )


def _resolve_known_place(value: str) -> GeoPlace | None:
    normalized = _normalize_geo_text(value)
    if not normalized:
        return None
    return _ALIAS_INDEX.get(normalized)


def _resolve_place_from_location(value: str) -> GeoPlace | None:
    normalized = _normalize_geo_text(value)
    if not normalized:
        return None

    direct = _ALIAS_INDEX.get(normalized)
    if direct is not None:
        return direct

    for alias, place in _ALIAS_INDEX.items():
        if normalized == alias:
            return place
        if normalized.startswith(alias + " ") or normalized.endswith(" " + alias):
            return place

    return None


def _resolve_place_from_text(value: str) -> GeoPlace | None:
    normalized = _normalize_geo_text(value)
    if not normalized:
        return None

    for alias, place in sorted(_ALIAS_INDEX.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(^|\s){re.escape(alias)}($|\s)", normalized):
            return place
    return None


def _is_generic_roma(value: str) -> bool:
    normalized = _normalize_geo_text(value)
    return normalized in GENERIC_ROMA_MARKERS


def _normalize_geo_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    ascii_value = re.sub(r"[^a-z0-9]+", " ", ascii_value)
    return " ".join(ascii_value.split())


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_km = 6371.0
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return earth_radius_km * c
