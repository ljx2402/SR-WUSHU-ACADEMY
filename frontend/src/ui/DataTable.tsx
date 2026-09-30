import type { ReactNode } from "react";

import { EmptyState } from "./states";

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  align?: "start" | "end";
  /** Hide on narrow screens when the column is not essential. */
  priority?: "primary" | "secondary";
}

/**
 * A real <table> on wide screens. On phones each row becomes a card: every
 * cell carries its column name (data-label), shown by the stylesheet.
 */
export function DataTable<T>({ columns, rows, rowKey, caption, emptyMessage, className = "" }: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  caption: string;
  emptyMessage?: string;
  className?: string;
}) {
  if (!rows.length) return <EmptyState message={emptyMessage} />;
  return (
    <div className={`table-wrap ${className}`.trim()}>
      <table className="table">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key} scope="col" className={cellClass(col)}>{col.header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((col) => (
                <td key={col.key} data-label={col.header} className={cellClass(col)}>{col.render(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function cellClass<T>(col: Column<T>) {
  return [col.align === "end" ? "num" : "", col.priority === "secondary" ? "secondary" : ""].join(" ").trim() || undefined;
}
