import { useState, useEffect, useRef } from "react";
import { startSensorRun, getSensorRunStatus, listSensorReports } from "../services/api";
import type { ToolJobStatus } from "../types";

const SENSOR_LIST = [
  { key: "sar", label: "SAR Backscatter", desc: "Sentinel-1 C-band multi-temporal backscatter + curvelet denoising" },
  { key: "icesat2", label: "ICESat-2 ATL13", desc: "Water surface elevation persistent anomaly" },
  { key: "goce", label: "GOCE Gravity", desc: "Free-air gravity gradient azimuth concordance" },
  { key: "enc", label: "NOAA ENC", desc: "Charted obstruction cross-reference" },
  { key: "sentinel2", label: "Sentinel-2 Clarity", desc: "Water clarity ratio / mussel plume detector" },
  { key: "turbidity", label: "Turbidity (Kd490)", desc: "GLERL/VIIRS survey-timing optimizer" },
  { key: "legacy-bathy", label: "Legacy Bathymetry", desc: "NCEI survey index + curvelet substrate analysis" },
  { key: "atl23", label: "ATL23 Bathy", desc: "ICESat-2 ATL23 bathymetric bottom detection" },
  { key: "thermal", label: "Thermal IR", desc: "Landsat TIRS thermal anomaly (buried structure)" },
  { key: "coherence", label: "SAR Coherence", desc: "InSAR interferometric coherence (rigid vs soft substrate)" },
];

interface SensorResult {
  sensor: string;
  anomaly_detected: boolean;
  value: number;
  units: string;
  description: string;
}

interface Report {
  file: string;
  candidate: Record<string, unknown>;
  sensors_total: number;
  sensors_flagged: number;
  generated: string;
}

