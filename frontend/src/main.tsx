import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import "./index.css";
import "./print.css";   // 2026-10-24: A4 print (lib/print.ts)
import { registerPwa } from "./pwa/register";   // Stream G4: service worker + install prompt

registerPwa();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);
