// Self-check of step 7's recovery (no model calls): node extraction2/spec_flow/recover_check.js  -> prints "ok"
// It runs the functions as they are in index.html, on the Markdown and resolved text server.py makes for one page.
const fs = require("fs"), assert = require("assert");
const src = fs.readFileSync(__dirname + "/index.html", "utf8"), grab = re => src.match(re)[0];
const hasWord = eval([/^const norm = .*$/m, /^const has = .*$/m, /^const hasWord = [\s\S]*?return false; };$/m, /^const mdLine = .*$/m, /^function recover[\s\S]*?\n}\n/m].map(grab).join("\n") + "\nhasWord");   // recover() is declared by the eval

// server.to_md / server.resolve(..., "production") of: nav, <h1>Gun X, a sentence, a spec table, a footer "Weight: 1,200 kg"
const md = "- Home\n- Crew training\n# Gun X\nThe Gun X is a 30 mm cannon.\n| Calibre | 30 mm |\n| Range | 4 km |\n| Crew | 3 |\nWeight: 1,200 kg";
const res = "Gun X\nThe Gun X is a 30 mm cannon.\nCalibre | 30 mm\nRange | 4 km\nCrew | 3";
const final = { products: [{ product: "Gun X", maker: "", specs: [{ parameter: "calibre_mm", value_text: "30 mm" }] }] };
const names = ["calibre_mm", "maximum_range_km", "crew_count", "weight_kg", "rate_of_fire_rpm"];
const miss = (product, parameter, value, evidence) => ({ product, parameter, value, evidence });
const { out, rows } = recover(final, [
  miss("Gun X", "maximum_range_km", "4 km", "| Range | 4 km |"),        // its table row is in the passage: added
  miss("Gun X", "crew_count", "3", "Crew | 3"),                         // a short value, real evidence: added
  miss("Gun X", "rate_of_fire_rpm", "3", "Rate of fire: 3"),            // a short value, invented evidence: rejected
  miss("Gun X", "weight_kg", "1,200 kg", "Weight: 1,200 kg"),           // in the footer the resolver cut: reported
  miss("Gun X", "calibre_mm", "30 mm", "| Calibre | 30 mm |"),          // step 6 has it: skipped
  miss("Gun", "calibre_mm", "30 mm", "The Gun X is a 30 mm cannon."),   // other product spelling, value already out: skipped
  miss("Gun X", "barrel_length", "4 km", "| Range | 4 km |"),           // not an ontology name: rejected
  miss("Gun X", "maximum_range_km", "4 km", "| Range | 4 km |"),        // the judge repeats itself: skipped
], md, res, names);
assert.deepStrictEqual(rows.map(r => r.status), ["added", "added", "no proof on the page", "resolver dropped it",
  "already in the output", "already in the output", "not an ontology parameter", "already in the output"]);
assert.deepStrictEqual(out.products.map(p => [p.product, p.specs.map(s => s.value_text)]), [["Gun X", ["30 mm", "4 km", "3"]]]);
assert.strictEqual(final.products[0].specs.length, 1);                  // step 6's output is not changed
// a missed product: added as a new product only with its proof on the page
const r2 = recover({ products: [] }, [miss("Gun X", "crew_count", "3", "| Crew | 3 |")], md, res, names);
assert.deepStrictEqual(r2.out.products, [{ product: "Gun X", maker: "", specs: [{ parameter: "crew_count", value_text: "3" }] }]);
assert.ok(hasWord("~45 km", "45 km") && hasWord("Crew | 3", "3") && !hasWord("30 mm", "3") && !hasWord("x", ""));
console.log("ok");
