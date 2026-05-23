from flask import Blueprint, render_template
from core import store

bp = Blueprint("dashboard", __name__)


@bp.route('/')
def index():
    return render_template("dashboard.html", poll_interval=store.get_poll_interval())
