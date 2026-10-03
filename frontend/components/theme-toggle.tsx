"use client";

import { Moon, Sun } from "lucide-react";
import { useSyncExternalStore } from "react";

import { THEME_STORAGE_KEY as STORAGE_KEY } from "@/lib/theme";

type Theme = "light" | "dark";
const listeners = new Set<() => void>();
const media = () => window.matchMedia("(prefers-color-scheme: dark)");

function currentTheme(): Theme {
  const explicit = document.documentElement.dataset.theme;
  if (explicit === "light" || explicit === "dark") return explicit;
  return media().matches ? "dark" : "light";
}

function subscribe(callback: () => void) {
  listeners.add(callback);
  const mq = media();
  mq.addEventListener("change", callback);
  return () => {
    listeners.delete(callback);
    mq.removeEventListener("change", callback);
  };
}

function setTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* storage blocked: the choice still applies for this page view */
  }
  listeners.forEach((l) => l());
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, currentTheme, () => null);
  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      className="flex size-9 items-center justify-center rounded-full border border-border bg-panel text-muted transition-colors hover:text-foreground"
      aria-label={theme ? `Switch to ${next} theme` : "Toggle theme"}
      title={theme ? `Switch to ${next} theme` : "Toggle theme"}
    >
      {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </button>
  );
}
