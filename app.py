import json
import os
import queue
import threading
import time
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import db as database
from ue_automation import build_ue_cart

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]

database.init_db()

# SSE broadcast queue — one queue per connected client
_sse_listeners: list[queue.Queue] = []
_sse_lock = threading.Lock()

# Automation status: None | "running" | {"status": "done"|"error", "message": str}
_automation_status = None
_automation_lock = threading.Lock()


def _broadcast(event: str, data: str):
    msg = f"event: {event}\ndata: {data}\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_listeners:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_listeners.remove(q)


def require_admin(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@app.route("/")
def submit_page():
    with database.get_db() as conn:
        order = database.get_active_order(conn)
        items = database.get_items(conn, order["id"])
    return render_template("submit.html", items=[dict(i) for i in items])


@app.route("/items", methods=["POST"])
def add_item():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    quantity = (data.get("quantity") or "").strip()
    url = (data.get("url") or "").strip()
    if not name or not quantity:
        return jsonify({"error": "name and quantity required"}), 400
    with database.get_db() as conn:
        order = database.get_active_order(conn)
        database.add_item(conn, order["id"], name, quantity, url)
        items = database.get_items(conn, order["id"])
    _broadcast("refresh", json.dumps([dict(i) for i in items]))
    return jsonify({"ok": True})


@app.route("/items/list")
def list_items():
    with database.get_db() as conn:
        order = database.get_active_order(conn)
        items = database.get_items(conn, order["id"])
    return jsonify([dict(i) for i in items])


@app.route("/events")
def sse():
    q: queue.Queue = queue.Queue(maxsize=50)
    with _sse_lock:
        _sse_listeners.append(q)

    def stream():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield msg
                except queue.Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            with _sse_lock:
                if q in _sse_listeners:
                    _sse_listeners.remove(q)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Admin auth
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin_page"))
        error = "Wrong password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.route("/admin")
@require_admin
def admin_page():
    with database.get_db() as conn:
        order = database.get_active_order(conn)
        items = database.get_items(conn, order["id"])
        addresses = database.get_addresses(conn)
    return render_template(
        "admin.html",
        items=[dict(i) for i in items],
        addresses=[dict(a) for a in addresses],
        order=dict(order),
    )


@app.route("/admin/items/<int:item_id>", methods=["PUT"])
@require_admin
def edit_item(item_id):
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    quantity = (data.get("quantity") or "").strip()
    url = (data.get("url") or "").strip()
    if not name or not quantity:
        return jsonify({"error": "name and quantity required"}), 400
    with database.get_db() as conn:
        database.update_item(conn, item_id, name, quantity, url)
        order = database.get_active_order(conn)
        items = database.get_items(conn, order["id"])
    _broadcast("refresh", json.dumps([dict(i) for i in items]))
    return jsonify({"ok": True})


@app.route("/admin/items/<int:item_id>", methods=["DELETE"])
@require_admin
def delete_item(item_id):
    with database.get_db() as conn:
        database.delete_item(conn, item_id)
        order = database.get_active_order(conn)
        items = database.get_items(conn, order["id"])
    _broadcast("refresh", json.dumps([dict(i) for i in items]))
    return jsonify({"ok": True})


@app.route("/admin/addresses", methods=["GET"])
@require_admin
def list_addresses():
    with database.get_db() as conn:
        addresses = database.get_addresses(conn)
    return jsonify([dict(a) for a in addresses])


@app.route("/admin/addresses", methods=["POST"])
@require_admin
def add_address():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    address = (data.get("address") or "").strip()
    if not name or not address:
        return jsonify({"error": "name and address required"}), 400
    with database.get_db() as conn:
        database.add_address(conn, name, address)
    return jsonify({"ok": True})


@app.route("/admin/addresses/<int:addr_id>", methods=["DELETE"])
@require_admin
def delete_address(addr_id):
    with database.get_db() as conn:
        database.delete_address(conn, addr_id)
    return jsonify({"ok": True})


@app.route("/admin/place-order", methods=["POST"])
@require_admin
def place_order():
    global _automation_status
    data = request.get_json(force=True)
    address_id = data.get("address_id")
    store_hint = (data.get("store_hint") or "grocery").strip()

    with database.get_db() as conn:
        order = database.get_active_order(conn)
        items = [dict(i) for i in database.get_items(conn, order["id"])]
        addresses = {a["id"]: dict(a) for a in database.get_addresses(conn)}

    if not items:
        return jsonify({"error": "No items to order"}), 400

    address_obj = addresses.get(address_id) if address_id else None
    address_str = address_obj["address"] if address_obj else ""

    order_id = order["id"]

    with _automation_lock:
        _automation_status = "running"

    def run():
        global _automation_status
        try:
            build_ue_cart(items, address_str, store_hint)
            with database.get_db() as conn:
                database.close_order(conn, order_id, address_id, store_hint)
            items_after = []
            with database.get_db() as conn:
                new_order = database.get_active_order(conn)
                items_after = [dict(i) for i in database.get_items(conn, new_order["id"])]
            _broadcast("refresh", json.dumps(items_after))
            _broadcast("order-done", json.dumps({"status": "done"}))
            with _automation_lock:
                _automation_status = {"status": "done", "message": "Cart built! Open Uber Eats to confirm."}
        except Exception as exc:
            with _automation_lock:
                _automation_status = {"status": "error", "message": str(exc)}
            _broadcast("order-done", json.dumps({"status": "error", "message": str(exc)}))

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True, "message": "Automation started"})


@app.route("/admin/order-status")
@require_admin
def order_status():
    with _automation_lock:
        status = _automation_status
    if status is None:
        return jsonify({"status": "idle"})
    if status == "running":
        return jsonify({"status": "running"})
    return jsonify(status)


@app.route("/admin/orders")
@require_admin
def order_history():
    with database.get_db() as conn:
        history = database.get_order_history(conn)
    return render_template("history.html", history=history)


@app.route("/health")
def health():
    return "ok"


if __name__ == "__main__":
    app.run(debug=True, threaded=True)
