"""
ساخت Flask app و ثبت همه blueprintها
"""

import os
from flask import Flask
from web.routes.dashboard  import bp as bp_dashboard
from web.routes.printers   import bp as bp_printers
from web.routes.logs       import bp as bp_logs
from web.routes.export_bp  import bp as bp_export
from web.routes.scan       import bp as bp_scan
from web.routes.discover   import bp as bp_discover
from web.routes.stats      import bp as bp_stats
from web.routes.validation import bp as bp_validation
from web.routes.system     import bp as bp_system

# مسیر مطلق پوشه web/ (همین فایل در web/ قرار دارد)
_WEB_DIR = os.path.dirname(os.path.abspath(__file__))

# پیشوندهای مجاز برای CORS (شبکه داخلی + localhost)
_CORS_ALLOWED_PREFIXES = (
    "http://172.16.", "https://172.16.",
    "http://192.168.", "https://192.168.",
    "http://10.",      "https://10.",
    "http://127.",     "https://127.",
    "http://localhost", "https://localhost",
)


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=os.path.join(_WEB_DIR, "templates"),
        static_folder=os.path.join(_WEB_DIR, "static"),
    )

    for blueprint in (
        bp_dashboard, bp_printers, bp_logs, bp_export,
        bp_scan, bp_discover, bp_stats, bp_validation, bp_system,
    ):
        app.register_blueprint(blueprint)

    @app.after_request
    def cors(r):
        from flask import request as _req
        origin = _req.headers.get("Origin", "")
        # فقط درخواست‌های شبکه داخلی و localhost مجاز هستند
        if any(origin.startswith(p) for p in _CORS_ALLOWED_PREFIXES):
            r.headers['Access-Control-Allow-Origin'] = origin
        else:
            r.headers['Access-Control-Allow-Origin'] = 'null'
        r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        r.headers['Access-Control-Allow-Methods'] = 'GET,POST,DELETE,OPTIONS'
        return r

    return app
