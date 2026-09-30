import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, RouterProvider, type RouteObject } from "react-router";

import { isApiError } from "../api/errors";
import { AuthProvider } from "../auth/AuthProvider";
import { RequireAccess, RequireAuth } from "../auth/guards";
import { AppShell } from "../layout/AppShell";
import { NAV_ITEMS } from "../nav/navigation";
import { DashboardPage } from "../pages/DashboardPage";
import { LoginPage } from "../pages/LoginPage";
import { ModulePlaceholderPage } from "../pages/ModulePlaceholderPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { ServicesProvider, type AppServices } from "./services";

/** The app is served under /app/ (Django keeps /api/ and /admin/). */
export const APP_BASENAME = "/app";

/**
 * Routes. Every page from NAV_ITEMS is wrapped in RequireAccess, so opening
 * its URL directly without the role/capability shows "access denied"
 * (the API refuses the data regardless).
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
          ...NAV_ITEMS.map((item) => ({
            path: item.path,
            element: (
              <RequireAccess portal={item.portal} capabilities={item.capabilities}>
                <ModulePlaceholderPage item={item} />
              </RequireAccess>
            ),
          })),
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
