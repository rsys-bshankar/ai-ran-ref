/** URL query-parameter state for the new RAN pages (Element, RAN topology, Software, Configuration): a selected element, campaign or job, or a
 * list filter, kept in `?name=` so a link or a reload lands on the same view. Setting one keeps the other parameters and the tab in the hash
 * (`useHashTab` writes the hash with `history.replaceState`, which the router does not see, so it is read from `window.location`). */
import { useNavigate, useSearchParams } from "react-router-dom";

/** The value of `?name=` and a setter (null removes it). The change replaces the history entry. */
export function useUrlParam(name: string): [string | null, (value: string | null) => void] {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const set = (value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value); else next.delete(name);
    const search = next.toString();
    navigate({ search: search ? `?${search}` : "", hash: window.location.hash }, { replace: true });
  };
  return [params.get(name), set];
}
