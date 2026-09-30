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

/** Decimal strings → integer cents (BigInt), so sums never touch floating point. */
function toCents(value: string): bigint | null {
  const match = DECIMAL.exec(value.trim());
  if (!match) return null;
  const [, sign, whole, fraction = ""] = match;
  const cents = BigInt(whole) * 100n + BigInt((fraction + "00").slice(0, 2));
  return sign ? -cents : cents;
}

/** Adds decimal money strings exactly: ["220.00", "280.00", "50.00"] → "550.00". */
export function sumMoney(values: string[]): string {
  let total = 0n;
  for (const value of values) {
    const cents = toCents(value);
    if (cents === null) return "";
    total += cents;
  }
  const negative = total < 0n;
  const abs = negative ? -total : total;
  return `${negative ? "-" : ""}${abs / 100n}.${String(abs % 100n).padStart(2, "0")}`;
}

/** True if a decimal money string is greater than zero. */
export function isPositiveMoney(value: string | null | undefined): boolean {
  const cents = value ? toCents(value) : null;
  return cents !== null && cents > 0n;
}

/** Shows only the last 4 characters of an identity number ("•••••••1234"). */
export function maskIdentifier(value: string | null | undefined): string {
  if (!value) return "—";
  const visible = value.slice(-4);
  return `${"•".repeat(Math.max(value.length - 4, 4))}${visible}`;
}

/** Monday of the week containing the date (YYYY-MM-DD, calendar arithmetic only). */
export function weekStart(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  const weekday = new Date(Date.UTC(y, m - 1, d)).getUTCDay(); // 0 = Sunday
  return addDays(isoDate, weekday === 0 ? -6 : 1 - weekday);
}

/** "2026-10-01" → "Thursday". */
export function weekdayName(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Intl.DateTimeFormat("en-MY", { weekday: "long", timeZone: "UTC" }).format(new Date(Date.UTC(y, m - 1, d)));
}

/** "2026-10-01"–"2026-10-31" → "1 Oct 2026 – 31 Oct 2026"; either may be missing. */
export function formatPeriod(start: string | null | undefined, end: string | null | undefined): string {
  const short = (iso: string) => formatDate(iso).replace(/^[A-Za-z]+, /, "");
  if (start && end) return start === end ? short(start) : `${short(start)} – ${short(end)}`;
  return start ? short(start) : end ? short(end) : "—";
}

/** "2026-10-01" → "Thu 1 Oct" (compact, for tables). */
export function formatShortDate(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" })
    .format(new Date(Date.UTC(y, m - 1, d))).replace(",", "");
}
