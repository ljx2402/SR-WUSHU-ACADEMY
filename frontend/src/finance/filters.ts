import { useSearchParams } from "react-router";

/** Filters kept in the URL (shareable, back-button friendly); sent to the backend as query parameters. */
export function useUrlFilters() {
  const [params, setParams] = useSearchParams();
  function setMany(values: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [name, value] of Object.entries(values)) {
      if (value) next.set(name, value); else next.delete(name);
    }
    setParams(next, { replace: true });
  }
  return { params, set: (name: string, value: string) => setMany({ [name]: value }), setMany };
}
