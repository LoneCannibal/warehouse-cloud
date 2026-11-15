#!/usr/bin/env python3
"""Serves the Warehouse Console with a fake API, so the page can be seen and tested
without Google Cloud or the local stack.

    python3 scripts/console_dev.py            then open http://localhost:8099
    PORT=9000 python3 scripts/console_dev.py

The fake API answers the same routes the public load balancer exposes, with the same
response shapes. An order moves through ACCEPTED, PICKING, PACKING, PACKED and
DISPATCHED over about eight seconds, now and then a pick is reclaimed as attempt 2,
and the number of pickers rises and falls every few minutes like the autoscaler.
Standard library only.
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PAGE = Path(__file__).resolve().parents[1] / "services" / "order-intake" / "app" / "static" / "index.html"
ZONES = ("A", "B", "C")
SEED_ON_HAND = 1_000_000
PICKER_IDS = ["k2x9q", "m7d4w", "r5t8z", "v3n6b", "h9c2x", "q4w7e"]

lock = threading.Lock()
orders: dict[str, dict] = {}
picked: dict[str, int] = {}
reserved: dict[str, int] = {}
started = time.time()
# Some history, so the charts have something to draw before any order is placed.
rng = random.Random(7)
history = {zone: [rng.randint(20, 60) for _ in range(30)] for zone in ZONES}


def bin_for(sku: str) -> str:
    zone, n = sku[4], int(sku[5:])
    return f"{zone}-{n % 10:02d}-{n % 7:02d}"


def pickers() -> list[str]:
    # Two pickers, rising to four for one minute in every three, like a scale-out.
    phase = int(time.time() - started) % 180
    count = 4 if 60 <= phase < 120 else 2
    return [f"picking-6f9c7d8b5d-{n}" for n in PICKER_IDS[:count]]


def workers() -> list[dict]:
    result = [{"worker_id": w, "capability": "picking", "zones": list(ZONES), "slots": 4} for w in pickers()]
    result += [{"worker_id": f"packing-7b4f6c9d8e-{n}", "capability": "packing", "zones": ["P"], "slots": 4} for n in ("h3j6v", "p8s2c")]
    return result


def tasks_of(order: dict) -> tuple[str, list[dict]]:
    """The order's status and tasks at its current age."""
    age = time.time() - order["placed"]
    names = pickers()
    tasks = []
    for i, line in enumerate(order["lines"]):
        status = "PENDING" if age < 1.0 else ("ASSIGNED" if age < 4.0 else "DONE")
        tasks.append({
            "task_id": f"{order['order_id']}.PICK.{line['zone']}", "task_type": "PICK", "zone": line["zone"],
            "status": status, "worker_id": None if status == "PENDING" else names[(order["seed"] + i) % len(names)],
            "attempt": 2 if order["reclaimed"] and i == 0 else 1, "bins": [line["bin"]],
        })
    if age >= 4.0:
        pack = "PENDING" if age < 4.5 else ("ASSIGNED" if age < 6.5 else "DONE")
        tasks.append({
            "task_id": f"{order['order_id']}.PACK", "task_type": "PACK", "zone": "P", "status": pack,
            "worker_id": None if pack == "PENDING" else "packing-7b4f6c9d8e-h3j6v", "attempt": 1,
            "bins": [line["bin"] for line in order["lines"]],
        })
    if age >= 4.0 and not order["confirmed"]:
        with lock:
            order["confirmed"] = True
            for line in order["lines"]:
                picked[line["sku"]] = picked.get(line["sku"], 0) + line["quantity"]
                reserved[line["sku"]] = reserved.get(line["sku"], 0) - line["quantity"]
    status = ("DISPATCHED" if age >= 8 else "PACKED" if age >= 6.5 else "PACKING" if age >= 4.0
              else "PICKING" if age >= 1.0 else "ACCEPTED")
    return status, tasks


def summary(order: dict) -> dict:
    status, tasks = tasks_of(order)
    return {
        "order_id": order["order_id"], "status": status, "tasks_total": len(tasks),
        "tasks_done": sum(t["status"] == "DONE" for t in tasks), "max_attempt": max(t["attempt"] for t in tasks),
        "dispatched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(order["placed"] + 8)) if status == "DISPATCHED" else None,
    }


def public(order: dict) -> dict:
    return {k: order[k] for k in ("order_id", "customer", "priority", "lines", "created_at")}


def floor() -> dict:
    tasks, waiting = [], {}
    for order in list(orders.values()):
        _, order_tasks = tasks_of(order)
        for t in order_tasks:
            if t["status"] == "ASSIGNED":
                tasks.append({k: t[k] for k in ("task_id", "task_type", "zone", "worker_id", "attempt", "bins")} | {"order_id": order["order_id"]})
            elif t["status"] == "PENDING":
                waiting[t["task_type"]] = waiting.get(t["task_type"], 0) + 1
    dispatched = sum(1 for o in orders.values() if time.time() - o["placed"] >= 8)
    return {"tasks": tasks, "waiting": waiting, "orders": {"open": len(orders) - dispatched, "dispatched": dispatched}}


