import os
from flask import Flask, jsonify

from . import db
from .config import SECRET_KEY


def create_app():
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.secret_key = SECRET_KEY

    db.init_db()

    # Entra ID SSO: registers /auth/* and a global login guard (no-op in dev
    # mode when the AZURE_* vars are unset). See nbu_research/auth.py.
    from . import auth
    auth.init_app(app)

    # Each module owns one blueprint registered under a stable url_prefix.
    from .modules.projects import bp as projects_bp
    from .modules.interviews import bp as interviews_bp
    from .modules.surveys import bp as surveys_bp
    from .modules.datasets import bp as datasets_bp
    from .modules.edgar import bp as edgar_bp
    from .modules.refinitiv import bp as refinitiv_bp
    from .modules.analysis import bp as analysis_bp
    from .modules.literature import bp as literature_bp
    from .modules.writing import bp as writing_bp
    from .modules.exports import bp as exports_bp

    app.register_blueprint(projects_bp)                       # / and /projects
    app.register_blueprint(interviews_bp, url_prefix="/interviews")
    app.register_blueprint(surveys_bp, url_prefix="/surveys")
    app.register_blueprint(datasets_bp, url_prefix="/datasets")
    app.register_blueprint(edgar_bp, url_prefix="/edgar")
    app.register_blueprint(refinitiv_bp, url_prefix="/refinitiv")
    app.register_blueprint(analysis_bp, url_prefix="/analysis")
    app.register_blueprint(literature_bp, url_prefix="/literature")
    app.register_blueprint(writing_bp, url_prefix="/writing")
    app.register_blueprint(exports_bp, url_prefix="/exports")

    from .jobs import get_job

    @app.route("/api/jobs/<job_id>")
    def api_job_status(job_id):
        job = get_job(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify(job)

    @app.context_processor
    def inject_globals():
        from .config import AVAILABLE_MODELS
        return {"available_models": AVAILABLE_MODELS}

    return app
