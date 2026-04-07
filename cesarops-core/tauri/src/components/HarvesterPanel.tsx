/**
 * HarvesterPanel.tsx
 * Raw Magnetometer Harvester — Tauri GUI front-end
 *
 * Workflow:
 *   1. [optional] Export LORAN-C warp field (needs datum_anchors.json with verified anchors)
 *   2. Choose lake + sources + options
 *   3. Click "Start Harvest" → polls /tools/harvester/status/{id} every 2 s
 *   4. Shows per-source pipeline state + progress bar
 *   5. On completion shows ping count, output CSV path, and survey table
 */
import { useState, useEffect, useRef } from "react";
import type { WarpFieldInfo, HarvesterJobStatus, HarvesterRequest } from "../types";
import {
  getApiBase, resetConnectionState,
  getWarpFieldInfo,
  startWarpFieldExport, getWarpFieldExportStatus,
  startHarvester, getHarvesterStatus, getHarvesterResults, getHarvesterCatalog,
} from "../services/api";

// ── Constants ───────────────────────────────────────────────────────────────

const SOURCE_DEFS = [
  { key: "ngdc",         label: "NGDC WFS (NOAA trackline)",  tip: "Marine + airborne 1-Hz pings via live NGDC WFS query" },
  { key: "sciencebase",  label: "USGS ScienceBase",           tip: "DS-321 (Ohio) + DS-411 (Michigan) aeromagnetic archives" },
  { key: "nrcan",        label: "NRCan open.canada.ca",       tip: "Canadian north-shore aeromagnetic XYZ files" },
  { key: "swarm",        label: "ESA Swarm (optional)",       tip: "MAGx_LR 1-Hz satellite baseline — needs viresclient + token" },
] as const;

const LAKE_OPTIONS = [
  { value: "erie",     label: "Lake Erie" },
  { value: "huron",   label: "Lake Huron / Georgian Bay" },
  { value: "superior",label: "Lake Superior" },
  { value: "michigan",label: "Lake Michigan" },
  { value: "ontario", label: "Lake Ontario" },
] as const;

// ── Per-source status badge ─────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  pending:  "var(--border)",
  running:  "#f59e0b",
  done:     "#22c55e",
  failed:   "#ef4444",
  skipped:  "#6b7280",
};

function SourceBadge({ src, state }: { src: string; state: string }) {
  const color = STATUS_COLORS[state] ?? "var(--border)";
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: "2px 10px", borderRadius: 12,
      border: `1px solid ${color}`,
      color, fontSize: 11, fontFamily: "var(--mono)",
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: "50%",
        background: color, display: "inline-block",
      }} />
      {src} · {state}
    </span>
  );
}

// ── Main panel ──────────────────────────────────────────────────────────────

