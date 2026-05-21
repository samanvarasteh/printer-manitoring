import socket
from flask import Blueprint, jsonify, request
from core import store
from config.settings import POLL_INTERVAL, FLASK_PORT

bp = Blueprint("system", __name__)


@bp.route('/api/status')
def api_status():
    try:
        host_ip = socket.gethostbyname(socket.gethostname())
    except:
        host_ip = "127.0.0.1"
    return jsonify({
        "status":        "running",
        "poll_interval": POLL_INTERVAL,  # 🔥 این خط باید باشد
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
