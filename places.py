"""Place names for photo locations, worked out on this computer: the nearest town or city from a small offline list
(data/cities.tsv.gz: GeoNames cities with 5,000+ people, https://www.geonames.org, CC BY 4.0). A photo's location is
never sent anywhere to name it."""
from __future__ import annotations

import gzip
import math
import sys
import threading
from pathlib import Path

DATA = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "data" / "cities.tsv.gz"  # ships with Ken
AREA_KM = 20  # places this close compete on size against distance
FAR_KM = 80  # further than this from any listed town: "near <town>", past 300 km just the country if known, else nothing

_grid: dict[tuple[int, int], list[tuple[float, float, str, str]]] | None = None
_countries: dict[str, str] = {}
_lock = threading.Lock()
_cache: dict[tuple[float, float], str] = {}


def _load() -> dict:
    global _grid
    with _lock:
        if _grid is None:
            grid: dict[tuple[int, int], list] = {}
            try:
                with gzip.open(DATA, "rt", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("#") or not line.strip():
                            continue
                        p = line.rstrip("\n").split("\t")
                        if p[0] == "C":
                            _countries[p[1]] = p[2]
                            continue
                        lat, lon = float(p[0]), float(p[1])
                        pop = int(p[4]) if len(p) > 4 and p[4].isdigit() else 0
                        grid.setdefault((math.floor(lat), math.floor(lon)), []).append((lat, lon, p[2], p[3], pop))
            except OSError:
                pass
            _grid = grid
    return _grid


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return 6371 * math.hypot(x, math.radians(lat2 - lat1))


def place_name(lat: float | None, lon: float | None) -> str:
    """"Lisbon, Portugal" for a photo's coordinates; "" when there are none or nothing is close."""
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return ""
    key = (round(lat, 2), round(lon, 2))  # about a kilometre: a whole trip needs only a few lookups
    if key in _cache:
        return _cache[key]
    grid = _load()
    near: list[tuple[float, tuple]] = []  # (km, place) for every listed place within reach
    for ring in range(0, 4):  # widen until something is found (each ring is about 100 km)
        cy, cx = math.floor(lat), math.floor(lon)
        for dy in range(-ring, ring + 1):
            for dx in range(-ring, ring + 1):
                if max(abs(dy), abs(dx)) == ring:
                    near += [(_km(lat, lon, c[0], c[1]), c) for c in grid.get((cy + dy, (cx + dx + 180) % 360 - 180), ())]
        if near and min(near)[0] <= ring * 100 + 50:
            break
    best, best_km = None, float("inf")
    if near:
        best_km, best = min(near, key=lambda n: n[0])
        close = [n for n in near if n[0] <= AREA_KM]
        if close:  # the place that's both big and close: "Tokyo", not the district the photo was in
            best_km, best = max(close, key=lambda n: (n[1][4] + 1) / (1 + n[0]) ** 2)
    name = ""
    if best and best_km <= 300:
        country = _countries.get(best[3], best[3])
        name = f"{'near ' if best_km > FAR_KM else ''}{best[2]}, {country}"
    _cache[key] = name
    return name
