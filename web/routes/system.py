import socket
from flask import Blueprint, jsonify, request
from core import store
from config.settings import FLASK_PORT

bp = Blueprint("system", __name__)


@bp.route('/api/status')
def api_status():
    try:
        host_ip = socket.gethostbyname(socket.gethostname())
    except:
        host_ip = "127.0.0.1"
    return jsonify({
        "status":        "running",
        "poll_interval": store.get_poll_interval(),
        "host_ip":       host_ip,
        "port":          FLASK_PORT,
        "dashboard_url": f"http://{host_ip}:{FLASK_PORT}/",
        **store.poll_stats,
    })


@bp.route('/api/poll/now', methods=['POST'])
def api_poll_now():
    import threading
    from core.poller import poll_all
    threading.Thread(target=poll_all, daemon=True).start()
    return jsonify({"status": "started"})


@bp.route('/api/poll/interval', methods=['GET', 'POST'])
def api_poll_interval():
    if request.method == 'GET':
        return jsonify({"poll_interval": store.get_poll_interval()})

    body = request.get_json(silent=True) or {}
    sec = body.get("seconds")
    if sec is None:
        return jsonify({"error": "seconds required"}), 400
    try:
        sec = int(sec)
    except Exception:
        return jsonify({"error": "seconds must be integer"}), 400

    updated = store.set_poll_interval(sec)
    return jsonify({"status": "ok", "poll_interval": updated})
