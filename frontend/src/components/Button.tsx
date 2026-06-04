"use client";

import { Icon, type IconName } from "./Icon";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

type Props = {
  children: React.ReactNode;
  onClick?: () => void;
  type?: "button" | "submit";
  variant?: Variant;
  size?: Size;
  icon?: IconName;
  loading?: boolean;
  disabled?: boolean;
  fullWidth?: boolean;
};

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-brand-600 text-white shadow-sm hover:bg-brand-700 active:bg-brand-800 disabled:bg-brand-300",
  secondary:
    "border border-slate-300 bg-white text-slate-800 hover:bg-slate-50 hover:border-slate-400 disabled:opacity-60",
  ghost:
    "text-slate-700 hover:bg-slate-100 disabled:opacity-60 disabled:hover:bg-transparent",
  danger:
    "bg-red-600 text-white shadow-sm hover:bg-red-700 active:bg-red-800 disabled:bg-red-300",
};

const SIZES: Record<Size, string> = {
  sm: "px-2.5 py-1.5 text-xs gap-1.5",
  md: "px-4 py-2 text-sm gap-2",
  lg: "px-5 py-2.5 text-sm gap-2",
};

export function Button({
  children,
  onClick,
  type = "button",
  variant = "primary",
  size = "md",
  icon,
  loading,
  disabled,
  fullWidth,
}: Props) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={loading || disabled}
      className={`inline-flex items-center justify-center rounded-lg font-medium transition-colors duration-150 disabled:cursor-not-allowed motion-safe:active:scale-[0.98] ${
        SIZES[size]
      } ${VARIANTS[variant]} ${fullWidth ? "w-full" : ""}`}
    >
      {loading ? (
        <Spinner />
      ) : (
        icon && <Icon name={icon} className="h-4 w-4" />
      )}
      {children}
    </button>
  );
}

function Spinner() {
  return (
    <svg
      className="h-4 w-4 animate-spin"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeOpacity="0.25"
        strokeWidth="4"
      />
      <path d="M4 12a8 8 0 0 1 8-8" stroke="currentColor" strokeWidth="4" />
    </svg>
  );
}
