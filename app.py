"""
Flask health check application server for Render/VPS deployment monitoring.
"""
import logging
from flask import Flask, jsonify

flask_app = Flask(__name__)


@flask_app.route("/", methods=["GET"])
@flask_app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint for Render/VPS monitoring."""
    return jsonify({"status": "ok"}), 200


def run_flask(port: int):
    """Run Flask HTTP server in background thread."""
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False)
