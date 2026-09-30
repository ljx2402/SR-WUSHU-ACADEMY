import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter } from "react-router";

import { APP_BASENAME, AppProviders, appRoutes, createQueryClient } from "./app/App";
import { createServices } from "./app/services";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/components.css";

const router = createBrowserRouter(appRoutes, { basename: APP_BASENAME });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppProviders services={createServices()} queryClient={createQueryClient()} router={router} />
  </StrictMode>,
);
