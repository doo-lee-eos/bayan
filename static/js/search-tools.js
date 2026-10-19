/* Bayan search tools: on-screen Arabic keyboard + live transliteration
   suggestions, added to every <form class="hero-search"> with an input
   named "q". No dependencies. */
(function () {
    "use strict";

    var LATIN = /[A-Za-zÀ-ɏḀ-ỿ]/;
    var ARABIC = /[؀-ۿ]/;

    var ROWS = [
        ["ض", "ص", "ث", "ق", "ف", "غ", "ع", "ه", "خ", "ح", "ج", "د"],
        ["ش", "س", "ي", "ب", "ل", "ا", "ت", "ن", "م", "ك", "ط", "ذ"],
        ["ئ", "ء", "ؤ", "ر", "ى", "ة", "و", "ز", "ظ", "أ", "إ", "آ", "ٱ"]
    ];
    // short vowels and other marks, shown on a dotted circle
    var MARKS = [
        ["َ", "fatha"], ["ُ", "damma"], ["ِ", "kasra"],
        ["ْ", "sukun"], ["ّ", "shadda"], ["ً", "tanwin fath"],
        ["ٌ", "tanwin damm"], ["ٍ", "tanwin kasr"], ["ٰ", "dagger alif"]
    ];

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }

    function setDirection(input) {
        var v = input.value;
        input.style.direction = (LATIN.test(v) && !ARABIC.test(v)) ? "ltr" : "";
    }

    function insertAtCaret(input, text) {
        var start = input.selectionStart == null ? input.value.length : input.selectionStart;
        var end = input.selectionEnd == null ? input.value.length : input.selectionEnd;
        input.value = input.value.slice(0, start) + text + input.value.slice(end);
        var pos = start + text.length;
        try { input.setSelectionRange(pos, pos); } catch (e) {}
        input.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function backspace(input) {
        var start = input.selectionStart == null ? input.value.length : input.selectionStart;
        var end = input.selectionEnd == null ? input.value.length : input.selectionEnd;
        if (start !== end) {
            input.value = input.value.slice(0, start) + input.value.slice(end);
        } else if (start > 0) {
            // Array.from keeps a letter and its mark separate, so one press
            // removes one character, as on a normal keyboard
            var before = Array.from(input.value.slice(0, start));
            before.pop();
            var b = before.join("");
            input.value = b + input.value.slice(start);
            start = b.length;
        }
        try { input.setSelectionRange(start, start); } catch (e) {}
        input.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function setup(form) {
        var input = form.querySelector('input[name="q"]');
        if (!input || form.dataset.searchTools) return;
        form.dataset.searchTools = "1";
        input.setAttribute("dir", "auto");
        input.setAttribute("autocomplete", "off");
        input.setAttribute("spellcheck", "false");
        input.setAttribute("autocapitalize", "off");

        // wrap the form so the keyboard and suggestion list sit under it
        var wrap = el("div", "st-wrap");
        form.parentNode.insertBefore(wrap, form);
        wrap.appendChild(form);

        // ---- keyboard toggle button (before the submit button)
        var toggle = el("button", "st-kbd-toggle");
        toggle.type = "button";
        toggle.title = "Arabic keyboard";
        toggle.setAttribute("aria-label", "Show Arabic keyboard");
        toggle.setAttribute("aria-expanded", "false");
        toggle.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="6" width="19" height="12" rx="2.5"></rect><path d="M6 10h.01M9.5 10h.01M13 10h.01M16.5 10h.01M7.5 14h9"></path></svg><span class="st-kbd-label">ع</span>';
        var submit = form.querySelector('button[type="submit"], button:not([type])');
        form.insertBefore(toggle, submit || null);

        // ---- keyboard panel
        var panel = el("div", "st-kbd");
        panel.hidden = true;
        panel.setAttribute("role", "group");
        panel.setAttribute("aria-label", "Arabic keyboard");
        ROWS.forEach(function (row) {
            var r = el("div", "st-row");
            row.forEach(function (ch) {
                var k = el("button", "st-key", ch);
                k.type = "button";
                k.dataset.insert = ch;
                r.appendChild(k);
            });
            panel.appendChild(r);
        });
        var marks = el("div", "st-row st-marks");
        MARKS.forEach(function (m) {
            var k = el("button", "st-key st-mark", "◌" + m[0]);
            k.type = "button";
            k.dataset.insert = m[0];
            k.title = m[1];
            k.setAttribute("aria-label", m[1]);
            marks.appendChild(k);
        });
        panel.appendChild(marks);
        var ctl = el("div", "st-row st-ctl");
        var root = el("button", "st-key st-wide", "√ root");
        root.type = "button";
        root.title = "Search a root, e.g. √كتب";
        root.dataset.root = "1";
        var space = el("button", "st-key st-space", "space");
        space.type = "button";
        space.dataset.insert = " ";
        var del = el("button", "st-key st-wide", "⌫");
        del.type = "button";
        del.dataset.del = "1";
        del.setAttribute("aria-label", "Delete");
        var go = el("button", "st-key st-wide st-go", "Search");
        go.type = "button";
        go.dataset.go = "1";
        [root, space, del, go].forEach(function (b) { ctl.appendChild(b); });
        panel.appendChild(ctl);
        wrap.appendChild(panel);

        // keep focus (and the caret) in the input while tapping keys
        panel.addEventListener("mousedown", function (e) { e.preventDefault(); });
        panel.addEventListener("click", function (e) {
            var k = e.target.closest(".st-key");
            if (!k) return;
            if (k.dataset.insert != null) insertAtCaret(input, k.dataset.insert);
            else if (k.dataset.del) backspace(input);
            else if (k.dataset.root) {
                if (input.value.indexOf("√") !== 0) {
                    input.value = "√" + input.value.replace(/^\s+/, "");
                    input.dispatchEvent(new Event("input", { bubbles: true }));
                }
            } else if (k.dataset.go) {
                if (input.value.trim()) form.submit();
                return;
            }
            input.focus();
        });

        function sizePanel() {
            var w = form.getBoundingClientRect().width;
            panel.style.width = w + "px";
            list.style.width = w + "px";
        }

        function openKbd(open) {
            panel.hidden = !open;
            toggle.setAttribute("aria-expanded", open ? "true" : "false");
            toggle.classList.toggle("on", open);
            // stop the phone's own keyboard covering ours while it's open
            if (open) input.setAttribute("inputmode", "none");
            else input.removeAttribute("inputmode");
            sizePanel();
            input.focus();
        }
        toggle.addEventListener("mousedown", function (e) { e.preventDefault(); });
        toggle.addEventListener("click", function () { openKbd(panel.hidden); });

        // ---- transliteration suggestions
        var list = el("ul", "st-suggest");
        list.hidden = true;
        list.setAttribute("role", "listbox");
        wrap.appendChild(list);
        var active = -1, items = [], timer = null, lastQ = "", seq = 0;

        function render() {
            list.innerHTML = "";
            if (!items.length) { list.hidden = true; return; }
            var head = el("li", "st-suggest-head", "Matches for “" + lastQ + "”");
            head.setAttribute("aria-hidden", "true");
            list.appendChild(head);
            items.forEach(function (it, i) {
                var li = el("li", "st-option" + (i === active ? " active" : ""));
                li.setAttribute("role", "option");
                li.dataset.index = i;
                li.appendChild(el("span", "st-ar", it.arabic));
                var tag = it.kind === "phrase" ? "phrase" : it.kind === "root" ? "root" : (i === 0 ? "best match" : "");
                if (tag) li.appendChild(el("span", "st-tag", tag));
                list.appendChild(li);
            });
            sizePanel();
            list.hidden = false;
        }

        function fetchSuggestions() {
            var q = input.value.trim();
            if (!q || !LATIN.test(q) || ARABIC.test(q)) { items = []; render(); return; }
            if (q === lastQ && items.length) { render(); return; }
            var mine = ++seq;
            fetch("/api/transliterate?q=" + encodeURIComponent(q))
                .then(function (r) { return r.ok ? r.json() : []; })
                .then(function (data) {
                    if (mine !== seq) return;          // a newer keystroke won
                    lastQ = q;
                    items = data || [];
                    active = -1;
                    render();
                })
                .catch(function () {});
        }

        function choose(i) {
            if (!items[i]) return;
            input.value = items[i].arabic;
            setDirection(input);
            items = [];
            render();
            form.submit();
        }

        input.addEventListener("input", function () {
            setDirection(input);
            clearTimeout(timer);
            timer = setTimeout(fetchSuggestions, 180);
        });
        input.addEventListener("keydown", function (e) {
            if (list.hidden) return;
            if (e.key === "ArrowDown") { e.preventDefault(); active = (active + 1) % items.length; render(); }
            else if (e.key === "ArrowUp") { e.preventDefault(); active = (active - 1 + items.length) % items.length; render(); }
            else if (e.key === "Enter" && active >= 0) { e.preventDefault(); choose(active); }
            else if (e.key === "Escape") { items = []; render(); }
        });
        list.addEventListener("mousedown", function (e) { e.preventDefault(); });
        list.addEventListener("click", function (e) {
            var li = e.target.closest(".st-option");
            if (li) choose(+li.dataset.index);
        });
        document.addEventListener("click", function (e) {
            if (!wrap.contains(e.target)) { list.hidden = true; }
        });
        input.addEventListener("focus", function () { if (items.length) render(); });
        window.addEventListener("resize", sizePanel);
        setDirection(input);
    }

    function init() {
        document.querySelectorAll("form.hero-search").forEach(setup);
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