export default function ExtendedSensorsPanel() {
  const [lat, setLat] = useState("42.4250");
  const [lon, setLon] = useState("-80.8130");
  const [amplitudeNt, setAmplitudeNt] = useState("305");
  const [depthM, setDepthM] = useState("24");
  const [label, setLabel] = useState("Tier1-ERIE-MB2");
  const [dryRun, setDryRun] = useState(true);
  const [earthdataToken, setEarthdataToken] = useState("");
  const [jobs, setJobs] = useState<ToolJobStatus[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load existing reports on mount
  useEffect(() => {
    listSensorReports().then(r => setReports(r.reports)).catch(() => {});
  }, []);

  // Poll active jobs
  useEffect(() => {
    const active = jobs.filter(j => j.status === "queued" || j.status === "running");
    if (active.length === 0) {
      if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; }
      return;
    }
    if (pollingRef.current) return;
    pollingRef.current = setInterval(async () => {
      const updated = await Promise.all(
        jobs.map(async j => {
          if (j.status === "completed" || j.status === "failed") return j;
          try {
            const s = await getSensorRunStatus(j.id);
            // Refresh reports on completion
            if (s.status === "completed" && j.status !== "completed") {
              listSensorReports().then(r => setReports(r.reports)).catch(() => {});
            }
            return { ...j, ...s };
          } catch { return j; }
        })
      );
      setJobs(updated);
    }, 3000);
    return () => { if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; } };
  }, [jobs]);

  const handleRun = async () => {
    const latN = parseFloat(lat);
    const lonN = parseFloat(lon);
    if (isNaN(latN) || isNaN(lonN)) {
      setError("Enter valid coordinates.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const job = await startSensorRun({
        lat: latN,
        lon: lonN,
        amplitude_nt: parseFloat(amplitudeNt) || 0,
        depth_m: parseFloat(depthM) || 24,
        label: label || "GUI-candidate",
        dry_run: dryRun,
        earthdata_token: earthdataToken || undefined,
      });
      setJobs(prev => [{
        id: job.job_id, tool: "extended_sensors", status: job.status,
        created: Date.now() / 1000,
      }, ...prev]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  // Extract sensor results from the latest completed job
  const latestCompleted = jobs.find(j => j.status === "completed");
  const sensorResults: SensorResult[] = (latestCompleted?.result as any)?.sensors ?? [];

  return (
    <div>
      <h2 style={{ marginBottom: 4 }}>Extended Sensors</h2>
      <p style={{ color: "var(--text-dim)", fontSize: 13, marginBottom: 16 }}>
        Multi-physics remote-sensing cross-validation: run 10 independent satellite
        observables against a candidate wreck location.
      </p>

      {/* ── Available sensors ──────────────────────────── */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.04em" }}>
          Available Sensors ({SENSOR_LIST.length})
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))", gap: 8 }}>
          {SENSOR_LIST.map(s => (
            <div key={s.key} style={{
              padding: "8px 12px", background: "var(--bg-card)", border: "1px solid var(--border)",
              borderRadius: 6, fontSize: 12,
            }}>
              <span style={{ fontWeight: 600 }}>{s.label}</span>
              <div style={{ color: "var(--text-dim)", fontSize: 11, marginTop: 2 }}>{s.desc}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Run configuration ──────────────────────────── */}
      <div className="tool-form" style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>Run All Sensors</div>

        <div style={{ display: "flex", gap: 12 }}>
          <div className="form-row" style={{ flex: 1 }}>
            <label className="form-label">Latitude</label>
            <input className="form-input" type="text" value={lat} onChange={e => setLat(e.target.value)}
              placeholder="42.4250" style={{ fontFamily: "var(--mono)" }} />
          </div>
          <div className="form-row" style={{ flex: 1 }}>
            <label className="form-label">Longitude</label>
            <input className="form-input" type="text" value={lon} onChange={e => setLon(e.target.value)}
              placeholder="-80.8130" style={{ fontFamily: "var(--mono)" }} />
          </div>
        </div>

        <div style={{ display: "flex", gap: 12 }}>
          <div className="form-row" style={{ flex: 1 }}>
            <label className="form-label">Mag Amplitude (nT)</label>
            <input className="form-input" type="text" value={amplitudeNt} onChange={e => setAmplitudeNt(e.target.value)}
              placeholder="305" style={{ fontFamily: "var(--mono)" }} />
            <div className="form-hint">0 for non-magnetic targets</div>
          </div>
          <div className="form-row" style={{ flex: 1 }}>
            <label className="form-label">Depth (m)</label>
            <input className="form-input" type="text" value={depthM} onChange={e => setDepthM(e.target.value)}
              placeholder="24" style={{ fontFamily: "var(--mono)" }} />
          </div>
        </div>

        <div className="form-row">
          <label className="form-label">Label</label>
          <input className="form-input" type="text" value={label} onChange={e => setLabel(e.target.value)}
            placeholder="Tier1-ERIE-MB2" />
        </div>

        <div className="form-row">
          <label className="form-label">NASA Earthdata Token (optional)</label>
          <input className="form-input" type="password" value={earthdataToken}
            onChange={e => setEarthdataToken(e.target.value)}
            placeholder="Bearer token for SAR + ICESat-2 downloads" />
          <div className="form-hint">Required for live data download. Leave blank for cached/dry-run mode.</div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, cursor: "pointer" }}>
            <input type="checkbox" checked={dryRun} onChange={e => setDryRun(e.target.checked)} />
            Dry run (use cached data only, no downloads)
          </label>
        </div>

        {error && <div className="error-banner">{error}</div>}

        <button className="btn-primary" onClick={handleRun} disabled={submitting}>
          {submitting ? "Launching…" : "Run All 10 Sensors"}
        </button>
      </div>

      {/* ── Active & completed jobs ─────────────────────── */}
      {jobs.length > 0 && (
        <div className="tool-form" style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Sensor Jobs</div>
          {jobs.map(j => (
            <div key={j.id} style={{
              padding: "8px 12px", border: "1px solid var(--border)",
              borderRadius: 6, marginBottom: 8, fontSize: 13,
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                <span className={`badge badge-${j.status === "completed" ? "strong" : j.status === "failed" ? "weak" : "moderate"}`}>
                  {j.status}
                </span>
                <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--text-dim)" }}>
                  {j.id.slice(0, 8)}…
                </span>
              </div>
              {j.status === "failed" && j.error && (
                <div style={{ color: "var(--red)", fontSize: 12, marginTop: 4 }}>{j.error}</div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ── Sensor results from latest run ──────────────── */}
      {sensorResults.length > 0 && (
        <div className="tool-form" style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>
            Cross-Validation Results ({sensorResults.filter(s => s.anomaly_detected).length}/{sensorResults.length} flagged)
          </div>
          <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "2px solid var(--border)", textAlign: "left" }}>
                <th style={{ padding: "4px 6px" }}>Sensor</th>
                <th style={{ padding: "4px 6px" }}>Anomaly</th>
                <th style={{ padding: "4px 6px" }}>Value</th>
                <th style={{ padding: "4px 6px" }}>Description</th>
              </tr>
            </thead>
            <tbody>
              {sensorResults.map((s, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "4px 6px", fontWeight: 500 }}>{s.sensor}</td>
                  <td style={{ padding: "4px 6px" }}>
                    <span className={`badge ${s.anomaly_detected ? "badge-strong" : "badge-weak"}`}>
                      {s.anomaly_detected ? "✅ FLAGGED" : "clear"}
                    </span>
                  </td>
                  <td style={{ padding: "4px 6px", fontFamily: "var(--mono)" }}>
                    {typeof s.value === "number" ? s.value.toFixed(4) : String(s.value)} {s.units}
                  </td>
                  <td style={{ padding: "4px 6px", color: "var(--text-dim)", fontSize: 11, maxWidth: 300 }}>
                    {s.description}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-dim)" }}>
            <strong>Interpretation:</strong>{" "}
            {(() => {
              const flagged = sensorResults.filter(s => s.anomaly_detected).length;
              const total = sensorResults.length;
              if (flagged === 0) return `0/${total} → No corroboration. Magnetic anomaly may be geological.`;
              if (flagged <= 2) return `${flagged}/${total} → Weak corroboration. Worth continued investigation.`;
              if (flagged <= 4) return `${flagged}/${total} → Significant corroboration. Side-scan survey warranted.`;
              return `${flagged}/${total} → Strong multi-physics case. High priority target.`;
            })()}
          </div>
        </div>
      )}

      {/* ── Previous reports ─────────────────────────────── */}
      {reports.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Previous Reports</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {reports.map((r, i) => (
              <div key={i} style={{
                padding: "10px 14px", background: "var(--bg-card)",
                border: "1px solid var(--border)", borderRadius: 6, fontSize: 12,
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                  <span style={{ fontWeight: 600 }}>
                    {String((r.candidate as any)?.label || "Unknown")}
                  </span>
                  <span style={{ color: "var(--text-dim)" }}>{r.generated}</span>
                </div>
                <div>
                  <span className={`badge ${r.sensors_flagged >= 3 ? "badge-strong" : r.sensors_flagged >= 1 ? "badge-moderate" : "badge-weak"}`}>
                    {r.sensors_flagged}/{r.sensors_total} sensors flagged
                  </span>
                  <span style={{ marginLeft: 12, fontFamily: "var(--mono)", fontSize: 11 }}>
                    {(r.candidate as any)?.lat?.toFixed(4)}°N, {Math.abs((r.candidate as any)?.lon ?? 0).toFixed(4)}°W
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
