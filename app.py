from flask import Flask
from flask import render_template
from flask import request

from services.search import lookup, lookup_phrase, fetch_all_roots

app = Flask(__name__)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/search")
def search():

    query = request.args.get("q", "").strip()

    if not query:
        return render_template("index.html")

    # More than one whitespace-separated token -> break the phrase down
    # word by word instead of searching for it as one literal string
    # (which would never match anything in the single-word `words` table).
    if len(query.split()) > 1:
        result = lookup_phrase(query)
    else:
        result = lookup(query)

    return render_template(
        "result.html",
        query=query,
        result=result
    )


@app.route("/roots")
def roots():

    roots = fetch_all_roots()

    return render_template(
        "roots.html",
        roots=roots
    )


if __name__ == "__main__":
    app.run(debug=True)