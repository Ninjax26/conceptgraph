import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
}

export default class AppErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Dashboard render failed", error, info);
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <main className="grid min-h-[calc(100vh-64px)] place-items-center p-6">
          <div className="max-w-md rounded-md border border-red-200 bg-red-50 p-5 text-center">
            <h1 className="text-base font-semibold text-red-900">The dashboard hit an unexpected error</h1>
            <p className="mt-2 text-sm text-red-700">Your documents are safe. Reload the page to reconnect.</p>
            <button className="mt-4 rounded-md bg-red-700 px-4 py-2 text-sm font-semibold text-white hover:bg-red-800" onClick={() => window.location.reload()} type="button">
              Reload dashboard
            </button>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}
