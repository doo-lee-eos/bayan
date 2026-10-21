/* Bayan course: progress, quizzes and notes.
 *
 * Signed in  -> everything is saved to the account through /api/learn/*.
 * Guest      -> saved in this browser (localStorage "bayan-learn"); the next
 *               time the learner signs in it is added to their account and
 *               cleared from the browser.
 * If a save to the account fails (offline, say), it is kept in the browser
 * and merged on the next page load, so nothing is lost.
 */
(function () {
    "use strict";

    var KEY = "bayan-learn";
    var stateEl = document.getElementById("learn-state");
    if (!stateEl) return;
    var page;
    try { page = JSON.parse(stateEl.textContent); } catch (e) { return; }

    // ---------------------------------------------------------- browser store
    function readLocal() {
        try {
            var d = JSON.parse(window.localStorage.getItem(KEY) || "null");
            if (d && typeof d === "object") {
                d.progress = d.progress && typeof d.progress === "object" ? d.progress : {};
                d.notes = d.notes && typeof d.notes === "object" ? d.notes : {};
                return d;
            }
        } catch (e) { /* storage blocked or corrupt */ }
        return { progress: {}, notes: {} };
    }
    function writeLocal(d) {
        try { window.localStorage.setItem(KEY, JSON.stringify(d)); return true; }
        catch (e) { return false; }
    }
    function clearLocal() {
        try { window.localStorage.removeItem(KEY); } catch (e) { /* ignore */ }
    }
    function hasData(d) {
        return Object.keys(d.progress).length > 0 || Object.keys(d.notes).some(function (k) {
            return (d.notes[k] || "").trim();
        });
    }

    function post(url, body) {
        return fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": page.csrf },
            body: JSON.stringify(body)
        }).then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
        });
    }

    var local = readLocal();
    var data = page.signedIn
        ? { progress: (page.server && page.server.progress) || {}, notes: (page.server && page.server.notes) || {} }
        : local;

    // Recording a quiz result in the browser copy.
    function localQuiz(store, lesson, score, total) {
        var p = store.progress[lesson] || { done: false, best: null, last: null, total: total, attempts: 0 };
        p.done = true;
        p.last = score;
        p.total = total;
        p.best = (typeof p.best === "number" && p.total === total) ? Math.max(p.best, score) : score;
        p.attempts = (p.attempts || 0) + 1;
        store.progress[lesson] = p;
    }

    // ------------------------------------------------------------- course map
    function renderMap() {
        var rows = document.querySelectorAll(".lesson-row[data-lesson]");
        if (!rows.length) return;
        var order = page.order || [];
        var done = 0, review = [];
        rows.forEach(function (row) {
            var p = data.progress[row.dataset.lesson];
            var score = row.querySelector(".score");
            row.classList.toggle("done", !!(p && p.done));
            if (score) {
                score.textContent = "";
                score.classList.remove("partial");
                if (p && typeof p.best === "number" && p.total) {
                    score.textContent = p.best + "/" + p.total;
                    if (p.best < p.total) score.classList.add("partial");
                }
            }
        });
        order.forEach(function (slug) {
            var p = data.progress[slug];
            if (p && p.done) done++;
            if (p && typeof p.best === "number" && p.total && p.best < p.total) review.push(slug);
        });

        var box = document.querySelector(".learn-progress");
        if (box) {
            var total = order.length || 1;
            box.querySelector(".done-count").textContent = done;
            box.querySelector(".bar span").style.width = Math.round(100 * done / total) + "%";
            var btn = box.querySelector(".continue");
            var next = order.filter(function (s) { return !(data.progress[s] && data.progress[s].done); })[0];
            if (btn && done > 0) {
                if (next) {
                    btn.href = "/learn/" + encodeURIComponent(next);
                    btn.textContent = "Continue: " + (page.titles[next] || next);
                } else {
                    btn.href = "/learn/" + encodeURIComponent(order[0]);
                    btn.textContent = "All lessons done — start again";
                }
            }
        }

        var rbox = document.querySelector(".review-box");
        if (rbox) {
            var list = rbox.querySelector(".review-list");
            list.innerHTML = "";
            review.forEach(function (slug) {
                var p = data.progress[slug];
                var li = document.createElement("li");
                var a = document.createElement("a");
                a.href = "/learn/" + encodeURIComponent(slug);
                a.textContent = page.titles[slug] || slug;
                li.appendChild(a);
                li.appendChild(document.createTextNode(" — best " + p.best + " of " + p.total));
                list.appendChild(li);
            });
            rbox.hidden = review.length === 0;
        }
    }

    // ------------------------------------------------------------------ quiz
    // The lesson page carries {fixed: [...], pool: [...], count: n}. Every
    // attempt shows the hand-written questions plus a fresh draw from the
    // generated pool, spread across the kinds of question, with the options
    // in a new order. Each question arrives with its right answer first.
    function shuffle(a) {
        for (var i = a.length - 1; i > 0; i--) {
            var j = Math.floor(Math.random() * (i + 1));
            var t = a[i]; a[i] = a[j]; a[j] = t;
        }
        return a;
    }

    function drawQuestions(data) {
        var want = Math.max(0, data.count - data.fixed.length);
        var byKind = {};
        data.pool.forEach(function (q) { (byKind[q.kind] = byKind[q.kind] || []).push(q); });
        var kinds = shuffle(Object.keys(byKind));
        kinds.forEach(function (k) { shuffle(byKind[k]); });
        var picked = [], seenPrompt = {};
        while (picked.length < want && kinds.some(function (k) { return byKind[k].length; })) {
            kinds.forEach(function (k) {
                if (picked.length >= want || !byKind[k].length) return;
                var q = byKind[k].pop();
                if (seenPrompt[q.q_html]) return;
                seenPrompt[q.q_html] = true;
                picked.push(q);
            });
        }
        return data.fixed.concat(shuffle(picked));
    }

    function setupQuiz(lesson) {
        var quiz = document.querySelector(".quiz");
        var dataEl = document.getElementById("quiz-data");
        if (!quiz || !dataEl) return;
        var data;
        try { data = JSON.parse(dataEl.textContent); } catch (e) { return; }
        var list = quiz.querySelector(".questions");
        var result = quiz.querySelector(".quiz-result");
        var answered = 0, right = 0, total = 0;

        function render() {
            list.innerHTML = "";
            answered = 0; right = 0;
            var qs = drawQuestions(data);
            total = qs.length;
            qs.forEach(function (q) {
                var li = document.createElement("li");
                li.className = "question";
                var prompt = document.createElement("div");
                prompt.className = "q";
                prompt.innerHTML = q.q_html;
                li.appendChild(prompt);
                var opts = document.createElement("div");
                opts.className = "options";
                var order = q.options_html.map(function (_, i) { return i; });
                if (q.kind !== "fixed") shuffle(order);
                var buttons = [];
                order.forEach(function (i) {
                    var b = document.createElement("button");
                    b.type = "button";
                    b.className = "option";
                    b.innerHTML = q.options_html[i];
                    b.dataset.right = i === q.answer ? "1" : "0";
                    buttons.push(b);
                    opts.appendChild(b);
                });
                li.appendChild(opts);
                var ex = document.createElement("div");
                ex.className = "explain";
                ex.hidden = true;
                ex.innerHTML = q.explain_html;
                li.appendChild(ex);
                buttons.forEach(function (b) {
                    b.addEventListener("click", function () {
                        if (li.classList.contains("answered")) return;
                        li.classList.add("answered");
                        buttons.forEach(function (o) {
                            o.disabled = true;
                            if (o.dataset.right === "1") o.classList.add("right");
                        });
                        if (b.dataset.right === "1") right++;
                        else b.classList.add("wrong");
                        if (ex.textContent.trim()) ex.hidden = false;
                        answered++;
                        if (answered === total) finish();
                    });
                });
                list.appendChild(li);
            });
            result.hidden = true;
        }

        function finish() {
            var line = result.querySelector(".score-line");
            line.textContent = "You got " + right + " of " + total +
                (right === total ? " — every one right." :
                    ". Read the explanations under the ones marked ✗, then try a new set.");
            result.hidden = false;
            saveQuiz(lesson, right, total, line);
        }

        var retry = result.querySelector(".retry");
        if (retry) retry.addEventListener("click", function () {
            render();
            list.scrollIntoView({ behavior: "smooth", block: "start" });
        });
        render();
    }

    // --------------------------------------------------------------- builder
    function setupBuilders() {
        document.querySelectorAll(".builder[data-builder]").forEach(function (box) {
            var data;
            try { data = JSON.parse(box.dataset.builder); } catch (e) { return; }
            var root = null, pat = null;
            var word = box.querySelector(".builder-word");
            var info = box.querySelector(".builder-info");
            var rootBtns = box.querySelectorAll("[data-root]");
            var patBtns = box.querySelectorAll("[data-pattern]");

            function show() {
                if (!root || !pat) {
                    word.innerHTML = "";
                    info.textContent = root ? "Now pick a pattern." : "Pick a root and a pattern.";
                    return;
                }
                var c = data.combos[root + "|" + pat];
                if (!c) return;
                word.innerHTML = c.html;
                info.innerHTML = "";
                if (c.found) {
                    var a = document.createElement("a");
                    a.href = c.href;
                    a.textContent = "In the Qur'an ×" + c.freq;
                    info.appendChild(document.createTextNode("“" + c.gloss + "” · "));
                    info.appendChild(a);
                } else {
                    info.textContent = "The shape is right, but the Qur'an doesn't use this word.";
                }
                box.classList.toggle("found", c.found);
            }
            function pick(btns, btn) {
                btns.forEach(function (b) { b.classList.toggle("on", b === btn); b.setAttribute("aria-pressed", b === btn ? "true" : "false"); });
            }
            rootBtns.forEach(function (b) {
                b.addEventListener("click", function () { root = b.dataset.root; pick(rootBtns, b); show(); });
            });
            patBtns.forEach(function (b) {
                b.addEventListener("click", function () { pat = b.dataset.pattern; pick(patBtns, b); show(); });
            });
            // start with a real word so the box isn't empty
            if (rootBtns.length && patBtns.length) {
                var first = Object.keys(data.combos).filter(function (k) { return data.combos[k].found; })[0];
                if (first) {
                    var parts = first.split("|");
                    rootBtns.forEach(function (b) { if (b.dataset.root === parts[0]) { root = parts[0]; pick(rootBtns, b); } });
                    patBtns.forEach(function (b) { if (b.dataset.pattern === parts[1]) { pat = parts[1]; pick(patBtns, b); } });
                    show();
                }
            }
        });
    }

    function saveQuiz(lesson, score, total, line) {
        function keepLocally(msg) {
            var store = readLocal();
            localQuiz(store, lesson, score, total);
            var ok = writeLocal(store);
            if (!page.signedIn) data = store;
            if (msg) line.textContent += " " + msg;
            else if (!ok) line.textContent += " (This browser isn't letting the page save, so the score won't be kept.)";
        }
        if (page.signedIn) {
            post("/api/learn/quiz", { lesson: lesson, score: score, total: total })
                .then(function (p) { if (p) data.progress[lesson] = p; })
                .catch(function () { keepLocally("(Couldn't reach the server — saved in this browser for now.)"); });
        } else {
            keepLocally();
        }
    }

    // ----------------------------------------------------------------- notes
    function setupNotes(lesson) {
        var box = document.querySelector(".note-box");
        if (!box) return;
        var status = document.querySelector(".note-status");
        box.value = data.notes[lesson] || "";
        var timer = null, lastSaved = box.value;

        function save() {
            var text = box.value;
            if (text === lastSaved) return;
            function keepLocally(msg) {
                var store = readLocal();
                if (text.trim()) store.notes[lesson] = text; else delete store.notes[lesson];
                var ok = writeLocal(store);
                if (!page.signedIn) data = store;
                status.textContent = msg || (ok ? "Saved in this browser." : "This browser isn't letting the page save notes.");
            }
            if (page.signedIn) {
                status.textContent = "Saving…";
                post("/api/learn/note", { lesson: lesson, text: text })
                    .then(function () { lastSaved = text; data.notes[lesson] = text; status.textContent = "Saved."; })
                    .catch(function () { keepLocally("Couldn't reach the server — saved in this browser for now."); });
            } else {
                lastSaved = text;
                keepLocally();
            }
        }
        box.addEventListener("input", function () {
            status.textContent = "";
            clearTimeout(timer);
            timer = setTimeout(save, 800);
        });
        box.addEventListener("blur", function () { clearTimeout(timer); save(); });
        window.addEventListener("pagehide", function () { clearTimeout(timer); save(); });
    }

    // ------------------------------------------------------------------ start
    function start() {
        renderMap();
        setupBuilders();
        if (page.lesson) {
            setupQuiz(page.lesson);
            setupNotes(page.lesson);
        }
    }

    if (page.signedIn && hasData(local)) {
        // Fold what was done in this browser into the account, then forget it here.
        post("/api/learn/sync", local)
            .then(function (state) {
                clearLocal();
                data = { progress: state.progress || {}, notes: state.notes || {} };
            })
            .catch(function () { /* keep it for next time */ })
            .then(start);
    } else {
        start();
    }
})();
