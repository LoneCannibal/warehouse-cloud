from app.manifest import build_manifest, choose_box, manifest_key, package_id


def test_box_grows_with_the_order():
    assert choose_box(1) == "SMALL"
    assert choose_box(5) == "SMALL"
    assert choose_box(6) == "MEDIUM"
    assert choose_box(60) == "LARGE"
    assert choose_box(61) == "PALLET"


def test_package_id_is_stable_across_attempts():
    # A reclaimed task must overwrite the same object, or Dispatch would ship twice.
    first = build_manifest("o1", [{"quantity": 1}], "worker-a", attempt=1)
    second = build_manifest("o1", [{"quantity": 1}], "worker-b", attempt=2)
    assert first["package_id"] == second["package_id"] == package_id("o1")


def test_manifest_totals():
    manifest = build_manifest("o1", [{"quantity": 2}, {"quantity": 3}], "worker", attempt=1)
    assert manifest["units"] == 5
    assert manifest["weight_kg"] == 2.0


def test_key_sits_under_the_prefix_that_triggers_dispatch():
    assert manifest_key("o1") == "manifests/o1/pkg-o1.json"
