type Item = { value: string; label: string; title?: string }

/** Radio group for switching between chat and management in the admin UI. */
export function Segmented({ label, items, value, onChange, disabled }: { label: string; items: Item[]; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      {items.map((it) => (
        <button
          key={it.value}
          type="button"
          role="radio"
          aria-checked={it.value === value}
          title={it.title}
          disabled={disabled}
          onClick={() => onChange(it.value)}
        >
          {it.label}
        </button>
      ))}
    </div>
  )
}