def windows(zone: str, limit: int) -> list[dict]:
    now = int(time.time() // 60 * 60_000)
    result = []
    for i in range(limit):
        end = now - i * 60_000
        start = end - 60_000
        recent = [o for o in orders.values() if start <= o["placed"] * 1000 < end]
        if zone in ZONES:
            n = sum(1 for o in recent for line in o["lines"] if line["zone"] == zone) + (history[zone][i] if i < 30 else 0)
        else:
            n = len(recent) + (sum(history[z][i] for z in ZONES) // 2 if i < 30 else 0)
        failures = 1 if (i % 7 == 3 and zone == "A") else 0
        result.append({
            "zone": zone, "window_start": start, "window_end": end, "tasks_created": n,
            "picks_completed": n if zone in ZONES else 0, "packs_completed": n if zone == "P" else 0,
            "dispatched": n if zone == "D" else 0, "tasks_abandoned": failures, "tasks_reclaimed": failures,
            "active_workers": (4 if i % 9 < 3 else 2) if zone != "D" else 0,
            "queue_depth": max(0, n - 40) if zone in ZONES else 0, "mean_recovery_ms": 11200.0 if failures else 0.0,
        })
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter than the default
        pass

    def send(self, status: int, body, content_type: str = "application/json") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)

        def arg(name: str, default: str = "") -> str:
            return (query.get(name) or [default])[0]

        if path == "/":
            return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/__demo":
            # For screenshots: opens the console on one view, with orders and a burst.
            ids = json.dumps([i for i in arg("ids").split(",") if i])
            view, burst = arg("view", "shop"), int(arg("burst", "0"))
            harness = f"""<!doctype html><meta charset=utf-8><style>html,body,iframe{{margin:0;border:0;width:100%;height:100%}}</style>
<iframe id=f src="/#{view}"></iframe><script>
f.onload = () => setTimeout(() => {{
  const w = f.contentWindow;
  for (const id of {ids}) w.consoleDemo.track(id);
  if ({burst}) w.consoleDemo.burst({burst});
  if ("{arg("cart")}") w.consoleDemo.fillCart();
}}, 300);
</script>"""
            return self.send(200, harness.encode(), "text/html; charset=utf-8")
        if path == "/v1/workers":
            return self.send(200, {"workers": workers()})
        if path == "/v1/floor":
            return self.send(200, floor())
        if path == "/v1/analytics/latest":
            return self.send(200, {"windows": [windows(z, 1)[0] for z in (*ZONES, "P", "D")]})
        if path == "/v1/analytics/windows":
            return self.send(200, {"zone": arg("zone"), "windows": windows(arg("zone"), min(200, int(arg("limit", "30"))))})
        if path == "/v1/stock":
            zone = arg("zone")
            if zone not in ZONES:
                return self.send(422, {"detail": "zone must be one of A, B, C"})
            limit = min(100, max(1, int(arg("limit", "20"))))
            items = []
            for n in range(limit):
                sku = f"SKU-{zone}{n:03d}"
                base = (n * 37 + ord(zone)) % 900  # some sales history
                items.append({"sku": sku, "name": f"Item {zone}{n:03d}", "zone": zone, "bin": bin_for(sku),
                              "on_hand": SEED_ON_HAND - base - picked.get(sku, 0), "reserved": max(0, reserved.get(sku, 0)),
                              "picked": base + picked.get(sku, 0)})
            return self.send(200, {"zone": zone, "items": items})
        if path == "/v1/orders":
            if arg("ids"):
                wanted = [i for i in arg("ids").split(",") if i][:100]
                chosen = [orders[i] for i in wanted if i in orders]
            else:
                chosen = sorted(orders.values(), key=lambda o: -o["placed"])[: int(arg("limit", "50"))]
            chosen.sort(key=lambda o: -o["placed"])
            return self.send(200, {"orders": [public(o) | {"fulfilment": summary(o)} for o in chosen]})
        if path.startswith("/v1/orders/"):
            order = orders.get(path.rsplit("/", 1)[-1])
            if order is None:
                return self.send(404, {"detail": "unknown order"})
            status, tasks = tasks_of(order)
            body = public(order) | {"fulfilment": {"order_id": order["order_id"], "status": status,
                                                    "tasks": [{k: t[k] for k in t if k != "bins"} for t in tasks]}}
            return self.send(200, body)
        return self.send(404, {"detail": "Not Found"})

    def do_POST(self):
        if urlparse(self.path).path != "/v1/orders":
            return self.send(404, {"detail": "Not Found"})
        try:
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            lines = request["lines"]
            assert request["customer"] and 1 <= len(lines) <= 50
            assert all(1 <= int(line["quantity"]) <= 1000 and line["sku"][4] in ZONES for line in lines)
        except (KeyError, AssertionError, ValueError, IndexError, TypeError):
            return self.send(422, {"detail": [{"loc": ["body"], "msg": "invalid order"}]})
        if any(int(line["quantity"]) > 900 for line in lines):
            return self.send(409, {"detail": f"insufficient stock for {lines[0]['sku']}"})
        order_id = f"ord-{uuid.uuid4().hex[:16]}"
        allocated = [{"sku": l["sku"], "quantity": int(l["quantity"]), "zone": l["sku"][4], "bin": bin_for(l["sku"])} for l in lines]
        with lock:
            for line in allocated:
                reserved[line["sku"]] = reserved.get(line["sku"], 0) + line["quantity"]
            orders[order_id] = {
                "order_id": order_id, "customer": request["customer"], "priority": int(request.get("priority", 0)),
                "lines": allocated, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "placed": time.time(), "reclaimed": random.random() < 0.15, "confirmed": False,
                "seed": random.randrange(1000),
            }
        return self.send(202, {"order_id": order_id, "status": "ACCEPTED"})


def main() -> None:
    port = int(os.environ.get("PORT", "8099"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Warehouse Console with a fake API: http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
