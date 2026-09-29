const inrFormatter = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
const compactInr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", notation: "compact", maximumFractionDigits: 1 });

export function inr(amount: number | null | undefined): string {
  if (amount === null || amount === undefined) return "—";
  return inrFormatter.format(amount).replace(/\.00$/, "");
}

export function inrCompact(amount: number): string {
  return compactInr.format(amount);
}

export function time(value: string): string {
  return new Date(value).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function dateTime(value: string): string {
  return new Date(value).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function relative(value: string): string {
  const seconds = Math.round((Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 60) return `${Math.max(seconds, 0)}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function humanize(value: string | null | undefined): string {
  if (!value) return "—";
  return value
    .toLowerCase()
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

const usdFormatter = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

/** Currency-aware amount. PayPal Sandbox processes USD; INR is shown only as a demo equivalent. */
export function money(amount: number | null | undefined, currency: string | null | undefined = "INR"): string {
  if (amount === null || amount === undefined) return "—";
  if (currency === "USD") return usdFormatter.format(amount);
  if (!currency || currency === "INR") return inr(amount);
  return `${amount.toFixed(2)} ${currency}`;
}

export function inrEquivalent(amount: number, currency: string, rate = 84): string | null {
  return currency === "USD" ? `≈ ${inr(Math.round(amount * rate))} demo equivalent` : null;
}

export function providerLabel(provider: string | null | undefined): string {
  switch (provider) {
    case "PAYPAL_SANDBOX":
      return "PayPal Sandbox";
    case "PAYFLOW_HISTORICAL":
      return "Historical PayFlow";
    case "SYNTHETIC":
      return "Synthetic";
    default:
      return provider ?? "—";
  }
}
