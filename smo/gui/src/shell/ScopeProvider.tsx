/** The scope provider of the shell (GUI-9.3, rules in `data/scope.ts`): reads the scope from the URL (`?region=&cluster=`) and gives it to every
 * read below it. A navigation that drops the parameters (a link inside a page, `setSearchParams` of a filter) is given them back at once (the URL
 * is replaced, so the history gets no extra entry); going back or forward (a "POP") takes the URL as it is, so the back button can leave a scope.
 * The picker changes the scope through `setScope`, which also writes the URL. */
import { useCallback, useEffect, useMemo, useRef, type ReactNode } from "react";
import { useLocation, useNavigate, useNavigationType } from "react-router-dom";

import { isScoped, ScopeContext, scopeFromSearch, withScopeSearch, type Scope } from "../data/scope";

/** Same scope? */
const same = (a: Scope, b: Scope) => a.region === b.region && a.cluster === b.cluster;

/** The provider. */
export function ScopeProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navType = useNavigationType();
  const navigate = useNavigate();
  const fromUrl = useMemo(() => scopeFromSearch(location.search), [location.search]);
  // the scope in force: the URL's, or the one carried over a navigation that dropped it
  const last = useRef<Scope>(fromUrl);
  const carry = !isScoped(fromUrl) && isScoped(last.current) && navType !== "POP";
  const scope = carry ? last.current : fromUrl;

  useEffect(() => {
    if (carry) {
      navigate({ pathname: location.pathname, search: withScopeSearch(location.search, last.current), hash: location.hash }, { replace: true, state: location.state });
    } else if (!same(last.current, fromUrl)) {
      last.current = fromUrl;
    }
  }, [location.key, carry]); // eslint-disable-line react-hooks/exhaustive-deps

  const setScope = useCallback((next: Scope) => {
    last.current = next;
    navigate({ pathname: location.pathname, search: withScopeSearch(location.search, next), hash: location.hash });
  }, [navigate, location.pathname, location.search, location.hash]);

  const value = useMemo(() => ({ scope, setScope }), [scope.region, scope.cluster, setScope]); // eslint-disable-line react-hooks/exhaustive-deps
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}
