from flask import Flask
from flask import render_template
from flask import request

from services.search import lookup, fetch_all_roots

app = Flask(__name__)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/search")
def search():

    query = request.args.get("q", "").strip()

    if not query:
        return render_template("index.html")

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