// Checks src/lib/datetime.ts (rule 2026-10-25: store UTC, show local, "today" in the org's zone).
//   node frontend/scripts/check_datetime.mjs          (Node >= 22.18 / 23.6: runs the .ts directly)
// Runs with the PROCESS in America/Toronto so the old `toISOString().slice(0, 10)` bug would show
// (a Toronto evening is already the next day in UTC).
// Also greps src/ for `toISOString().slice(0, 10)` / `.split("T")[0]`: every hit must carry an
// "instant-ok" comment on the same line (it is sending a UTC instant on purpose).
process.env.TZ = "America/Toronto";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
let D;
try {
  D = await import("../src/lib/datetime.ts");                        // Node with type stripping
} catch {                                                            // older Node (CI 20): transpile with the project's tsc
  const ts = (await import("typescript")).default;
  const { mkdtempSync, writeFileSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { pathToFileURL } = await import("node:url");
  const out = ts.transpileModule(readFileSync(join(HERE, "..", "src", "lib", "datetime.ts"), "utf8"),
    { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
  const f = join(mkdtempSync(join(tmpdir(), "dtcheck-")), "datetime.mjs");
  writeFileSync(f, out);
  D = await import(pathToFileURL(f).href);
}
let n = 0;
const eq = (a, b, msg) => { assert.equal(a, b, msg); n++; };
const utc = (s) => new Date(s).getTime();

// 1) "today" late in the Toronto evening (EDT, UTC-4) is the Toronto date, not UTC's.
eq(D.todayISO("America/Toronto", utc("2026-10-31T03:30:00Z")), "2026-10-30", "23:30 EDT 30 Oct");
eq(D.todayISO("America/Toronto", utc("2026-10-31T03:59:00Z")), "2026-10-30", "23:59 EDT 30 Oct");
eq(D.todayISO("America/Toronto", utc("2026-10-31T04:00:00Z")), "2026-10-31", "00:00 EDT 31 Oct");
eq(D.todayISO("America/Vancouver", utc("2026-10-31T06:30:00Z")), "2026-10-30", "23:30 PDT 30 Oct");
eq(D.todayISO("UTC", utc("2026-10-31T03:30:00Z")), "2026-10-31", "UTC zone");
eq(D.todayISO(undefined, utc("2026-10-31T03:30:00Z")), "2026-10-30", "default zone = Toronto");
eq(D.thisMonthISO("America/Toronto", utc("2026-11-01T03:30:00Z")), "2026-10", "23:30 EDT 31 Oct is still October");
// what the old code did at that moment, to prove the test can see the bug
eq(new Date(utc("2026-10-31T03:30:00Z")).toISOString().slice(0, 10), "2026-10-31", "old bug reproduced"); // instant-ok

// 2) month ends — Oct 31, Feb 28 / 29, Dec → Jan
eq(D.monthEndISO("2026-10"), "2026-10-31", "Oct 31");
eq(D.monthEndISO("2026-10-05"), "2026-10-31", "Oct 31 from a day");
eq(D.monthEndISO("2026-02"), "2026-02-28", "Feb 28");
eq(D.monthEndISO("2028-02-10"), "2028-02-29", "Feb 29 leap");
eq(D.monthEndISO("2100-02"), "2100-02-28", "2100 not leap");
eq(D.monthStartISO("2026-10-31"), "2026-10-01", "month start");

// 3) date arithmetic is calendar arithmetic (no zone can shift it)
eq(D.addDaysISO("2026-10-31", 1), "2026-11-01", "Oct 31 + 1");
eq(D.addDaysISO("2026-03-01", -1), "2026-02-28", "Mar 1 - 1");
eq(D.addDaysISO("2028-03-01", -1), "2028-02-29", "leap Mar 1 - 1");
eq(D.addDaysISO("2026-12-31", 1), "2027-01-01", "year end");
eq(D.addDaysISO("2026-10-25", 7), "2026-11-01", "+7");
eq(D.addMonthsISO("2026-01-31", 1), "2026-02-28", "Jan 31 + 1 month clamps");
eq(D.addMonthsISO("2027-11-30", 3), "2028-02-29", "Nov 30 + 3 months, leap");
eq(D.addMonthsISO("2026-10-15", -10), "2025-12-15", "negative months");
eq(D.addMonthsYM("2026-12", 1), "2027-01", "YM rollover");
eq(D.addMonthsYM("2026-01", -2), "2025-11", "YM back");
eq(D.weekdayISO("2026-10-26"), 0, "Mon = 0");
eq(D.weekdayISO("2026-10-25"), 6, "Sun = 6");
eq(D.mondayOfISO("2026-10-25"), "2026-10-19", "Monday of Sunday");
eq(D.mondayOfISO("2026-10-26"), "2026-10-26", "Monday of Monday");
eq(D.daysBetweenISO("2026-10-01", "2026-10-31"), 30, "days between");
eq(D.toLocalDateISO(new Date(2026, 9, 31)), "2026-10-31", "local midnight stays the 31st");

// 4) instants: shown in the zone, with the zone named
eq(D.dateInZone("2026-10-31T02:00:00Z", "America/Toronto"), "2026-10-30", "UTC 02:00 = previous Toronto day");
assert.match(D.formatTime("2026-10-25T14:30:00Z", { tz: "America/Toronto" }), /10:30\s*a\.?m\.?\s*EDT/i); n++;
assert.match(D.formatDateTime("2026-10-25T14:30:00+00:00", { tz: "America/Toronto" }), /Oct 25, 2026.*10:30\s*a\.?m\.?\s*EDT/i); n++;
assert.doesNotMatch(D.formatDateTime("2026-10-25T14:30:00Z", { tz: "America/Toronto", zone: false }), /EDT/); n++;
eq(D.formatDate("2026-10-31"), "Oct 31, 2026", "calendar date not shifted");
eq(D.formatDateTime(null), "—", "blank");
eq(D.zoneLabel("America/Toronto", utc("2026-10-25T14:30:00Z")), "EDT", "zone label");

// 5) datetime-local round trip (process zone = Toronto, EDT in October)
eq(D.fromDateTimeLocal("2026-10-25T10:30"), "2026-10-25T14:30:00.000Z", "local 10:30 EDT → 14:30 UTC");
eq(D.toDateTimeLocal("2026-10-25T14:30:00Z"), "2026-10-25T10:30", "UTC → local input");
eq(D.toDateTimeLocal("2026-10-31T03:30:00Z"), "2026-10-30T23:30", "23:30 EDT input shows the 30th");
eq(D.fromDateTimeLocal(""), null, "blank input");

// 6) org zone setting
D.setOrgTimeZone("Not/AZone"); eq(D.orgTimeZone(), "America/Toronto", "bad zone keeps Toronto");
D.setOrgTimeZone("America/Vancouver"); eq(D.todayISO(undefined, utc("2026-10-31T05:00:00Z")), "2026-10-30", "org zone used");
D.setOrgTimeZone(null); eq(D.orgTimeZone(), "America/Toronto", "reset");

// 7) grep proof — no calendar date is made from a UTC instant anywhere in src/
const SRC = join(HERE, "..", "src");
const BAD = /toISOString\(\)\.(slice\(0,\s*(10|7)\)|substring\(0,\s*(10|7)\)|split\(["']T["']\)\[0\])/;
const hits = [];
const walk = (d) => { for (const f of readdirSync(d)) { const p = join(d, f); if (statSync(p).isDirectory()) walk(p); else if (/\.(tsx?|mjs)$/.test(f)) {
  readFileSync(p, "utf8").split(/\r?\n/).forEach((ln, i) => { if (BAD.test(ln) && !/instant-ok/.test(ln)) hits.push(`${p}:${i + 1}: ${ln.trim().slice(0, 140)}`); }); } } };
walk(SRC);
if (hits.length) { console.error(`${hits.length} calendar date(s) still built from a UTC instant:\n` + hits.join("\n")); process.exit(1); }
console.log(`datetime helpers: ${n} checks passed; grep: 0 toISOString() date slices in src/ (TZ=${process.env.TZ})`);
