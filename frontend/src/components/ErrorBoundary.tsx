import React from "react";

interface State { hasError: boolean; message?: string; }

/** Same role as the HMS ErrorBoundary: catch a render crash and show a
 *  recoverable panel instead of a white screen. */
export default class ErrorBoundary extends React.Component<{ children: React.ReactNode }, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(err: Error): State {
    return { hasError: true, message: err.message };
  }

  componentDidCatch(err: Error, info: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("Lexora HRM render error:", err, info);
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div className="min-h-screen flex items-center justify-center p-6">
        <div className="lx-card p-8 max-w-md text-center">
          <div className="text-4xl mb-3">⚠️</div>
          <h1 className="text-lg font-semibold" style={{ color: "var(--ink)" }}>Something went wrong</h1>
          <p className="text-sm mt-2" style={{ color: "var(--muted)" }}>{this.state.message}</p>
          <button className="lx-btn lx-btn-primary mt-5" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      </div>
    );
  }
}
