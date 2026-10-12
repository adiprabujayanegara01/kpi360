def register_blueprints(app):
    from . import auth, main, master, cycle, perf, ai_bp, api
    for m in (auth, main, master, cycle, perf, ai_bp):
        app.register_blueprint(m.bp)
    app.register_blueprint(api.bp, url_prefix="/api/v1")
