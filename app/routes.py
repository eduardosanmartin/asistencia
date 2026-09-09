"""Root routes of the application."""

from flask import redirect, render_template, url_for

from .blueprints.auth import login_required


def register_routes(app):
    """Registers the root routes on the application."""

    @app.route("/")
    @login_required
    def index():
        """Home page — the redirect target after a successful login."""
        return render_template("index.html")

    @app.route("/home")
    def home_redirect():
        """Compatibility alias so external links to /home keep working."""
        return redirect(url_for("index"))