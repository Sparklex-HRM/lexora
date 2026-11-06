import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

/** A page tab kept in the URL (`?tab=…` by default) instead of local state, so global search,
 *  bookmarks and the browser Back button can open the exact tab. Drop-in for
 *  `const [tab, setTab] = useState(initial)`:
 *
 *      const [tab, setTab] = useUrlTab("issued");            // ?tab=
 *      const [sub, setSub] = useUrlTab("ecr", "sub");        // ?sub=
 *
 *  The default value is not written to the URL (clean links); `replace` keeps history short.
 *  Other query params (e.g. `hl` from search) are kept. */
export function useUrlTab<T extends string>(initial: T, key = "tab"): [T, (next: T) => void] {
  const [sp, setSp] = useSearchParams();
  const value = (sp.get(key) as T | null) || initial;
  const set = useCallback((next: T) => {
    setSp((prev) => {
      const p = new URLSearchParams(prev);
      if (!next || next === initial) p.delete(key); else p.set(key, next);
      return p;
    }, { replace: true });
  }, [setSp, key, initial]);
  return [value, set];
}
