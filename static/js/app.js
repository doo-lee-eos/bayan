// Bayan JS (v1)

// ---- basic sanity check ----
console.log("Bayan frontend loaded");

// ---- future hooks (important) ----
// These are placeholders for when we upgrade to diagram UI
const Bayan = {
    word: null,

    init(wordData) {
        this.word = wordData;
        this.setupHoverEffects();
    },

    setupHoverEffects() {
        // future: highlight prefix/stem/root on hover
        const parts = document.querySelectorAll(".part");

        parts.forEach(p => {
            p.addEventListener("mouseenter", () => {
                p.style.transform = "translateY(-3px)";
                p.style.transition = "0.2s ease";
            });

            p.addEventListener("mouseleave", () => {
                p.style.transform = "translateY(0px)";
            });
        });
    }
};
function connect(a, b, lineId) {
    const A = document.getElementById(a).getBoundingClientRect();
    const B = document.getElementById(b).getBoundingClientRect();
    const svg = document.querySelector(".lines").getBoundingClientRect();

    const x1 = A.left + A.width / 2 - svg.left;
    const y1 = A.top + A.height / 2 - svg.top;

    const x2 = B.left + B.width / 2 - svg.left;
    const y2 = B.top + B.height / 2 - svg.top;

    const line = document.getElementById(lineId);
    line.setAttribute("x1", x1);
    line.setAttribute("y1", y1);
    line.setAttribute("x2", x2);
    line.setAttribute("y2", y2);
}

function drawDiagram() {
    connect("center", "prefix", "l1");
    connect("center", "stem", "l2");
    connect("center", "suffix", "l3");

    connect("center", "root", "l4");
    connect("center", "meaning", "l5");
    connect("center", "occ", "l6");
}

window.addEventListener("load", drawDiagram);
window.addEventListener("resize", drawDiagram);

// ---- "Search" nav link: jump straight to the search bar ----
// The nav's Search link points at /#search. When it's a fresh navigation
// to the home page, the browser already jumps to the #search element on
// its own -- but it lands you AT the search bar without focusing the
// input, so you still have to click before you can type. When you're
// ALREADY on the home page and click it again, there's no navigation and
// no "load" event at all, so nothing would happen without this.
// goToSearch() covers both: smooth-scrolls to the search bar and focuses
// the input, called on initial load (if the hash is already #search) and
// on every subsequent hashchange.
function goToSearch() {
    if (window.location.hash !== "#search") return;

    const input = document.getElementById("search-input");
    if (!input) return;

    input.closest("#search").scrollIntoView({ behavior: "smooth", block: "center" });
    // Wait for the scroll to settle before focusing, so the page doesn't
    // jump again as focus brings the input into view a second time.
    window.setTimeout(() => input.focus(), 400);
}

window.addEventListener("load", goToSearch);
window.addEventListener("hashchange", goToSearch);