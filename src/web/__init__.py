"""
Last Ember — Flask app factory.
Web dashboard for MMUD, runs in-process with the mesh daemon.
"""
import os

from flask import Flask

from src.web import config as web_config


def create_app(db_path=None):
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
    )
    app.secret_key = web_config.WEB_SECRET_KEY
    app.config["MMUD_DB_PATH"] = db_path or web_config.DB_PATH

    # Register blueprints
    from src.web.routes.public import bp as public_bp
    from src.web.routes.admin import bp as admin_bp
    from src.web.routes.api import bp as api_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/api")

    # DB teardown
    from src.web.services.gamedb import init_app
    init_app(app)

    # Template context
    from src.web.npc_blurbs import NPC_BLURBS

    @app.context_processor
    def inject_config():
        return {
            "poll_status_interval": web_config.POLL_STATUS_INTERVAL,
            "poll_broadcast_interval": web_config.POLL_BROADCAST_INTERVAL,
            "npc_blurbs": NPC_BLURBS,
        }

    return app
