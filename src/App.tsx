import { useState } from "react";
import { Network, ArrowLeft } from "lucide-react";

import Home from "@/pages/Home";
import Dashboard from "@/pages/Dashboard";

export type Page = "home" | "dashboard";

export default function App(): JSX.Element {
  const [page, setPage] = useState<Page>("home");

  return (
    <div className="min-h-screen bg-white dark:bg-[#0B0B0F]">
      <nav className="fixed inset-x-0 top-0 z-50 flex h-16 items-center border-b border-slate-200/70 bg-white/85 px-4 shadow-sm backdrop-blur lg:px-6 dark:border-white/10 dark:bg-[#0B0B0F]/80">
        <div className="flex w-full items-center justify-between">
          <div className="flex items-center gap-3">
            {page !== "home" && (
              <button
                onClick={() => setPage("home")}
                className="mr-2 grid h-8 w-8 place-items-center rounded-md border border-slate-200 text-slate-500 transition hover:bg-slate-100 hover:text-ink dark:border-white/10 dark:text-slate-400 dark:hover:bg-white/10 dark:hover:text-white"
                aria-label="Go back to home"
              >
                <ArrowLeft className="h-4 w-4" />
              </button>
            )}
            <button
              onClick={() => setPage("home")}
              className="flex items-center gap-3"
            >
              <div className="grid h-9 w-9 place-items-center rounded-md bg-ink text-sm font-bold text-white dark:bg-teal-400 dark:text-slate-950">
                CG
              </div>
              <div className="text-left">
                <p className="text-sm font-semibold text-ink dark:text-white">
                  ConceptGraph AI Pipeline
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Academic graph retrieval dashboard
                </p>
              </div>
            </button>
          </div>

          <div className="flex items-center gap-2">
            {page === "home" && (
              <button
                onClick={() => setPage("dashboard")}
                className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-ink shadow-sm transition hover:bg-slate-50 dark:border-white/10 dark:bg-white/5 dark:text-white dark:hover:bg-white/10"
              >
                <Network className="h-3.5 w-3.5" />
                Open Dashboard
              </button>
            )}
          </div>
        </div>
      </nav>
      <div className="pt-16">
        {page === "home" && <Home navigate={setPage} />}
        {page === "dashboard" && <Dashboard />}
      </div>
    </div>
  );
}
