/**
 * Lexora brand mark — inline SVG so it needs no external fetch and always
 * matches the favicon (public/icon.svg): a blue → indigo gradient "L" monogram,
 * optionally paired with the "Lexora HRM" wordmark (and the tagline for large placements).
 */
interface Props {
  className?: string;
  /** Monogram only (collapsed sidebar, small badges). */
  monogram?: boolean;
  /** Full lockup with the "Canadian HR & payroll" tagline (for large placements only). */
  tagline?: boolean;
  /** White wordmark for dark backgrounds (splash screen). */
  onDark?: boolean;
  alt?: string;
}

// The Lexora brand gradient (blue → indigo → light indigo).
function Gradient({ id }: { id: string }) {
  return (
    <linearGradient id={id} x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stopColor="#2563eb" />
      <stop offset="50%" stopColor="#4f46e5" />
      <stop offset="100%" stopColor="#6366f1" />
    </linearGradient>
  );
}

export function Monogram({ className = "w-10 h-10", title = "Lexora HRM" }: { className?: string; title?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} role="img" aria-label={title}>
      <defs><Gradient id="lx-mono-grad" /></defs>
      <rect x="2" y="2" width="60" height="60" rx="16" fill="url(#lx-mono-grad)" />
      <text x="32" y="47" textAnchor="middle" fontFamily="'Bogart Alt', Georgia, serif"
        fontWeight={700} fontSize={40} fill="#ffffff">L</text>
    </svg>
  );
}

export default function Logo({ className = "h-10 w-auto", monogram = false, tagline = false, onDark = false, alt = "Lexora HRM" }: Props) {
  if (monogram) return <Monogram className={className} title={alt} />;
  const label = tagline ? `${alt} — Canadian HR & payroll` : alt;
  const gid = `lx-word-grad${tagline ? "-t" : ""}${onDark ? "-d" : ""}`;
  // Height-controlled wordmark lockup: monogram + "Lexora" in the brand gradient + "HRM".
  return (
    <svg viewBox={tagline ? "0 0 268 78" : "0 0 268 56"} className={className} role="img" aria-label={label}>
      <defs><Gradient id={gid} /></defs>
      <rect x="1" y="1" width="54" height="54" rx="14" fill={`url(#${gid})`} />
      <text x="28" y="42" textAnchor="middle" fontFamily="'Bogart Alt', Georgia, serif"
        fontWeight={700} fontSize={36} fill="#ffffff">L</text>
      <text x="66" y="39" fontFamily="'Bogart Alt', Georgia, serif" fontWeight={700}
        fontSize={34} fill={onDark ? "#ffffff" : `url(#${gid})`} letterSpacing="-0.5">Lexora</text>
      <text x="198" y="39" fontFamily="'MD System Narrow', Arial, sans-serif" fontWeight={700}
        fontSize={22} fill={onDark ? "#c7d2fe" : "#6366f1"} letterSpacing="1">HRM</text>
      {tagline && (
        <text x="134" y="73" textAnchor="middle" fontFamily="'MD System Narrow', Arial, sans-serif" fontWeight={600}
          fontSize={12} fill={onDark ? "#c7d2fe" : "#64748b"} letterSpacing="2.5">CANADIAN HR &amp; PAYROLL</text>
      )}
    </svg>
  );
}
