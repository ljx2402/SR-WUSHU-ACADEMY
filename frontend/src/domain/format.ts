/**
 * Formatting helpers. Money arrives from the API as decimal strings
 * ("1234.50"); it is formatted as text, never converted to a float.
 * Dates and times are shown in the academy's time zone.
 */

export const ACADEMY_TIME_ZONE = "Asia/Kuala_Lumpur";
export const CURRENCY_SYMBOL = "RM";

const DECIMAL = /^(-)?(\d+)(?:\.(\d+))?$/;

/** "1234.5" → "RM 1,234.50"; "-10" → "−RM 10.00"; invalid input → "—". */
export function formatMoney(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const text = typeof value === "number" ? value.toFixed(2) : value.trim();
  const match = DECIMAL.exec(text);
  if (!match) return "—";
  const [, sign, whole, fraction = ""] = match;
  const cents = (fraction + "00").slice(0, 2); // the API already sends 2 decimal places
  const grouped = whole.replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign ? "−" : ""}${CURRENCY_SYMBOL} ${grouped}.${cents}`;
}

/** "87.50" → "87.50%"; null (nothing marked) → "—". */
export function formatPercentage(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return `${value}%`;
}

/** Today's date (YYYY-MM-DD) in the academy time zone, whatever the device's zone. */
export function academyToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: ACADEMY_TIME_ZONE, year: "numeric", month: "2-digit",
                                            day: "2-digit" }).format(now);
}

export function addDays(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d + days));
  return date.toISOString().slice(0, 10);
}

/** "2026-10-01" → "Thu, 1 Oct 2026" (a calendar date: no time-zone shift). */
export function formatDate(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Intl.DateTimeFormat("en-MY", { weekday: "short", day: "numeric", month: "short", year: "numeric",
                                            timeZone: "UTC" }).format(new Date(Date.UTC(y, m - 1, d)));
}

/** "17:00:00" → "17:00". */
export function formatTime(time: string): string {
  return time.slice(0, 5);
}

/** An API timestamp shown in academy time: "1 Oct 2026, 17:30". */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-MY", { day: "numeric", month: "short", year: "numeric", hour: "2-digit",
                                            minute: "2-digit", hour12: false, timeZone: ACADEMY_TIME_ZONE })
    .format(date);
}
