import type { Wreck } from "../types";

interface Props {
  wreck: Wreck;
  onBack: () => void;
}

function MagBar({ value, max }: { value: number | null; max: number }) {
  if (value == null) return <span style={{ color: "var(--text-dim)" }}>—</span>;
  const pct = Math.min(100, (Math.abs(value) / max) * 100);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div className="mag-bar" style={{ width: 120 }}>
        <div className="mag-bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <span style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{value.toFixed(1)} nT</span>
    </div>
  );
}

export default function WreckDetail({ wreck, onBack }: Props) {
  const w = wreck;
  return (
    <div className="detail-panel">
      <button onClick={onBack} style={{
        background: "none", border: "1px solid var(--border)", color: "var(--text-dim)",
        padding: "4px 12px", borderRadius: 6, cursor: "pointer", marginBottom: 12, fontSize: 12,
      }}>
        &larr; Back to list
      </button>

      <h2>{w.name || "Unknown Wreck"}</h2>
      {w.date && <div style={{ color: "var(--text-dim)", marginBottom: 16 }}>{w.date}</div>}

      <dl className="detail-grid">
        <dt>ID</dt><dd>{w.id}</dd>
        <dt>Type</dt><dd>{w.feature_type || "—"}</dd>
        <dt>Hull Material</dt><dd>{w.hull_material || "—"}</dd>
        <dt>Size</dt><dd>{w.size_category || "—"}</dd>
        <dt>Salvage</dt><dd>{w.salvage_status || "—"}</dd>
        <dt>Source</dt><dd>{w.source || "—"}</dd>
        <dt>Depth</dt><dd>{w.depth != null ? `${w.depth} ft` : "—"}</dd>
        <dt>Latitude</dt><dd>{w.latitude != null ? w.latitude.toFixed(5) : "—"}</dd>
        <dt>Longitude</dt><dd>{w.longitude != null ? w.longitude.toFixed(5) : "—"}</dd>

        <dt>Location Quality</dt>
        <dd>
          {(() => {
            const q = w.coord_quality;
            if (q === "dive_verified") return <span className="badge badge-coord-verified">✓ Dive Verified</span>;
            if (q === "swayze_parsed") return <span className="badge badge-coord-estimated">⚠ Estimated (Swayze)</span>;
            if (q === "place_estimated") return <span className="badge badge-coord-estimated">⚠ Estimated (place name)</span>;
            if (q === "lake_center") return <span className="badge badge-coord-warn">⚠ Lake Center — true position unknown</span>;
            return <span className="badge badge-coord-none">{q || "unknown"}</span>;
          })()}
        </dd>

        <dt>Magnetic Potential</dt>
        <dd>
          <span className={`badge badge-${w.magnetic_potential ?? "weak"}`}>
            {w.magnetic_potential || "unknown"}
          </span>
          {w.is_steel_freighter ? <span className="badge badge-steel" style={{ marginLeft: 6 }}>steel freighter</span> : null}
          {w.is_iron_ore_carrier ? <span className="badge badge-ore" style={{ marginLeft: 6 }}>ore carrier</span> : null}
        </dd>

        <dt>Confidence</dt>
        <dd>{w.training_confidence != null ? `${(w.training_confidence * 100).toFixed(0)}%` : "—"}</dd>
      </dl>

      {(w.mag_mean != null || w.mag_max != null) && (
        <div style={{ marginTop: 20 }}>
          <h3 style={{ fontSize: 14, marginBottom: 8 }}>NAMAG Magnetic Signature</h3>
          <dl className="detail-grid">
            <dt>Mean</dt><dd><MagBar value={w.mag_mean ?? null} max={500} /></dd>
            <dt>Max</dt><dd><MagBar value={w.mag_max ?? null} max={1000} /></dd>
            <dt>Min</dt><dd><MagBar value={w.mag_min ?? null} max={1000} /></dd>
            <dt>Std Dev</dt><dd style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{w.mag_std?.toFixed(2) ?? "—"}</dd>
            <dt>AS Peak</dt><dd style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{w.mag_as_peak?.toFixed(2) ?? "—"}</dd>
            <dt>VD Peak</dt><dd style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{w.mag_vd_peak?.toFixed(2) ?? "—"}</dd>
            <dt>TMI Peak</dt><dd style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{w.mag_tmi_peak?.toFixed(2) ?? "—"}</dd>
            <dt>Spike Width</dt><dd style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{w.mag_spike_w_m != null ? `${w.mag_spike_w_m.toFixed(0)} m` : "—"}</dd>
            <dt>Polarity</dt><dd>{w.mag_polarity || "—"}</dd>
            <dt>Label</dt><dd>{w.mag_label === 1 ? "Positive anomaly" : w.mag_label === 0 ? "No anomaly" : "—"}</dd>
          </dl>
        </div>
      )}

      {w.historical_place_names && (
        <div style={{ marginTop: 12 }}>
          <h3 style={{ fontSize: 14, marginBottom: 8 }}>Historical Place Names</h3>
          <div style={{ fontSize: 13, color: "var(--text-dim)" }}>{w.historical_place_names}</div>
        </div>
      )}
    </div>
  );
}
