"""Dev check (2026-10-20 global search): every sidebar menu route has a navigation-index entry.

Reads the menu definitions the app renders — components/DashboardLayout.tsx (NAV + EMPLOYEE_NAV:
`to: "…"`, `real("slug", "/path", …)`, `opsNav("slug", "resource", …)`) —
and the registry frontend/src/search/searchIndex.ts (PAGE_INDEX keys). Exit 1 and list the
routes when any menu route has no entry; also lists stale index keys no menu item uses.

Run:  python -X utf8 scripts/check_search_index.py
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "src")


def read(rel: str) -> str:
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def menu_routes() -> set[str]:
    lay = read("components/DashboardLayout.tsx")
    start = lay.index("const NAV: NavGroup[]")
    end = lay.index("function useDarkMode")
    body = lay[start:end]
    routes = set(re.findall(r'\bto:\s*"([^"]+)"', body))
    routes |= set(re.findall(r'\breal\("[^"]+",\s*"([^"]+)"', body))
    routes |= {f"/ops/{r}" for r in re.findall(r'\bopsNav\("[^"]+",\s*"([^"]+)"', body)}
    return routes


def index_keys() -> set[str]:
    src = read("search/searchIndex.ts")
    start = src.index("export const PAGE_INDEX")
    end = src.index("export const EXTRA_PAGES")
    return set(re.findall(r'^\s*"(/[^"]*)":\s*\{', src[start:end], flags=re.M))


def main() -> int:
    routes, keys = menu_routes(), index_keys()
    missing = sorted(routes - keys)
    stale = sorted(keys - routes)
    print(f"menu routes: {len(routes)}  index entries: {len(keys)}")
    if stale:
        print("note — index keys with no menu item (kept for EXTRA links / harmless):", ", ".join(stale))
    if missing:
        print("FAIL — menu routes with NO search-index entry (add them to frontend/src/search/searchIndex.ts):")
        for r in missing:
            print("   ", r)
        return 1
    print("OK — every menu route is in the search index")
    return 0


if __name__ == "__main__":
    sys.exit(main())
