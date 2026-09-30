import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AttendanceStatusBadge, AttendanceSummary } from "./attendance";
import { InvoiceStatusBadge, Money, RegistrationStatusBadge } from "./finance";
import { academyToday, addDays, formatDate, formatMoney, formatPercentage } from "./format";

describe("money", () => {
  it("formats decimal strings without floating point", () => {
    expect(formatMoney("1234.5")).toBe("RM 1,234.50");
    expect(formatMoney("0.10")).toBe("RM 0.10");
    expect(formatMoney("1000000.00")).toBe("RM 1,000,000.00");
    expect(formatMoney("-25.00")).toBe("−RM 25.00");
    expect(formatMoney(0)).toBe("RM 0.00");
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney("abc")).toBe("—");
  });

  it("marks negative amounts", () => {
    render(<Money value="-5.00" />);
    expect(screen.getByText("−RM 5.00")).toHaveClass("money-negative");
  });
});

describe("dates", () => {
  it("uses the academy time zone for today", () => {
    // 2026-09-30 17:30 UTC is already 1 October in Kuala Lumpur (UTC+8).
    expect(academyToday(new Date("2026-09-30T17:30:00Z"))).toBe("2026-10-01");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(formatDate("2026-10-01")).toMatch(/1 Oct 2026/);
  });
});

describe("status badges", () => {
  it("labels invoice and registration states in plain words", () => {
    render(<><InvoiceStatusBadge status="PARTIALLY_PAID" /><RegistrationStatusBadge status="PENDING" /></>);
    expect(screen.getByText("Partially paid")).toBeInTheDocument();
    expect(screen.getByText("Awaiting payment")).toBeInTheDocument();
  });

  it("shows unknown statuses as-is rather than hiding them", () => {
    render(<InvoiceStatusBadge status="SOMETHING_NEW" />);
    expect(screen.getByText("SOMETHING_NEW")).toBeInTheDocument();
  });
});

describe("attendance", () => {
  it("UNMARKED is shown as 'Not marked', never as absent", () => {
    render(<AttendanceStatusBadge status="UNMARKED" />);
    expect(screen.getByText("Not marked")).toBeInTheDocument();
    expect(screen.queryByText(/absent/i)).not.toBeInTheDocument();
  });

  it("shows unmarked students separately from the percentage", () => {
    render(<AttendanceSummary summary={{ percentage: "75.00", present: 3, late: 0, absent: 1, excused: 0, unmarked: 2 }} />);
    expect(screen.getByText("75.00%")).toBeInTheDocument();
    expect(screen.getByText(/1 absent/)).toBeInTheDocument();
    expect(screen.getByText(/2 students not\s+marked \(not counted in the percentage\)/)).toBeInTheDocument();
  });

  it("shows a dash when nothing is marked yet", () => {
    expect(formatPercentage(null)).toBe("—");
    render(<AttendanceSummary summary={{ percentage: null, present: 0, late: 0, absent: 0, excused: 0, unmarked: 4 }} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText(/nothing marked yet/)).toBeInTheDocument();
  });
});
