"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { type AIStatus, getAIStatus } from "@/lib/api";

// Which model writes drafts right now. Fetched once per signed-in session
// and on demand (the server caches its reachability probe briefly).

type State = { status: AIStatus | null; refresh: () => void };

const AIStatusContext = createContext<State>({ status: null, refresh: () => undefined });

export function useAIStatus(): State {
  return useContext(AIStatusContext);
}

export function AIStatusProvider({ children }: { children: React.ReactNode }) {
  const { session } = useAuth();
  const [status, setStatus] = useState<AIStatus | null>(null);

  const refresh = useCallback(() => {
    getAIStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    if (session) refresh();
    else setStatus(null);
  }, [session, refresh]);

  return <AIStatusContext.Provider value={{ status, refresh }}>{children}</AIStatusContext.Provider>;
}

const DOT: Record<AIStatus["mode"], string> = {
  fine_tuned: "bg-emerald-500",
  openai: "bg-brand-500",
  mock: "bg-amber-400",
  unavailable: "bg-red-500",
};

const SHORT: Record<AIStatus["mode"], string> = {
  fine_tuned: "Fine-tuned model",
  openai: "OpenAI",
  mock: "Demo generator",
  unavailable: "Model offline",
};

export function ModelStatusPill({ status }: { status: AIStatus | null }) {
  if (!status) return null;
  return (
    <span className="inline-flex items-center gap-2 text-xs font-medium text-slate-700" title={status.detail}>
      <span className={`h-2 w-2 rounded-full ${DOT[status.mode]}`} aria-hidden />
      {SHORT[status.mode]}
    </span>
  );
}

// The model that wrote a stored draft, from its recorded `model_used`.
export function draftModelLabel(modelUsed: string | null | undefined): string {
  if (!modelUsed) return "Unknown";
  if (modelUsed === "qwen3-4b-lora-v1") return "Fine-tuned model";
  if (modelUsed === "mock") return "Demo generator";
  if (modelUsed === "openai") return "OpenAI";
  if (modelUsed === "human_edit") return "Edited by a person";
  return modelUsed;
}
