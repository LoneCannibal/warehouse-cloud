"""Pick-route planning: a small travelling-salesman heuristic over bin locations.

The route is real work rather than a sleep. An HPA scales on CPU, and a sleeping
worker uses none, so simulated picking would never trigger a scale-out.
"""
from __future__ import annotations

import random

AISLES = 40
BAYS = 60


def bin_location(bin_code: str) -> tuple[int, int]:
    """Maps a bin code such as A-03-02 onto aisle and bay coordinates."""
    zone, aisle, bay = bin_code.split("-")
    return (ord(zone[0]) - ord("A")) * 10 + int(aisle), int(bay)


def distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def route_length(route: list[tuple[int, int]]) -> int:
    return sum(distance(route[i], route[i + 1]) for i in range(len(route) - 1))


def nearest_neighbour(stops: list[tuple[int, int]]) -> list[tuple[int, int]]:
    remaining = stops[1:]
    route = [stops[0]]
    while remaining:
        closest = min(remaining, key=lambda stop: distance(route[-1], stop))
        remaining.remove(closest)
        route.append(closest)
    return route


def two_opt(route: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Reverses route segments while doing so shortens the route."""
    route = list(route)
    improved = True
    while improved:
        improved = False
        for i in range(1, len(route) - 2):
            for j in range(i + 1, len(route) - 1):
                before = distance(route[i - 1], route[i]) + distance(route[j], route[j + 1])
                after = distance(route[i - 1], route[j]) + distance(route[i], route[j + 1])
                if after < before:
                    route[i : j + 1] = reversed(route[i : j + 1])
                    improved = True
    return route


def plan_route(bins: list[str], extra_stops: int, seed: str) -> list[tuple[int, int]]:
    """Plans a tour of the order's bins plus extra aisle waypoints.

    extra_stops sets the CPU cost per task. The waypoints stand in for the other
    picks a real worker would batch into the same walk.
    """
    rng = random.Random(seed)
    waypoints = [(rng.randrange(AISLES), rng.randrange(BAYS)) for _ in range(extra_stops)]
    stops = [(0, 0)] + [bin_location(code) for code in bins] + waypoints
    return two_opt(nearest_neighbour(stops))
