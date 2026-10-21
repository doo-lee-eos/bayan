import os
import secrets
from pathlib import Path

from flask import Flask
from flask import abort
from flask import jsonify
from flask import redirect
from flask import render_template
from flask import request
from flask import session
from flask import url_for

from services.search import lookup, lookup_phrase, fetch_all_roots
from services import translit
from services import accounts
from services.course import load_course, get_lesson

app = Flask(__name__)


# ---------------------------------------------------------------- sessions
# Sign-in uses Flask's signed session cookie, which needs a secret key.
# Set BAYAN_SECRET_KEY in the environment on a real server; otherwise a
# random key is created once and kept in instance/secret_key (don't commit it).
def _secret_key():
    key = os.environ.get("BAYAN_SECRET_KEY")
    if key:
        return key
    path = Path(app.instance_path) / "secret_key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
    return path.read_text().strip()


app.secret_key = _secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _check_csrf():
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf")
    if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
        abort(400, "Your session expired. Reload the page and try again.")


def current_user():
    return accounts.get_user(session.get("uid"))


@app.context_processor
def _template_globals():
    return {"csrf_token": csrf_token, "current_user": current_user}


def _safe_next(target):
    """Only send people back to a page on this site."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("learn")


# ------------------------------------------------------------------ search
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/search")
def search():

    query = request.args.get("q", "").strip()

    if not query:
        return render_template("index.html")

    # Typed in Latin letters ("allah", "rahman", "√ktb", "alhamdulillah"):
    # turn it into the closest Qur'anic Arabic and search that instead,
    # telling the page what was typed and what else it could have meant.
    transliteration = translit.resolve(query)
    search_for = query
    if transliteration is not None:
        if transliteration["arabic"]:
            search_for = transliteration["arabic"]
        else:
            return render_template(
                "result.html",
                query=query,
                result={"type": "none", "query": query},
                transliteration=transliteration,
            )

    # More than one whitespace-separated token -> break the phrase down
    # word by word instead of searching for it as one literal string
    # (which would never match anything in the single-word `words` table).
    if len(search_for.split()) > 1:
        result = lookup_phrase(search_for)
    else:
        result = lookup(search_for)

    return render_template(
        "result.html",
        query=search_for,
        result=result,
        transliteration=transliteration,
    )


@app.route("/api/transliterate")
def api_transliterate():
    """Live suggestions for the search box while someone types in Latin
    letters: [{"arabic": ..., "count": ...}], best first."""
    query = request.args.get("q", "").strip()
    if not translit.is_latin_query(query.lstrip("√")):
        return jsonify([])
    resolved = translit.resolve(query)
    items = []
    if resolved and resolved["arabic"]:
        items.append({"arabic": resolved["arabic"], "kind": resolved["kind"]})
        for alt in resolved["alternatives"][:6]:
            items.append({"arabic": alt, "kind": resolved["kind"]})
    return jsonify(items)


# ------------------------------------------------------------------ course
# Every lesson is open to everyone. Signing in only adds saving progress,
# quiz results and notes to an account; without one they are kept in the
# browser and moved into the account on first sign-in.
@app.route("/learn")
def learn():
    user = current_user()
    state = accounts.get_state(user["id"]) if user else None
    return render_template("learn.html", course=load_course(), state=state)


@app.route("/learn/<slug>")
def lesson(slug):
    item = get_lesson(slug)
    if item is None:
        abort(404)
    user = current_user()
    state = accounts.get_state(user["id"]) if user else None
    return render_template("lesson.html", course=load_course(), lesson=item, state=state)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        _check_csrf()
        try:
            uid = accounts.create_user(request.form.get("email"), request.form.get("password"),
                                       request.form.get("name"))
            session.clear()
            session["uid"] = uid
            return redirect(_safe_next(request.form.get("next")))
        except accounts.AccountError as e:
            error = str(e)
    return render_template("auth.html", mode="signup", error=error,
                           next=request.values.get("next", ""), email=request.form.get("email", ""))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        _check_csrf()
        uid = accounts.authenticate(request.form.get("email"), request.form.get("password"))
        if uid:
            session.clear()
            session["uid"] = uid
            return redirect(_safe_next(request.form.get("next")))
        error = "That email and password don't match."
    return render_template("auth.html", mode="login", error=error,
                           next=request.values.get("next", ""), email=request.form.get("email", ""))


@app.route("/logout", methods=["POST"])
def logout():
    _check_csrf()
    session.clear()
    return redirect(_safe_next(request.form.get("next")))


def _api_user():
    user = current_user()
    if not user:
        abort(401)
    _check_csrf()
    return user


@app.route("/api/learn/quiz", methods=["POST"])
def api_quiz():
    user = _api_user()
    data = request.get_json(silent=True) or {}
    if not get_lesson(data.get("lesson")):
        abort(400)
    try:
        accounts.record_quiz(user["id"], data["lesson"], data.get("score"), data.get("total"))
    except (accounts.AccountError, TypeError, ValueError):
        abort(400)
    return jsonify(accounts.get_state(user["id"])["progress"].get(data["lesson"]))


@app.route("/api/learn/complete", methods=["POST"])
def api_complete():
    user = _api_user()
    data = request.get_json(silent=True) or {}
    if not get_lesson(data.get("lesson")):
        abort(400)
    accounts.mark_complete(user["id"], data["lesson"])
    return jsonify({"ok": True})


@app.route("/api/learn/note", methods=["POST"])
def api_note():
    user = _api_user()
    data = request.get_json(silent=True) or {}
    if not get_lesson(data.get("lesson")):
        abort(400)
    accounts.save_note(user["id"], data["lesson"], data.get("text", ""))
    return jsonify({"ok": True})


@app.route("/api/learn/sync", methods=["POST"])
def api_sync():
    """Move progress saved in the browser (before signing in) into the account."""
    user = _api_user()
    accounts.merge_guest(user["id"], request.get_json(silent=True) or {}, set(load_course()["order"]))
    return jsonify(accounts.get_state(user["id"]))


# ------------------------------------------------------------------- other
@app.route("/roots")
def roots():

    roots = fetch_all_roots()

    return render_template(
        "roots.html",
        roots=roots
    )


@app.route("/about")
def about():
    return render_template("about.html")


if __name__ == "__main__":
    app.run(debug=True)
