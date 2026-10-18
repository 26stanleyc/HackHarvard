import type { ReactNode } from "react";
import { API_BASE, API_MODE } from "@/lib/api";
import { loadTypeColor, loadTypeLabel } from "@/lib/bids";

/** Persistent label shown whenever numbers come from fixtures rather than the engine. */
export function FixtureBadge() {
  if (API_MODE !== "fixture") {
    return (
      <span
        data-testid="fixture-badge"
        data-mode="live"
        className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-line bg-surface px-2.5 py-1 text-[11px] font-medium text-ink-2"
        title={`Live API: ${API_BASE}`}
      >
        <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
        Live API
      </span>
    );
  }
  return (
    <span
      data-testid="fixture-badge"
      data-mode="fixture"
      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-warn/40 bg-warn-soft px-2.5 py-1 text-[11px] font-semibold text-warn"
      title="Numbers come from recorded backend responses in fixtures/ (example scenario only), not from a live engine. Unset NEXT_PUBLIC_API_MODE (or set it to live) to use the backend."
    >
      <span className="h-1.5 w-1.5 rounded-full bg-warn" aria-hidden />
      Fixture mode — recorded data
    </span>
  );
}

/** Small "where this number comes from" caption. */
export function Source({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <p className={`font-mono text-[11px] leading-relaxed text-muted ${className}`}>
      <span className="mr-1 uppercase tracking-wider text-muted/80">source</span>
      {children}
    </p>
  );
}

/** Closed-by-default box for method notes and secondary stats, so the main view stays short. */
export function Details({ children, title = "How we got these numbers" }: { children: ReactNode; title?: string }) {
  return (
    <details className="card group" data-testid="details">
      <summary className="cursor-pointer select-none px-4 py-3 text-sm font-medium text-ink-2 hover:text-ink sm:px-5">
        {title}
      </summary>
      <div className="space-y-6 border-t border-line p-4 sm:p-5">{children}</div>
    </details>
  );
}

export function SectionTitle({
  kicker,
  title,
  children,
}: {
  kicker?: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="mb-5 max-w-3xl">
      {kicker && <div className="kicker mb-2">{kicker}</div>}
      <h2 className="font-serif text-[26px] leading-tight font-semibold tracking-tight text-ink sm:text-[30px]">
        {title}
      </h2>
      {children && <div className="mt-2 text-[15px] leading-relaxed text-ink-2">{children}</div>}
    </div>
  );
}

export function Stat({
  label,
  value,
  sub,
  tone = "default",
  testId,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "default" | "good" | "bad" | "muted";
  testId?: string;
}) {
  const color =
    tone === "good"
      ? "text-accent"
      : tone === "bad"
        ? "text-danger"
        : tone === "muted"
          ? "text-muted"
          : "text-ink";
  return (
    <div className="min-w-0" data-testid={testId}>
      <div className="text-[11px] font-medium uppercase tracking-wider text-muted">{label}</div>
      <div className={`num mt-1 text-xl font-medium sm:text-2xl ${color}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
  testId,
  type = "button",
  className = "",
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "ghost";
  disabled?: boolean;
  testId?: string;
  type?: "button" | "submit";
  className?: string;
  title?: string;
}) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50";
  const v =
    variant === "primary"
      ? "bg-accent text-white hover:bg-accent-ink"
      : variant === "secondary"
        ? "border border-line-strong bg-surface text-ink hover:bg-surface-2"
        : "text-ink-2 hover:bg-surface-2";
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      title={title}
      className={`${base} ${v} ${className}`}
    >
      {children}
    </button>
  );
}

export function ErrorBanner({
  title,
  message,
  items,
  onDismiss,
}: {
  title: string;
  message: string;
  items?: { label: string; message: string }[];
  onDismiss?: () => void;
}) {
  return (
    <div
      role="alert"
      data-testid="error-banner"
      className="rounded-lg border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-semibold">{title}</div>
          <div className="mt-0.5 break-words text-danger/90">{message}</div>
          {items && items.length > 0 && (
            <ul className="mt-2 space-y-1">
              {items.map((it, i) => (
                <li key={i} className="break-words">
                  <span className="font-medium">{it.label}:</span> {it.message}
                </li>
              ))}
            </ul>
          )}
        </div>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="shrink-0 rounded px-1.5 text-danger/70 hover:text-danger"
            aria-label="Dismiss error"
          >
            ×
          </button>
        )}
      </div>
    </div>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  testIdPrefix,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
  testIdPrefix?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex max-w-full flex-wrap rounded-md border border-line bg-surface-2 p-0.5"
    >
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          data-testid={testIdPrefix ? `${testIdPrefix}-${o.value}` : undefined}
          onClick={() => onChange(o.value)}
          className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
            value === o.value ? "bg-surface text-ink shadow-sm" : "text-muted hover:text-ink"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Small load-type tag (label + muted type color). Unknown types get a neutral tag. */
export function LoadTypeTag({ type, className = "" }: { type: string; className?: string }) {
  const color = loadTypeColor(type);
  return (
    <span
      data-testid="load-type-tag"
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-px text-[10px] font-medium ${className}`}
      style={{ color, borderColor: `${color}55`, background: `${color}12` }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} aria-hidden />
      {loadTypeLabel(type)}
    </span>
  );
}
