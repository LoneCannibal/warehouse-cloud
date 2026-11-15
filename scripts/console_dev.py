#!/usr/bin/env python3
"""Serves the Warehouse Console with a fake API, so the page can be seen and tested
without Google Cloud or the local stack.

    python3 scripts/console_dev.py            then open http://localhost:8099
    PORT=9000 python3 scripts/console_dev.py

The fake API answers the same routes the public load balancer exposes, with the same
response shapes. An order moves through ACCEPTED, PICKING, PACKING, PACKED and
DISPATCHED over about six seconds, and now and then a pick is reclaimed as attempt 2.
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

lock = threading.Lock()
orders: dict[str, dict] = {}
picked: dict[str, int] = {}
reserved: dict[str, int] = {}
started = time.time()


def bin_for(sku: str) -> str:
    zone, n = sku[4], int(sku[5:])
    return f"{zone}-{n % 10:02d}-{n % 7:02d}"


def workers() -> list[dict]:
    # One extra picker joins for half of every minute, like the autoscaler at work.
    pickers = 2 + (1 if int(time.time() - started) % 60 >= 30 else 0)
    names = ["k2x9q", "m7d4w", "r5t8z"][:pickers]
    result = [{"worker_id": f"picking-6f9c7d8b5d-{n}", "capability": "picking", "zones": list(ZONES), "slots": 4} for n in names]
    result += [{"worker_id": f"packing-7b4f6c9d8e-{n}", "capability": "packing", "zones": ["P"], "slots": 4} for n in ("h3j6v", "p8s2c")]
    return result


def order_view(order: dict) -> dict:
    age = time.time() - order["placed"]
    pickers = [w["worker_id"] for w in workers() if w["capability"] == "picking"]
    tasks = []
    for i, line in enumerate(order["lines"]):
        status = "PENDING" if age < 0.8 else ("ASSIGNED" if age < 2.5 else "DONE")
        attempt = 2 if order["reclaimed"] and i == 0 else 1
        tasks.append({
            "task_id": f"{order['order_id']}.PICK.{line['zone']}", "task_type": "PICK", "zone": line["zone"],
            "status": status, "worker_id": None if status == "PENDING" else pickers[i % len(pickers)], "attempt": attempt,
        })
    if age >= 2.5:
        pack = "PENDING" if age < 3.0 else ("ASSIGNED" if age < 4.5 else "DONE")
        tasks.append({
            "task_id": f"{order['order_id']}.PACK", "task_type": "PACK", "zone": "P", "status": pack,
            "worker_id": None if pack == "PENDING" else "packing-7b4f6c9d8e-h3j6v", "attempt": 1,
        })
    if age >= 6:
        status = "DISPATCHED"
    elif age >= 4.5:
        status = "PACKED"
    elif age >= 2.5:
        status = "PACKING"
    elif age >= 0.8:
        status = "PICKING"
    else:
        status = "ACCEPTED"
    if age >= 2.5 and not order["confirmed"]:
        with lock:
            order["confirmed"] = True
            for line in order["lines"]:
                picked[line["sku"]] = picked.get(line["sku"], 0) + line["quantity"]
                reserved[line["sku"]] = reserved.get(line["sku"], 0) - line["quantity"]
    return {"order_id": order["order_id"], "status": status, "tasks": tasks}


def windows() -> list[dict]:
    now = int(time.time() // 60 * 60_000)
    start, end = now - 60_000, now
    recent = [o for o in orders.values() if o["placed"] * 1000 >= start - 60_000]
    result = []
    for zone in (*ZONES, "P", "D"):
        n = sum(1 for o in recent for line in o["lines"] if line["zone"] == zone) if zone in ZONES else len(recent)
        result.append({
            "zone": zone, "window_start": start, "window_end": end, "tasks_created": n,
            "picks_completed": n if zone in ZONES else 0, "packs_completed": n if zone == "P" else 0,
            "dispatched": n if zone == "D" else 0, "tasks_abandoned": 1 if zone == "A" and n else 0,
            "tasks_reclaimed": 1 if zone == "A" and n else 0, "active_workers": 2 if zone != "D" else 0,
            "queue_depth": 0, "mean_recovery_ms": 11200.0 if zone == "A" and n else 0.0,
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
        if path == "/":
            return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/__demo":
            # For screenshots: shows the console with some orders already tracked.
            ids = json.dumps((query.get("ids") or [""])[0].split(","))
            burst = int((query.get("burst") or ["0"])[0])
            harness = f"""<!doctype html><meta charset=utf-8><style>html,body,iframe{{margin:0;border:0;width:100%;height:100%}}</style>
<iframe id=f src="/"></iframe><script>
f.onload = () => setTimeout(() => {{
  const d = f.contentDocument;
  for (const id of {ids}) if (id) {{ d.getElementById("track-id").value = id; d.getElementById("track-form").requestSubmit(); }}
  d.getElementById("order-form").requestSubmit();
  if ({burst}) {{ d.getElementById("burst-count").value = {burst}; d.getElementById("burst-rate").value = 10; d.getElementById("burst-start").click(); }}
}}, 300);
</script>"""
            return self.send(200, harness.encode(), "text/html; charset=utf-8")
        if path == "/v1/workers":
            return self.send(200, {"workers": workers()})
        if path == "/v1/analytics/latest":
            return self.send(200, {"windows": windows()})
        if path == "/v1/stock":
            zone = (query.get("zone") or [""])[0]
            if zone not in ZONES:
                return self.send(422, {"detail": [{"loc": ["query", "zone"], "msg": "zone must be A, B or C"}]})
            limit = min(100, max(1, int((query.get("limit") or ["20"])[0])))
            items = []
            for n in range(limit):
                sku = f"SKU-{zone}{n:03d}"
                items.append({"sku": sku, "name": f"Item {zone}{n:03d}", "zone": zone, "bin": bin_for(sku),
                              "on_hand": SEED_ON_HAND - picked.get(sku, 0), "reserved": max(0, reserved.get(sku, 0))})
            return self.send(200, {"zone": zone, "items": items})
        if path.startswith("/v1/orders/"):
            order = orders.get(path.rsplit("/", 1)[-1])
            if order is None:
                return self.send(404, {"detail": "unknown order"})
            body = {k: order[k] for k in ("order_id", "customer", "priority", "lines", "created_at")}
            body["fulfilment"] = order_view(order)
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
