import { ChevronDown, Plus } from 'lucide-react';
import { type ReactNode, useEffect, useId, useRef, useState } from 'react';

type WorkDropdownProps = {
  label: ReactNode;
  children: ReactNode;
  primary?: boolean;
  closeOnAction?: boolean;
};

/** A disclosure of ordinary controls, with native keyboard navigation. */
export function WorkDropdown({
  label,
  children,
  primary = false,
  closeOnAction = false,
}: WorkDropdownProps) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: Event) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      setOpen(false);
      trigger.current?.focus();
    };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('focusin', closeOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('focusin', closeOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [open]);

  return (
    <div className="work-dropdown" ref={root}>
      <button
        type="button"
        ref={trigger}
        className={`work-dropdown-trigger ${primary ? 'primary' : 'secondary'}`}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((current) => !current)}
      >
        {primary && <Plus aria-hidden="true" />}
        {label}
        <ChevronDown aria-hidden="true" />
      </button>
      {open && (
        // Focus the surviving trigger before the action opens its modal. The modal then owns
        // focus, and can restore it to that trigger when dismissed.
        // biome-ignore lint/a11y/useKeyWithClickEvents: Keyboard activation of child buttons also bubbles a click.
        // biome-ignore lint/a11y/noStaticElementInteractions: This container only observes clicks on its native buttons.
        <div
          className="work-dropdown-panel"
          id={panelId}
          onClickCapture={(event) => {
            if (
              closeOnAction &&
              event.target instanceof Element &&
              event.target.closest('button:not([disabled])')
            ) {
              trigger.current?.focus({ preventScroll: true });
            }
          }}
          onClick={(event) => {
            if (
              closeOnAction &&
              event.target instanceof Element &&
              event.target.closest('button:not([disabled])')
            ) {
              setOpen(false);
            }
          }}
        >
          {children}
        </div>
      )}
    </div>
  );
}
