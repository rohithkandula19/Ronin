/**
 * The Ronin ensō (円相) — a single, nearly-closed ink circle that draws itself
 * on load. The signature mark of the "Sumi" identity. Pure SVG + CSS (no JS),
 * so it renders in a server component and respects reduced-motion.
 */
export function Enso({ className = "", size = 440 }: { className?: string; size?: number }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 200 200"
      fill="none"
      aria-hidden
    >
      {/* faint ink bleed underneath */}
      <circle
        className="enso-ink"
        cx="100"
        cy="100"
        r="74"
        stroke="var(--rds-accent-soft)"
        strokeWidth="20"
        strokeLinecap="round"
        pathLength={100}
        strokeDasharray="92 100"
        transform="rotate(-88 100 100)"
        style={{ filter: "blur(6px)" }}
      />
      {/* the drawn stroke */}
      <circle
        className="enso-stroke"
        cx="100"
        cy="100"
        r="74"
        stroke="var(--rds-accent)"
        strokeWidth="11"
        strokeLinecap="round"
        pathLength={100}
        transform="rotate(-88 100 100)"
      />
    </svg>
  );
}
