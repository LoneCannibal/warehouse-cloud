from app.route import bin_location, distance, nearest_neighbour, plan_route, route_length, two_opt


def test_bin_codes_map_to_coordinates():
    assert bin_location("A-03-02") == (3, 2)
    assert bin_location("B-03-02") == (13, 2)


def test_distance_is_manhattan():
    assert distance((0, 0), (3, 4)) == 7


def test_two_opt_never_lengthens_a_route():
    stops = [(0, 0), (9, 9), (1, 1), (8, 8), (2, 2), (7, 7)]
    greedy = nearest_neighbour(stops)
    assert route_length(two_opt(greedy)) <= route_length(greedy)


def test_two_opt_removes_a_crossing():
    crossed = [(0, 0), (5, 5), (1, 1), (6, 6)]
    assert route_length(two_opt(crossed)) < route_length(crossed)


def test_route_visits_every_bin():
    bins = ["A-01-01", "B-02-03", "C-03-04"]
    route = plan_route(bins, extra_stops=10, seed="task")
    assert all(bin_location(code) in route for code in bins)
    assert len(route) == 1 + len(bins) + 10


def test_same_task_gives_same_route():
    assert plan_route(["A-01-01"], 20, "t1") == plan_route(["A-01-01"], 20, "t1")
