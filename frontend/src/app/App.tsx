import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Navigate, RouterProvider, type RouteObject } from "react-router";

import { isApiError } from "../api/errors";
import { AuthProvider } from "../auth/AuthProvider";
import { RequireAccess, RequireAuth } from "../auth/guards";
import { AppShell } from "../layout/AppShell";
import { NAV_ITEMS, SUB_ROUTES, type NavItem } from "../nav/navigation";
import { DashboardPage } from "../pages/DashboardPage";
import { LoginPage } from "../pages/LoginPage";
import { ModulePlaceholderPage } from "../pages/ModulePlaceholderPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { AttendancePage } from "../parent/pages/AttendancePage";
import { CompetitionDetailPage, CompetitionsPage } from "../parent/pages/CompetitionPages";
import { CompetitionRegisterPage } from "../parent/pages/CompetitionRegisterPage";
import { FamilyPage } from "../parent/pages/FamilyPage";
import {
  FinanceOverviewPage, InvoiceDetailPage, InvoicesPage, PaymentProofsPage, PaymentsPage, ReceiptDetailPage,
  ReceiptsPage,
} from "../parent/pages/FinancePages";
import { ParentDashboardPage } from "../parent/pages/ParentDashboardPage";
import { SchedulePage } from "../parent/pages/SchedulePage";
import { StudentProfilePage } from "../parent/pages/StudentProfilePage";
import { ParentLayout } from "../parent/ParentContext";
import { CoachAttendancePage } from "../coach/pages/CoachAttendancePage";
import { CoachCompetitionsPage } from "../coach/pages/CoachCompetitionsPage";
import { CoachDashboardPage } from "../coach/pages/CoachDashboardPage";
import { CoachAttendanceIndexPage, CoachSessionDetailPage, CoachSessionsPage } from "../coach/pages/CoachSessionPages";
import { ServicesProvider, type AppServices } from "./services";

/** The app is served under /app/ (Django keeps /api/ and /admin/). */
export const APP_BASENAME = "/app";

/** Built pages, by NAV_ITEMS id or SUB_ROUTES path. Anything else shows "not available yet". */
const PAGES: Record<string, ReactNode> = {
  "coach-dashboard": <CoachDashboardPage />,
  "coach-sessions": <CoachSessionsPage />,
  "coach-attendance": <CoachAttendanceIndexPage />,
  "coach-competitions": <CoachCompetitionsPage />,
  "/coach/sessions/:sessionId": <CoachSessionDetailPage />,
  "/coach/sessions/:sessionId/attendance": <CoachAttendancePage />,
  "parent-overview": <ParentDashboardPage />,
  "parent-family": <FamilyPage />,
  "parent-schedule": <SchedulePage />,
  "parent-attendance": <AttendancePage />,
  "parent-finance": <FinanceOverviewPage />,
  "parent-competitions": <CompetitionsPage />,
  "/parent/students/:studentId": <StudentProfilePage />,
  "/parent/finance/invoices": <InvoicesPage />,
  "/parent/finance/invoices/:invoiceId": <InvoiceDetailPage />,
  "/parent/finance/payments": <PaymentsPage />,
  "/parent/finance/proofs": <PaymentProofsPage />,
  "/parent/finance/receipts": <ReceiptsPage />,
  "/parent/finance/receipts/:receiptId": <ReceiptDetailPage />,
  "/parent/competitions/:competitionId": <CompetitionDetailPage />,
  "/parent/competitions/:competitionId/register": <CompetitionRegisterPage />,
};

const byId = new Map(NAV_ITEMS.map((item) => [item.id, item]));

function guarded(item: NavItem, element: ReactNode, extra: readonly string[] = []) {
  return (
    <RequireAccess portal={item.portal} capabilities={item.capabilities} extraCapabilities={extra}>
      {element}
    </RequireAccess>
  );
}

const sectionRoutes: RouteObject[] = [
  ...NAV_ITEMS.map((item) => ({
    path: item.path,
    element: guarded(item, PAGES[item.id] ?? <ModulePlaceholderPage item={item} />),
  })),
  ...SUB_ROUTES.map((sub) => ({
    path: sub.path,
    element: guarded(byId.get(sub.parent)!, PAGES[sub.path], sub.capabilities),
  })),
];

/**
 * Routes. Every page from NAV_ITEMS and SUB_ROUTES is wrapped in
 * RequireAccess, so opening its URL directly without the role/capability
 * shows "access denied" (the API refuses the data regardless). Parent Portal
 * pages share a layout that knows the parent's children.
 */
export const appRoutes: RouteObject[] = [
  { path: "/login", element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppShell />,
        children: [
          { index: true, element: <Navigate to="/dashboard" replace /> },
          { path: "/dashboard", element: <DashboardPage /> },
          { path: "/parent", element: <Navigate to="/parent/dashboard" replace /> },
          { path: "/coach", element: <Navigate to="/coach/dashboard" replace /> },
          { path: "/parent/students", element: <Navigate to="/parent/family" replace /> },
          {
            element: <ParentLayout />,
            children: sectionRoutes.filter((route) => route.path?.startsWith("/parent/")),
          },
          ...sectionRoutes.filter((route) => !route.path?.startsWith("/parent/")),
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
];

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        // Retry only failures that may be temporary; never 401/403/404/400.
        retry: (count, error) =>
          count < 2 && (!isApiError(error) || ["network", "timeout", "server"].includes(error.kind)),
      },
      mutations: { retry: false },
    },
  });
}

export function AppProviders({ services, queryClient, router }: {
  services: AppServices;
  queryClient: QueryClient;
  router: Parameters<typeof RouterProvider>[0]["router"];
}) {
  return (
    <ServicesProvider services={services}>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <RouterProvider router={router} />
        </AuthProvider>
      </QueryClientProvider>
    </ServicesProvider>
  );
}
