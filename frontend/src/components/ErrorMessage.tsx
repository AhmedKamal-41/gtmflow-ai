import { Icon } from "./Icon";

export function ErrorMessage({ children }: { children: React.ReactNode }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2.5 rounded-lg border border-red-200 bg-red-50 p-3.5 text-sm text-red-800"
    >
      <Icon name="alert" className="h-4 w-4 flex-none translate-y-0.5" />
      <div>{children}</div>
    </div>
  );
}
