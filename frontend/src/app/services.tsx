import { createContext, useContext, type ReactNode } from "react";

import { ApiClient } from "../api/client";
import { endpoints, type Endpoints } from "../api/endpoints";
import { browserTokenStore, type TokenStore } from "../auth/tokenStore";

/**
 * Everything that talks to the outside world, created once and provided by
 * context, so tests can swap in a fake `fetch` and storage.
 */
export interface AppServices {
  api: ApiClient;
  endpoints: Endpoints;
  tokens: TokenStore;
  /** Called by the API client on any 401 (set by the AuthProvider). */
  setUnauthorizedHandler(handler: () => void): void;
}

export interface ServiceOptions {
  fetchImpl?: typeof fetch;
  tokens?: TokenStore;
  baseUrl?: string;
  timeoutMs?: number;
}

export function createServices(options: ServiceOptions = {}): AppServices {
  const tokens = options.tokens ?? browserTokenStore();
  let unauthorized: () => void = () => tokens.clear();
  const api = new ApiClient({
    baseUrl: options.baseUrl ?? import.meta.env.VITE_API_BASE_URL ?? "",
    timeoutMs: options.timeoutMs ?? (Number(import.meta.env.VITE_API_TIMEOUT_MS) || 15000),
    getToken: () => tokens.get(),
    onUnauthorized: () => unauthorized(),
    fetchImpl: options.fetchImpl,
  });
  return {
    api,
    endpoints: endpoints(api),
    tokens,
    setUnauthorizedHandler(handler) {
      unauthorized = handler;
    },
  };
}

const ServicesContext = createContext<AppServices | null>(null);

export function ServicesProvider({ services, children }: { services: AppServices; children: ReactNode }) {
  return <ServicesContext.Provider value={services}>{children}</ServicesContext.Provider>;
}

export function useServices(): AppServices {
  const services = useContext(ServicesContext);
  if (!services) throw new Error("useServices must be used inside ServicesProvider");
  return services;
}
