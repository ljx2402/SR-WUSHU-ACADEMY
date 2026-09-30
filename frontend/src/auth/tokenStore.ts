/**
 * Where the API token lives in the browser.
 *
 * sessionStorage: survives a page reload but not closing the tab, and is not
 * sent automatically with requests (so no CSRF). The backend also expires
 * tokens (14 days), rotates them on every sign-in and revokes them on logout,
 * role, password or active-status changes. The production Content-Security-
 * Policy (script-src 'self') is the main defence against script injection
 * reading it. The token is never logged or shown.
 */
export interface TokenStore {
  get(): string | null;
  set(token: string): void;
  clear(): void;
}

const KEY = "sr-wushu.token";

export function browserTokenStore(storage: Storage | undefined = safeSessionStorage()): TokenStore {
  let memory: string | null = null; // fallback when storage is unavailable (private mode, blocked)
  return {
    get() {
      try {
        return storage?.getItem(KEY) ?? memory;
      } catch {
        return memory;
      }
    },
    set(token) {
      memory = token;
      try {
        storage?.setItem(KEY, token);
      } catch {
        /* memory only */
      }
    },
    clear() {
      memory = null;
      try {
        storage?.removeItem(KEY);
      } catch {
        /* nothing stored */
      }
    },
  };
}

function safeSessionStorage(): Storage | undefined {
  try {
    return window.sessionStorage;
  } catch {
    return undefined;
  }
}
