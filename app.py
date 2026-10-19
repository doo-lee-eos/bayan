from flask import Flask
from flask import jsonify
from flask import render_template
from flask import request

from services.search import lookup, lookup_phrase, fetch_all_roots
from services import translit

app = Flask(__name__)


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
