import { useState, useEffect, useRef } from "react";
import { pickFolder } from "../services/dialog";
import type { ToolJobStatus, ErieCandidate } from "../types";
import {
  getApiBase, resetConnectionState,
  startErieScan, getErieScanStatus, getErieScanResults,
  getErieWellheads, getErieKnownWrecks,
  startErieTraining, getErieTrainingStatus, getErieTrainingReport,
} from "../services/api";
import type { ErieTrainRequest } from "../services/api";

const GROUND_TRUTH_COLORS: Record<string, string> = {
  wreck: "rgba(0,200,80,0.12)",
  wellhead: "rgba(200,120,0,0.12)",
  geological: "rgba(100,100,200,0.08)",
  unknown: "transparent",
};

const GROUND_TRUTH_BADGES: Record<string, string> = {
  wreck: "🚢 Wreck",
  wellhead: "⛽ Wellhead",
  geological: "🪨 Geological",
  unknown: "❓ Unknown",
};

export default function EriePanel() {
  // Config
  const [candidatesCsv, setCandidatesCsv] = useState("adaptive_bg_erie_1000yd/adaptive_candidates_scored.csv");
  const [wellsCsv, setWellsCsv] = useState("");
  const [outputDir, setOutputDir] = useState("erie_scanner_output");
  const [wellheadRadius, setWellheadRadius] = useState(2000);
  const [applyLoran, setApplyLoran] = useState(true);
  const [retrain, setRetrain] = useState(false);

  // State
  const [backendOnline, setBackendOnline] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<ToolJobStatus[]>([]);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Results
  const [candidates, setCandidates] = useState<ErieCandidate[]>([]);
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [loadingResults, setLoadingResults] = useState(false);

  // Reference data
  const [wellheadCount, setWellheadCount] = useState<number | null>(null);
  const [knownWreckCount, setKnownWreckCount] = useState<number | null>(null);

  // Candidate detail expand
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // Training state
  const [trainSubmitting, setTrainSubmitting] = useState(false);
  const [trainJobId, setTrainJobId] = useState<string | null>(null);
  const [trainStatus, setTrainStatus] = useState<string | null>(null);
  const [trainingReport, setTrainingReport] = useState<Record<string, unknown> | null>(null);
  const [nSynthWreck, setNSynthWreck] = useState(10000);
  const [nSynthWellhead, setNSynthWellhead] = useState(3000);
  const [nSynthGeological, setNSynthGeological] = useState(2000);
  const trainPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Backend health check
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
    const id = setInterval(() => void check(), 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Load reference counts on mount
  useEffect(() => {
    if (!backendOnline) return;
    getErieKnownWrecks().then(d => setKnownWreckCount(d.total)).catch(() => {});
    if (wellsCsv) {
      getErieWellheads(wellsCsv).then(d => setWellheadCount(d.total)).catch(() => {});
    } else {
      getErieWellheads().then(d => setWellheadCount(d.total)).catch(() => {});
    }
  }, [backendOnline, wellsCsv]);

  // Job polling
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
          try { return { ...j, ...(await getErieScanStatus(j.id)) }; }
          catch { return j; }
        })
      );
      setJobs(updated);
      // Auto-load results when a job completes
      const justCompleted = updated.find(j => j.status === "completed" && jobs.find(oj => oj.id === j.id && oj.status !== "completed"));
      if (justCompleted) {
        handleLoadResults();
      }
    }, 2000);
    return () => { if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; } };
  }, [jobs]);

  const handleSelectCandidates = async () => {
    try {
      const mod = await import("@tauri-apps/plugin-dialog");
      const file = await mod.open({ title: "Select candidates CSV", filters: [{ name: "CSV", extensions: ["csv"] }] });
      if (file) setCandidatesCsv(typeof file === "string" ? file : (file as { path: string }).path);
    } catch {
      // fallback: user types path
    }
  };

  const handleSelectWells = async () => {
    try {
      const mod = await import("@tauri-apps/plugin-dialog");
      const file = await mod.open({ title: "Select OGSr wells CSV", filters: [{ name: "CSV", extensions: ["csv"] }] });
      if (file) setWellsCsv(typeof file === "string" ? file : (file as { path: string }).path);
    } catch {}
  };

  const handleSelectOutput = async () => {
    const dirs = await pickFolder({ title: "Select output directory" });
    if (dirs.length > 0) setOutputDir(dirs[0]);
  };

  const handleStart = async () => {
    if (!backendOnline) { setError("Backend is offline."); return; }
    setError(null);
    setSubmitting(true);
    try {
      const result = await startErieScan({
        candidates_csv: candidatesCsv || undefined,
        wells_csv: wellsCsv || undefined,
        output_dir: outputDir,
        wellhead_radius_m: wellheadRadius,
        apply_loran_correction: applyLoran,
        retrain,
      });
      setJobs(prev => [{
        id: result.job_id, tool: "erie_scanner", status: result.status,
        created: Date.now() / 1000,
      }, ...prev]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to start Erie scanner");
    } finally {
      setSubmitting(false);
    }
  };

  const handleLoadResults = async () => {
    setLoadingResults(true);
    setError(null);
    try {
      const data = await getErieScanResults(outputDir);
      setCandidates(data.candidates as ErieCandidate[]);
      setSummary({
        wellheads_loaded: data.wellheads_loaded,
        known_wrecks_loaded: data.known_wrecks_loaded,
        candidates_filtered: data.candidates_filtered,
        wellhead_matches: data.wellhead_matches,
        wreck_matches: data.wreck_matches,
        model_accuracy: data.model_accuracy,
      });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load results");
    } finally {
      setLoadingResults(false);
    }
  };

  const fmtTime = (t?: number) => t ? new Date(t * 1000).toLocaleTimeString() : "—";

  // ── Training handlers ──────────────────────────────
  const handleStartTraining = async () => {
    if (!backendOnline) { setError("Backend is offline."); return; }
    setError(null);
    setTrainSubmitting(true);
    try {
      const req: ErieTrainRequest = {
        candidates_csv: candidatesCsv || undefined,
        wells_csv: wellsCsv || undefined,
        output_dir: "models/erie",
        n_synth_wreck: nSynthWreck,
        n_synth_wellhead: nSynthWellhead,
        n_synth_geological: nSynthGeological,
      };
      const result = await startErieTraining(req);
      setTrainJobId(result.job_id);
      setTrainStatus("queued");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to start training");
    } finally {
      setTrainSubmitting(false);
    }
  };

  // Poll training job
  useEffect(() => {
    if (!trainJobId || trainStatus === "completed" || trainStatus === "failed") return;
    const poll = setInterval(async () => {
      try {
        const s = await getErieTrainingStatus(trainJobId);
        setTrainStatus(s.status);
        if (s.status === "completed" || s.status === "failed") {
          clearInterval(poll);
          if (s.status === "completed") {
            try { setTrainingReport(await getErieTrainingReport()); } catch { /* noop */ }
          }
        }
      } catch { /* noop */ }
    }, 3000);
    trainPollRef.current = poll;
    return () => clearInterval(poll);
  }, [trainJobId, trainStatus]);

  const handleLoadTrainingReport = async () => {
    try { setTrainingReport(await getErieTrainingReport()); } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "No training report found");
    }
  };

  const inputStyle: React.CSSProperties = {
    padding: "4px 8px",
    background: "var(--bg)", color: "var(--text)",
    border: "1px solid var(--border)", borderRadius: "var(--radius)",
    fontFamily: "var(--mono)", fontSize: 13,
  };

  return (
    <div style={{ padding: 16, maxWidth: 1000 }}>
      <h2 style={{ marginBottom: 4 }}>
        🌊 Lake Erie Focused Scanner
      </h2>
      <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 16 }}>
        Tuned for Lake Erie basin — wellhead discrimination, Loran-C warp correction,
        satellite gap-filling, known wreck cross-reference.
        {knownWreckCount != null && (
          <span style={{ marginLeft: 12 }}>
            📍 {knownWreckCount} known wrecks
          </span>
        )}
        {wellheadCount != null && (
          <span style={{ marginLeft: 12 }}>
            ⛽ {wellheadCount} wells loaded
          </span>
        )}
      </div>

      {/* ── Ground Truth Banner ─────────────────────────── */}
      <div className="card" style={{ padding: 12, marginBottom: 16, background: "rgba(0,200,80,0.05)" }}>
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
          Confirmed Ground Truth
        </div>
        <div style={{ display: "flex", gap: 16, fontSize: 12 }}>
          <span>🚢 <b>#103</b> — Colgate (whaleback wreck)</span>
          <span>⛽ <b>#63</b> — Gas wellhead</span>
          <span>⛽ <b>#85</b> — Gas wellhead</span>
        </div>
      </div>

      {/* ── Input Configuration ────────────────────────── */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 12, marginBottom: 12, alignItems: "center" }}>
          <label style={{ fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", minWidth: 120 }}>Candidates CSV</label>
          <input value={candidatesCsv} onChange={e => setCandidatesCsv(e.target.value)}
            style={{ ...inputStyle, flex: 1 }} placeholder="adaptive_bg_erie_1000yd/adaptive_candidates_scored.csv" />
          <button onClick={handleSelectCandidates} style={{ fontSize: 12, padding: "4px 10px" }}>Browse…</button>
        </div>
        <div style={{ display: "flex", gap: 12, marginBottom: 12, alignItems: "center" }}>
          <label style={{ fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", minWidth: 120 }}>Wells CSV (OGSr)</label>
          <input value={wellsCsv} onChange={e => setWellsCsv(e.target.value)}
            style={{ ...inputStyle, flex: 1 }} placeholder="data/wells.csv (Ontario petroleum wells)" />
          <button onClick={handleSelectWells} style={{ fontSize: 12, padding: "4px 10px" }}>Browse…</button>
        </div>
        <div style={{ display: "flex", gap: 12, marginBottom: 12, alignItems: "center" }}>
          <label style={{ fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", minWidth: 120 }}>Output dir</label>
          <input value={outputDir} onChange={e => setOutputDir(e.target.value)}
            style={{ ...inputStyle, flex: 1 }} />
          <button onClick={handleSelectOutput} style={{ fontSize: 12, padding: "4px 10px" }}>Select…</button>
        </div>
      </div>

      {/* ── Options ────────────────────────────────────── */}
      <div className="card" style={{ padding: 16, marginBottom: 16, display: "flex", gap: 24, flexWrap: "wrap", alignItems: "center" }}>
        <div>
          <label style={{ fontSize: 13, fontWeight: 600, display: "block", marginBottom: 4 }}>
            Wellhead radius ({wellheadRadius}m)
          </label>
          <input type="range" min={500} max={5000} step={100} value={wellheadRadius}
            onChange={e => setWellheadRadius(parseInt(e.target.value))}
            style={{ width: 160 }} />
        </div>
        <label style={{ fontSize: 13, cursor: "pointer", display: "flex", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={applyLoran} onChange={e => setApplyLoran(e.target.checked)} />
          Loran-C Warp Correction
        </label>
        <label style={{ fontSize: 13, cursor: "pointer", display: "flex", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={retrain} onChange={e => setRetrain(e.target.checked)} />
          Retrain Discriminator
        </label>

        <button
          onClick={handleStart}
          disabled={submitting || !backendOnline}
          style={{
            padding: "8px 24px",
            background: "linear-gradient(135deg, #1a6b4a, #2a9b6a)",
            color: "#fff",
            border: "none", borderRadius: "var(--radius)",
            fontWeight: 600, cursor: submitting ? "not-allowed" : "pointer",
            opacity: (submitting || !backendOnline) ? 0.6 : 1,
          }}
        >
          {submitting ? "Starting…" : !backendOnline ? "Backend Offline" : "🌊 Run Erie Scanner"}
        </button>
      </div>

      {/* ── XGBoost Training Section ──────────────────── */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <h3 style={{ marginBottom: 8 }}>🧠 XGBoost Off-Axis Training</h3>
        <p style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 12 }}>
          Train 4 models (Western/Central/Eastern basin + Erie-wide) using synthetic dipole data
          + real candidates. Pushes detection to 1-2km off flight line while rejecting wellheads.
        </p>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginBottom: 12 }}>
          <label style={{ fontSize: 12, color: "var(--text-dim)" }}>
            Wreck Synthetics/Basin
            <input type="number" value={nSynthWreck} onChange={e => setNSynthWreck(+e.target.value)}
              style={{ ...inputStyle, width: "100%", marginTop: 4 }} />
          </label>
          <label style={{ fontSize: 12, color: "var(--text-dim)" }}>
            Wellhead Synthetics/Basin
            <input type="number" value={nSynthWellhead} onChange={e => setNSynthWellhead(+e.target.value)}
              style={{ ...inputStyle, width: "100%", marginTop: 4 }} />
          </label>
          <label style={{ fontSize: 12, color: "var(--text-dim)" }}>
            Geological Synthetics/Basin
            <input type="number" value={nSynthGeological} onChange={e => setNSynthGeological(+e.target.value)}
              style={{ ...inputStyle, width: "100%", marginTop: 4 }} />
          </label>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            onClick={handleStartTraining}
            disabled={trainSubmitting || !backendOnline || trainStatus === "running"}
            style={{
              padding: "8px 24px", borderRadius: "var(--radius)", border: "none",
              background: "var(--accent-2)", color: "#fff", cursor: "pointer", fontWeight: 600,
              opacity: (trainSubmitting || !backendOnline || trainStatus === "running") ? 0.6 : 1,
            }}
          >
            {trainSubmitting ? "Starting…" : trainStatus === "running" ? "Training…" : "🧠 Train XGBoost Models"}
          </button>
          <button onClick={handleLoadTrainingReport}
            style={{ padding: "8px 16px", borderRadius: "var(--radius)", border: "1px solid var(--border)", background: "var(--bg)", color: "var(--text)", cursor: "pointer", fontSize: 13 }}>
            📊 Load Report
          </button>
          {trainStatus && (
            <span style={{ fontSize: 12, color: trainStatus === "completed" ? "var(--green)" : trainStatus === "failed" ? "var(--red)" : "var(--text-dim)" }}>
              Status: {trainStatus}
            </span>
          )}
        </div>

        {/* Training Report */}
        {trainingReport && (
          <div style={{ marginTop: 12, padding: 12, background: "var(--bg)", borderRadius: "var(--radius)", border: "1px solid var(--border)" }}>
            <h4 style={{ marginBottom: 8, fontSize: 14 }}>Training Report</h4>
            {trainingReport.training_duration_s != null && (
              <p style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 8 }}>
                Duration: {(trainingReport.training_duration_s as number).toFixed(1)}s
              </p>
            )}
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                  <th style={{ padding: "4px 6px" }}>Model</th>
                  <th style={{ padding: "4px 6px" }}>Samples</th>
                  <th style={{ padding: "4px 6px" }}>CV Accuracy</th>
                  <th style={{ padding: "4px 6px" }}>Precision</th>
                  <th style={{ padding: "4px 6px" }}>Recall</th>
                  <th style={{ padding: "4px 6px" }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries((trainingReport.models || {}) as Record<string, Record<string, unknown>>).map(([name, info]) => (
                  <tr key={name} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "4px 6px", fontWeight: 600 }}>{name}</td>
                    <td style={{ padding: "4px 6px" }}>{String(info.samples ?? "—")}</td>
                    <td style={{ padding: "4px 6px" }}>
                      {info.cv_accuracy != null ? Number(info.cv_accuracy).toFixed(3) : "—"}
                    </td>
                    <td style={{ padding: "4px 6px" }}>
                      {info.precision != null ? Number(info.precision).toFixed(3) : "—"}
                    </td>
                    <td style={{ padding: "4px 6px" }}>
                      {info.recall != null ? Number(info.recall).toFixed(3) : "—"}
                    </td>
                    <td style={{ padding: "4px 6px" }}>
                      <span style={{ color: info.status === "trained" ? "var(--green)" : "var(--text-dim)" }}>
                        {String(info.status)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {error && (
        <div style={{ marginBottom: 12, color: "var(--red)", fontSize: 13, padding: "8px 12px", background: "rgba(200,0,0,0.05)", borderRadius: "var(--radius)" }}>
          ⚠ {error}
        </div>
      )}

      {/* ── Job History ────────────────────────────────── */}
      {jobs.length > 0 && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <h3 style={{ marginBottom: 8 }}>Scanner Jobs</h3>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                <th style={{ padding: "6px 8px" }}>Status</th>
                <th style={{ padding: "6px 8px" }}>Job ID</th>
                <th style={{ padding: "6px 8px" }}>Started</th>
                <th style={{ padding: "6px 8px" }}>Results</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map(j => (
                <tr key={j.id} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "6px 8px" }}>
                    <span className={`badge badge-${
                      j.status === "completed" ? "strong" :
                      j.status === "failed" ? "weak" :
                      j.status === "running" ? "moderate" : "steel"
                    }`}>{j.status}</span>
                  </td>
                  <td style={{ padding: "6px 8px", fontFamily: "var(--mono)", fontSize: 11 }}>{j.id.slice(0, 8)}…</td>
                  <td style={{ padding: "6px 8px" }}>{fmtTime(j.start_time)}</td>
                  <td style={{ padding: "6px 8px" }}>
                    {j.status === "completed" && (
                      <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
                        {(j.result as Record<string, unknown>)?.candidates_filtered != null
                          ? `${(j.result as Record<string, unknown>).candidates_filtered} candidates`
                          : "Done"}
                      </span>
                    )}
                    {j.error && <span style={{ color: "var(--red)", fontSize: 11 }}>{j.error}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Load Results ───────────────────────────────── */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Candidate Results</h3>
          <button
            onClick={handleLoadResults}
            disabled={!backendOnline || loadingResults}
            style={{ fontSize: 12, padding: "4px 12px" }}
          >{loadingResults ? "Loading…" : "Load Results"}</button>
        </div>

        {/* Summary stats */}
        {summary && (
          <div style={{ display: "flex", gap: 16, marginBottom: 12, flexWrap: "wrap" }}>
            {[
              ["Candidates", summary.candidates_filtered],
              ["Near Wellheads", summary.wellhead_matches],
              ["Near Wrecks", summary.wreck_matches],
              ["Wells Loaded", summary.wellheads_loaded],
              ["Known Wrecks", summary.known_wrecks_loaded],
              ...(summary.model_accuracy ? [["Model Accuracy", `${((summary.model_accuracy as number) * 100).toFixed(1)}%`]] : []),
            ].map(([label, value]) => (
              <div key={String(label)} style={{
                padding: "8px 14px", borderRadius: "var(--radius)",
                border: "1px solid var(--border)", fontSize: 12, textAlign: "center",
              }}>
                <div style={{ fontWeight: 600, fontSize: 16 }}>{String(value)}</div>
                <div style={{ color: "var(--text-dim)" }}>{String(label)}</div>
              </div>
            ))}
          </div>
        )}

        {/* Candidates table */}
        {candidates.length > 0 && (
          <div style={{ maxHeight: 500, overflowY: "auto", border: "1px solid var(--border)", borderRadius: "var(--radius)" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead style={{ position: "sticky", top: 0, background: "var(--bg)", zIndex: 1 }}>
                <tr style={{ borderBottom: "2px solid var(--border)", textAlign: "left" }}>
                  <th style={{ padding: "6px 8px" }}>ID</th>
                  <th style={{ padding: "6px 8px" }}>Score</th>
                  <th style={{ padding: "6px 8px" }}>Tier</th>
                  <th style={{ padding: "6px 8px" }}>Type</th>
                  <th style={{ padding: "6px 8px" }}>Lat</th>
                  <th style={{ padding: "6px 8px" }}>Lon</th>
                  <th style={{ padding: "6px 8px" }}>Amplitude</th>
                  <th style={{ padding: "6px 8px" }}>Nearest Well</th>
                  <th style={{ padding: "6px 8px" }}>Nearest Wreck</th>
                  <th style={{ padding: "6px 8px" }}>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map(c => (
                  <>
                    <tr
                      key={c.label_id}
                      style={{
                        borderBottom: "1px solid var(--border)",
                        background: GROUND_TRUTH_COLORS[c.ground_truth || "unknown"],
                        cursor: "pointer",
                      }}
                      onClick={() => setExpandedId(expandedId === c.label_id ? null : c.label_id)}
                    >
                      <td style={{ padding: "4px 8px", fontWeight: 600 }}>#{c.label_id}</td>
                      <td style={{ padding: "4px 8px", fontFamily: "var(--mono)" }}>
                        {c.composite_score?.toFixed(1)}
                      </td>
                      <td style={{ padding: "4px 8px" }}>
                        <span className={`badge badge-${c.tier === "TIER1" ? "strong" : c.tier === "TIER2" ? "moderate" : "weak"}`}>
                          {c.tier}
                        </span>
                      </td>
                      <td style={{ padding: "4px 8px" }}>
                        {GROUND_TRUTH_BADGES[c.ground_truth || "unknown"]}
                      </td>
                      <td style={{ padding: "4px 8px" }}>{c.center_lat?.toFixed(4)}</td>
                      <td style={{ padding: "4px 8px" }}>{c.center_lon?.toFixed(4)}</td>
                      <td style={{ padding: "4px 8px" }}>{c.amplitude_peak_abs?.toFixed(1)} nT</td>
                      <td style={{ padding: "4px 8px", fontSize: 11 }}>
                        {c.nearest_wellhead
                          ? <span style={{ color: "var(--orange, #d90)" }}>
                              {c.nearest_wellhead} ({c.wellhead_distance_m?.toFixed(0)}m)
                            </span>
                          : "—"}
                      </td>
                      <td style={{ padding: "4px 8px", fontSize: 11 }}>
                        {c.nearest_known_wreck
                          ? <span style={{ color: "var(--green, #2a7)" }}>
                              {c.nearest_known_wreck} ({c.wreck_distance_m?.toFixed(0)}m)
                            </span>
                          : "—"}
                      </td>
                      <td style={{ padding: "4px 8px", fontSize: 11 }}>{c.dipole_verdict}</td>
                    </tr>
                    {expandedId === c.label_id && (
                      <tr key={`${c.label_id}-detail`}>
                        <td colSpan={10} style={{ padding: "8px 12px", background: "rgba(0,0,0,0.03)" }}>
                          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontSize: 11 }}>
                            <div><b>Dipole Score:</b> {c.dipole_score}</div>
                            <div><b>Bonus Score:</b> {c.bonus_score}</div>
                            <div><b>Width:</b> {c.width_m?.toFixed(0)}m × {c.height_m?.toFixed(0)}m</div>
                            <div><b>Ground Truth:</b> {c.ground_truth_name || "—"}</div>
                            {c.loran_corrected_lat && (
                              <div><b>Loran-Corrected:</b> {c.loran_corrected_lat.toFixed(5)}, {c.loran_corrected_lon?.toFixed(5)}</div>
                            )}
                          </div>
                          {c.all_reasons && c.all_reasons.length > 0 && (
                            <div style={{ marginTop: 8 }}>
                              <b>Scoring Reasons:</b>
                              <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                                {c.all_reasons.map((r, i) => (
                                  <li key={i} style={{
                                    color: String(r).startsWith("+") ? "var(--green, #2a7)" :
                                           String(r).startsWith("-") ? "var(--red, #a33)" : "var(--text-dim)",
                                  }}>{String(r)}</li>
                                ))}
                              </ul>
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {candidates.length === 0 && !loadingResults && (
          <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
            Run the Erie scanner or load existing results. The scanner cross-references
            mag anomaly candidates against Ontario petroleum wells and known shipwrecks,
            applies Loran-C warp correction for aero-mag targets, and trains a discriminator
            to distinguish wrecks from wellheads.
          </div>
        )}
      </div>

      {/* ── Info Panel ─────────────────────────────────── */}
      <div style={{ fontSize: 12, color: "var(--text-dim)", lineHeight: 1.6 }}>
        <b>Lake Erie Scanner</b> extends the baseline Mag Pipeline with region-specific tuning:
        <ul style={{ margin: "4px 0 0 16px" }}>
          <li><b>Well Discrimination:</b> Cross-references against OGSr Ontario petroleum well database
            to identify and penalize candidates near known gas/oil wellheads</li>
          <li><b>Known Wreck Database:</b> {knownWreckCount ?? "?"} positions from Niagara Divers Association
            and ShipwreckWorld for positive correlation</li>
          <li><b>Loran-C Warp:</b> Corrects systematic position errors in pre-GPS aero-mag surveys
            (200-400m depending on basin location)</li>
          <li><b>Basin Tuning:</b> Separate scoring for western (shallow/mineral), central (gas wells),
            and eastern (deep/clean) sub-basins</li>
          <li><b>Satellite Gap-Fill:</b> Uses EMAG2v3 and WDMAM where aero coverage is missing</li>
        </ul>
      </div>
    </div>
  );
}
