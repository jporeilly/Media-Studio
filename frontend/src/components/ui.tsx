/**
 * Shared UI primitives. Keep them small: pages compose these.
 * House style: dark theme first, no borders on selects, clearly visible buttons.
 *
 * These are the generic primitives copied from the OpenSight design system. The
 * domain-specific pieces (health rings, band/stage/CTA chips, KPI cards) are left
 * out of this rebuild until the matching features land.
 */
import { type ReactNode, useEffect, type ButtonHTMLAttributes, type SelectHTMLAttributes, type InputHTMLAttributes, type TextareaHTMLAttributes } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertTriangle, HelpCircle, Loader2, X } from "lucide-react";

// ── Buttons & inputs ────────────────────────────────────────────────
type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger"; size?: "sm" | "md"; icon?: ReactNode };

export function Button({ variant = "secondary", size = "md", icon, children, className = "", ...rest }: BtnProps) {
  return (
    <button className={`os-btn os-btn-${variant} os-btn-${size} ${className}`} {...rest}>
      {icon && <span className="os-btn-icon">{icon}</span>}
      {children}
    </button>
  );
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  const { className = "", ...rest } = props;
  return <select className={`os-select ${className}`} {...rest} />;
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  const { className = "", ...rest } = props;
  return <input className={`os-input ${className}`} {...rest} />;
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const { className = "", ...rest } = props;
  return <textarea className={`os-input os-textarea ${className}`} {...rest} />;
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="os-field">
      <span className="os-field-label">{label}</span>
      {children}
      {hint && <span className="os-field-hint">{hint}</span>}
    </label>
  );
}

// ── Layout pieces ───────────────────────────────────────────────────
export function Card({ children, className = "", title, actions, subtitle, style }: { children: ReactNode; className?: string; title?: ReactNode; actions?: ReactNode; subtitle?: ReactNode; style?: React.CSSProperties }) {
  return (
    <section className={`os-card ${className}`} style={style}>
      {(title || actions) && (
        <header className="os-card-header">
          <div>
            {title && <div className="os-title-row"><h3 className="os-card-title">{title}</h3></div>}
            {subtitle && <div className="os-card-subtitle">{subtitle}</div>}
          </div>
          {actions && <div className="os-card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="os-page-header">
      <div>
        <div className="os-title-row"><h1 className="os-page-title">{title}</h1></div>
        {subtitle && <div className="os-page-subtitle">{subtitle}</div>}
      </div>
      {actions && <div className="os-page-actions">{actions}</div>}
    </div>
  );
}

export function EmptyState({ icon, title, sub, action }: { icon?: ReactNode; title: string; sub?: string; action?: ReactNode }) {
  return (
    <div className="os-empty">
      <div className="os-empty-icon">{icon || <HelpCircle size={36} />}</div>
      <div className="os-empty-title">{title}</div>
      {sub && <div className="os-empty-sub">{sub}</div>}
      {action && <div className="os-empty-action">{action}</div>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="os-spinner">
      <Loader2 className="os-spin" size={20} />
      {label && <span>{label}</span>}
    </div>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="os-error">
      <AlertTriangle size={16} /> <span>{message}</span>
    </div>
  );
}

export function Modal({ open, onClose, title, children, width = 560, footer }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; width?: number; footer?: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="os-modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="os-modal" style={{ width, maxWidth: "95vw" }} role="dialog" aria-modal="true">
        <header className="os-modal-header">
          <h3>{title}</h3>
          <button className="os-icon-btn" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </header>
        <div className="os-modal-body">{children}</div>
        {footer && <footer className="os-modal-footer">{footer}</footer>}
      </div>
    </div>
  );
}

export function Tabs({ tabs, active, onChange }: { tabs: { key: string; label: ReactNode; count?: number }[]; active: string; onChange: (k: string) => void }) {
  return (
    <div className="os-tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.key} role="tab" aria-selected={active === t.key} className={`os-tab ${active === t.key ? "active" : ""}`} onClick={() => onChange(t.key)}>
          {t.label}
          {t.count !== undefined && <span className="os-tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

// ── Data display ────────────────────────────────────────────────────
export function Avatar({ name, size = 28, color }: { name: string | null | undefined; size?: number; color?: string }) {
  const parts = (name || "?").split(" ").filter(Boolean);
  const text = parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : (parts[0] || "?").slice(0, 2);
  return <span className="os-avatar" style={{ width: size, height: size, fontSize: size * 0.4, background: color || "var(--brand)" }}>{text.toUpperCase()}</span>;
}

export function Markdown({ text, className = "" }: { text: string | null | undefined; className?: string }) {
  return (
    <div className={`os-markdown ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text || ""}</ReactMarkdown>
    </div>
  );
}

/** Table with consistent header styling; rows come from the caller. */
export function Table({ headers, children, className = "" }: { headers: ReactNode[]; children: ReactNode; className?: string }) {
  return (
    <div className={`os-table-wrap ${className}`}>
      <table className="os-table">
        <thead>
          <tr>{headers.map((h, i) => <th key={i}>{h}</th>)}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