export default function HarvesterPanel() {
  // Warp field state
  const [warpInfo, setWarpInfo]       = useState<WarpFieldInfo | null>(null);
  const [warpJobId, setWarpJobId]     = useState<string | null>(null);
  const [warpRunning, setWarpRunning] = useState(false);
  const [warpSpacing, setWarpSpacing] = useState(2.0);

  // Harvester settings
  const [lake, setLake]               = useState<string>("erie");
  const [sources, setSources]         = useState<string[]>(["ngdc", "sciencebase", "nrcan"]);
  const [applyWarp, setApplyWarp]     = useState(true);
  const [maxSurveys, setMaxSurveys]   = useState(0);
  const [dryRun, setDryRun]           = useState(false);
  const [swarmToken, setSwarmToken]   = useState("");

  // Job state
  const [jobId, setJobId]             = useState<string | null>(null);
  const [jobStatus, setJobStatus]     = useState<HarvesterJobStatus | null>(null);
  const [catalog, setCatalog]         = useState<Record<string, unknown> | null>(null);
  const [submitting, setSubmitting]   = useState(false);
  const [error, setError]             = useState<string | null>(null);
  const [backendOnline, setBackendOnline] = useState(false);

  const pollRef   = useRef<ReturnType<typeof setInterval> | null>(null);
  const warpPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Backend health ping ──────────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const base = await getApiBase();
        const res = await fetch(`${base}/health`, { signal: AbortSignal.timeout(2500) });
        if (!cancelled) setBackendOnline(res.ok);
      } catch {
        resetConnectionState();
        try {
          const base = await getApiBase();
          const res = await fetch(`${base}/health`, { signal: AbortSignal.timeout(2500) });
          if (!cancelled) setBackendOnline(res.ok);
        } catch {
          if (!cancelled) setBackendOnline(false);
        }
      }
    };
    void check();
    const id = setInterval(() => void check(), 6000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // ── Load warp field info on mount ────────────────────────────────────────

  useEffect(() => {
    void getWarpFieldInfo().then(setWarpInfo).catch(() => setWarpInfo(null));
  }, []);

  // ── Poll harvester job ───────────────────────────────────────────────────

  useEffect(() => {
    if (!jobId || !jobStatus) return;
    if (["completed", "failed"].includes(jobStatus.status)) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      if (jobStatus.status === "completed") {
        void getHarvesterCatalog(lake).then(setCatalog).catch(() => null);
      }
      return;
    }
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      if (!jobId) return;
      try {
        const s = await getHarvesterStatus(jobId);
        setJobStatus(s);
        if (["completed", "failed"].includes(s.status)) {
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
          if (s.status === "completed") {
            void getHarvesterCatalog(lake).then(setCatalog).catch(() => null);
          }
        }
      } catch { /* keep polling */ }
    }, 2000);
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  }, [jobId, jobStatus, lake]);

  // ── Poll warp export job ─────────────────────────────────────────────────

  useEffect(() => {
    if (!warpJobId) return;
    if (!warpRunning) return;
    if (warpPollRef.current) return;
    warpPollRef.current = setInterval(async () => {
      if (!warpJobId) return;
      try {
        const s = await getWarpFieldExportStatus(warpJobId);
        if (["completed", "failed"].includes(s.status ?? "")) {
          if (warpPollRef.current) { clearInterval(warpPollRef.current); warpPollRef.current = null; }
          setWarpRunning(false);
          // Refresh warp info
          const info = await getWarpFieldInfo();
          setWarpInfo(info);
        }
      } catch { /* keep polling */ }
    }, 2000);
    return () => { if (warpPollRef.current) { clearInterval(warpPollRef.current); warpPollRef.current = null; } };
  }, [warpJobId, warpRunning]);

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleExportWarp = async () => {
    setError(null);
    setWarpRunning(true);
    try {
      const job = await startWarpFieldExport({ lake, spacing_km: warpSpacing });
      setWarpJobId(job.job_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Warp export failed");
      setWarpRunning(false);
    }
  };

  const handleStart = async () => {
    if (!backendOnline) { setError("Backend is offline."); return; }
    if (sources.length === 0) { setError("Select at least one source."); return; }
    setError(null);
    setSubmitting(true);
    setCatalog(null);
    const req: HarvesterRequest = {
      lake, sources, apply_warp: applyWarp,
      max_surveys: maxSurveys,
      dry_run: dryRun,
      swarm_token: swarmToken,
    };
    try {
      const job = await startHarvester(req);
      setJobId(job.job_id);
      setJobStatus({
        id: job.job_id, tool: "harvester", status: "queued",
        lake, sources, created: Date.now() / 1000,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Harvest start failed");
    } finally {
      setSubmitting(false);
    }
  };

  const handleLoadCatalog = async () => {
    try {
      const c = await getHarvesterCatalog(lake);
      setCatalog(c);
    } catch (e) {
      setError(e instanceof Error ? e.message : "No catalog — run a harvest first");
    }
  };

  // ── Helpers ───────────────────────────────────────────────────────────────

  const toggleSource = (key: string) =>
    setSources(prev => prev.includes(key) ? prev.filter(s => s !== key) : [...prev, key]);

  const fmtTime = (t?: number) => t ? new Date(t * 1000).toLocaleTimeString() : "—";

  const jobDone    = jobStatus?.status === "completed";
  const jobFailed  = jobStatus?.status === "failed";
  const jobRunning = jobStatus?.status === "running" || jobStatus?.status === "queued";

  // ── Input style helper ────────────────────────────────────────────────────

  const inputStyle: React.CSSProperties = {
    padding: "4px 8px",
    background: "var(--bg)", color: "var(--text)",
    border: "1px solid var(--border)", borderRadius: "var(--radius)",
    fontFamily: "var(--mono)", fontSize: 13,
  };

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div style={{ padding: 16, maxWidth: 860 }}>
      <h2 style={{ marginBottom: 4 }}>Raw Magnetometer Harvester</h2>
      <p style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 16 }}>
        Downloads true 1-Hz ping data from NGDC, USGS ScienceBase, NRCan, and ESA Swarm.
        Bypasses pre-gridded TIF products. Applies LORAN-C warp correction.
      </p>

      {/* Backend status */}
      <div style={{
        padding: "6px 12px", marginBottom: 12, borderRadius: "var(--radius)",
        border: "1px solid var(--border)", fontSize: 12,
        color: backendOnline ? "#22c55e" : "#ef4444",
      }}>
        {backendOnline ? "● Backend online" : "● Backend offline — start Tauri app or uvicorn"}
      </div>

      {error && (
        <div style={{ padding: "8px 12px", marginBottom: 12, borderRadius: "var(--radius)",
          background: "#7f1d1d33", border: "1px solid #ef4444",
          color: "#fca5a5", fontSize: 13 }}>
          {error}
        </div>
      )}

      {/* ── Step 1: Warp Field ─────────────────────────────────────────── */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <h3 style={{ fontSize: 14, marginBottom: 8 }}>Step 1 — LORAN-C Warp Field</h3>
        {warpInfo?.exists ? (
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 8 }}>
            <span style={{ color: "#22c55e", marginRight: 8 }}>✓ Built</span>
            Lake: {warpInfo.lake} · Anchors: {warpInfo.anchor_count} · Grid: {warpInfo.grid_spacing_km} km · {warpInfo.size_kb} KB
            {warpInfo.generated && <> · Generated: {new Date(warpInfo.generated).toLocaleDateString()}</>}
          </div>
        ) : (
          <div style={{ fontSize: 12, color: "#f59e0b", marginBottom: 8 }}>
            ⚠ No warp field yet — harvester will still run but without LORAN-C correction.
            <br />Requires verified anchors in <code>scripts/datum_anchors.json</code>.
          </div>
        )}
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <label style={{ fontSize: 12 }}>Grid spacing:
            <input
              type="number" min={0.5} max={10} step={0.5}
              value={warpSpacing}
              onChange={e => setWarpSpacing(parseFloat(e.target.value))}
              style={{ ...inputStyle, width: 64, marginLeft: 6 }}
            /> km
          </label>
          <button
            onClick={() => void handleExportWarp()}
            disabled={warpRunning || !backendOnline}
            style={{
              padding: "5px 14px", fontSize: 12,
              background: warpRunning ? "var(--border)" : "var(--accent)",
              color: "var(--bg)", border: "none", borderRadius: "var(--radius)",
              cursor: warpRunning ? "not-allowed" : "pointer",
            }}
          >
            {warpRunning ? "Exporting…" : warpInfo?.exists ? "Rebuild Warp Field" : "Export Warp Field"}
          </button>
          {warpInfo?.anchors_used && warpInfo.anchors_used.length > 0 && (
            <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
              Anchors: {warpInfo.anchors_used.map(a => a.name).join(", ")}
            </span>
          )}
        </div>
      </div>

      {/* ── Step 2: Config ────────────────────────────────────────────── */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <h3 style={{ fontSize: 14, marginBottom: 12 }}>Step 2 — Configure Harvest</h3>

        {/* Lake picker */}
        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 4 }}>
            Target lake
          </label>
          <select
            value={lake}
            onChange={e => setLake(e.target.value)}
            style={{ ...inputStyle, width: 250 }}
          >
            {LAKE_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>

        {/* Sources */}
        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 6 }}>
            Data sources
          </label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {SOURCE_DEFS.map(s => (
              <label
                key={s.key}
                title={s.tip}
                style={{
                  fontSize: 12, cursor: "pointer", padding: "5px 12px",
                  borderRadius: "var(--radius)", border: "1px solid var(--border)",
                  background: sources.includes(s.key) ? "var(--accent-dim)" : "var(--bg)",
                  userSelect: "none",
                }}
              >
                <input
                  type="checkbox" checked={sources.includes(s.key)}
                  onChange={() => toggleSource(s.key)}
                  style={{ marginRight: 5 }}
                />{s.label}
              </label>
            ))}
          </div>
        </div>

        {/* Options row */}
        <div style={{ display: "flex", gap: 20, alignItems: "center", flexWrap: "wrap" }}>
          <label style={{ fontSize: 12, cursor: "pointer" }}>
            <input type="checkbox" checked={applyWarp} onChange={e => setApplyWarp(e.target.checked)}
              style={{ marginRight: 5 }} />
            Apply LORAN-C warp
          </label>

          <label style={{ fontSize: 12, cursor: "pointer" }}>
            <input type="checkbox" checked={dryRun} onChange={e => setDryRun(e.target.checked)}
              style={{ marginRight: 5 }} />
            Dry run (catalog only, no download)
          </label>

          <label style={{ fontSize: 12 }}>Max surveys (0=all):
            <input
              type="number" min={0} max={200} step={1}
              value={maxSurveys}
              onChange={e => setMaxSurveys(parseInt(e.target.value, 10) || 0)}
              style={{ ...inputStyle, width: 60, marginLeft: 6 }}
            />
          </label>
        </div>

        {/* Swarm token */}
        {sources.includes("swarm") && (
          <div style={{ marginTop: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
              ESA VirES token (optional — <a
                href="https://vires.services/accounts/tokens/"
                style={{ color: "var(--accent)" }}
                target="_blank" rel="noreferrer">get token</a>)
            </label>
            <input
              type="password"
              value={swarmToken}
              onChange={e => setSwarmToken(e.target.value)}
              placeholder="Paste VirES token here"
              style={{ ...inputStyle, width: 400 }}
            />
          </div>
        )}
      </div>

      {/* ── Step 3: Run ──────────────────────────────────────────────── */}
      <div style={{ display: "flex", gap: 12, marginBottom: 20, alignItems: "center" }}>
        <button
          onClick={() => void handleStart()}
          disabled={submitting || jobRunning || !backendOnline}
          style={{
            padding: "8px 24px", fontSize: 14, fontWeight: 600,
            background: (submitting || jobRunning) ? "var(--border)" : "var(--accent)",
            color: "var(--bg)", border: "none", borderRadius: "var(--radius)",
            cursor: (submitting || jobRunning) ? "not-allowed" : "pointer",
          }}
        >
          {jobRunning ? "Harvesting…" : "Start Harvest"}
        </button>

        <button
          onClick={() => void handleLoadCatalog()}
          disabled={!backendOnline}
          style={{
            padding: "6px 16px", fontSize: 12,
            background: "transparent", color: "var(--accent)",
            border: "1px solid var(--accent)", borderRadius: "var(--radius)",
            cursor: "pointer",
          }}
        >
          Load Last Catalog
        </button>
      </div>

      {/* ── Live job status ──────────────────────────────────────────── */}
      {jobStatus && (
        <div className="card" style={{ padding: 14, marginBottom: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>
              Job {jobStatus.id.slice(0, 8)}…
            </span>
            <span style={{
              fontSize: 12, padding: "2px 10px", borderRadius: 12,
              border: `1px solid ${jobDone ? "#22c55e" : jobFailed ? "#ef4444" : "#f59e0b"}`,
              color: jobDone ? "#22c55e" : jobFailed ? "#ef4444" : "#f59e0b",
            }}>
              {jobStatus.status}
            </span>
          </div>

          {/* Progress bar */}
          {typeof jobStatus.progress_pct === "number" && (
            <div style={{ marginBottom: 8 }}>
              <div style={{
                height: 6, borderRadius: 3,
                background: "var(--border)", overflow: "hidden",
              }}>
                <div style={{
                  height: "100%", borderRadius: 3,
                  background: jobDone ? "#22c55e" : jobFailed ? "#ef4444" : "#f59e0b",
                  width: `${jobStatus.progress_pct}%`,
                  transition: "width 0.4s ease",
                }} />
              </div>
              <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
                {jobStatus.progress_pct}% — {jobStatus.progress_msg ?? ""}
              </span>
            </div>
          )}

          {/* Per-source badges */}
          {jobStatus.pipeline && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
              {Object.entries(jobStatus.pipeline).map(([src, state]) => (
                <SourceBadge key={src} src={src} state={state} />
              ))}
            </div>
          )}

          <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
            Started: {fmtTime(jobStatus.start_time)}
            {jobStatus.end_time && <> · Finished: {fmtTime(jobStatus.end_time)}</>}
          </div>

          {jobStatus.error && (
            <div style={{
              marginTop: 8, padding: "6px 10px", borderRadius: "var(--radius)",
              background: "#7f1d1d22", border: "1px solid #ef4444",
              color: "#fca5a5", fontSize: 12, fontFamily: "var(--mono)",
            }}>
              {jobStatus.error}
            </div>
          )}
        </div>
      )}

      {/* ── Catalog / results ────────────────────────────────────────────── */}
      {catalog && (
        <div className="card" style={{ padding: 14 }}>
          <h3 style={{ fontSize: 13, marginBottom: 10 }}>Harvest Results</h3>

          {/* Summary row */}
          <div style={{ display: "flex", gap: 24, marginBottom: 12, flexWrap: "wrap" }}>
            {[
              { label: "Total pings", value: ((catalog.total_pings as number) ?? 0).toLocaleString() },
              { label: "Surveys", value: (catalog.surveys as unknown[])?.length ?? 0 },
              { label: "Warp applied", value: catalog.warp_applied ? "✓ Yes" : "✗ No" },
              { label: "Output CSV", value: catalog.output_csv ? "generated" : "—" },
            ].map(({ label, value }) => (
              <div key={label}>
                <div style={{ fontSize: 10, color: "var(--text-dim)" }}>{label}</div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{String(value)}</div>
              </div>
            ))}
          </div>

          {/* Survey table */}
          {Array.isArray(catalog.surveys) && (catalog.surveys as unknown[]).length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table style={{ fontSize: 11, borderCollapse: "collapse", width: "100%" }}>
                <thead>
                  <tr>
                    {["Survey ID", "Source", "Status", "Pings", "Error"].map(h => (
                      <th key={h} style={{
                        textAlign: "left", padding: "4px 10px",
                        borderBottom: "1px solid var(--border)", color: "var(--text-dim)",
                      }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(catalog.surveys as Record<string, unknown>[]).slice(0, 80).map((s, i) => (
                    <tr key={i}>
                      <td style={{ padding: "3px 10px", fontFamily: "var(--mono)" }}>{s.survey_id as string}</td>
                      <td style={{ padding: "3px 10px" }}>{s.source as string}</td>
                      <td style={{
                        padding: "3px 10px",
                        color: s.status === "downloaded" ? "#22c55e"
                             : s.status === "cached"     ? "#86efac"
                             : s.status === "failed"     ? "#ef4444"
                             : "var(--text-dim)",
                      }}>{s.status as string}</td>
                      <td style={{ padding: "3px 10px", textAlign: "right", fontFamily: "var(--mono)" }}>
                        {((s.pings as number) || 0).toLocaleString()}
                      </td>
                      <td style={{ padding: "3px 10px", color: "#f59e0b", fontSize: 10 }}>
                        {(s.error as string) || ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {(catalog.surveys as unknown[]).length > 80 && (
                <p style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4 }}>
                  Showing first 80 of {(catalog.surveys as unknown[]).length} surveys.
                </p>
              )}
            </div>
          )}

          {catalog.output_csv && (
            <p style={{ marginTop: 10, fontSize: 12, fontFamily: "var(--mono)", color: "var(--text-dim)" }}>
              Merged CSV: {catalog.output_csv as string}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
