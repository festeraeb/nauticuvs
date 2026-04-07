import { useState, useEffect, useRef } from "react";
import { pickFolder } from "../services/dialog";
import type { ToolJobStatus } from "../types";
import { getApiBase, resetConnectionState, startMagPipeline, getMagPipelineStatus, getMagDetections, labelMagDetections } from "../services/api";

const ALL_SOURCES = [
  { key: "emag2", label: "EMAG2v3 (NOAA global)" },
  { key: "usgs_namag", label: "USGS NAmag (N. America)" },
  { key: "nrcan", label: "NRCan Aeromagnetic (Canada)" },
  { key: "wdmam", label: "WDMAM (satellite)" },
  { key: "swarm", label: "ESA Swarm (satellite API)" },
] as const;

const STAGE_OPTIONS = [
  { value: "all", label: "Full pipeline (all stages)" },
  { value: "download,ingest", label: "Download & ingest only" },
  { value: "tile,detect,crossref,export", label: "Detect from existing grids" },
  { value: "detect,crossref,export", label: "Re-detect from existing tiles" },
] as const;

const DEFAULT_BBOX: [number, number, number, number] = [-92.5, 41.0, -75.0, 49.0];

export default function MagPipelinePanel() {
  const [outputDir, setOutputDir] = useState("mag_pipeline_output");
  const [modelsDir, setModelsDir] = useState("bagfilework/training/models");
  const [sources, setSources] = useState<string[]>(["emag2", "usgs_namag", "nrcan", "wdmam"]);
  const [bbox, setBbox] = useState<[number, number, number, number]>(DEFAULT_BBOX);
  const [stages, setStages] = useState("all");
  const [threshold, setThreshold] = useState(0.3);
  const [mode, setMode] = useState<"full" | "validate">("full");
  const [jobs, setJobs] = useState<ToolJobStatus[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [backendOnline, setBackendOnline] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Detection review state
  type Detection = Record<string, unknown> & { patch_file?: string; anomaly_score?: number; label?: number | null };
  const [detections, setDetections] = useState<Detection[]>([]);
  const [detLabelMap, setDetLabelMap] = useState<Record<string, number>>({});
  const [detTotal, setDetTotal] = useState(0);
  const [detLabeled, setDetLabeled] = useState(0);
  const [loadingDet, setLoadingDet] = useState(false);
  const [savingLabels, setSavingLabels] = useState(false);
  const [refining, setRefining] = useState(false);

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
          if (j.status === "completed" || j.status === "failed" || j.status === "skipped") return j;
          try { return { ...j, ...(await getMagPipelineStatus(j.id)) }; }
          catch { return j; }
        })
      );
      setJobs(updated);
    }, 2000);
    return () => { if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; } };
  }, [jobs]);

  const handleSelectOutputDir = async () => {
    const dirs = await pickFolder({ title: "Select output directory" });
    if (dirs.length > 0) setOutputDir(dirs[0]);
  };

  const toggleSource = (key: string) => {
    setSources(prev => prev.includes(key) ? prev.filter(s => s !== key) : [...prev, key]);
  };

  const updateBbox = (idx: number, val: string) => {
    const n = parseFloat(val);
    if (!isNaN(n)) setBbox(prev => { const b = [...prev] as [number, number, number, number]; b[idx] = n; return b; });
  };

  const handleStart = async () => {
    if (!backendOnline) { setError("Backend is offline."); return; }
    if (mode === "full" && sources.length === 0) { setError("Select at least one data source."); return; }
    setError(null);
    setSubmitting(true);
    try {
      const result = await startMagPipeline({
        output_dir: outputDir,
        sources,
        bbox,
        stages,
        threshold,
        mode,
        config: { mag_models_dir: modelsDir },
      });
      setJobs(prev => [{
        id: result.job_id, tool: "mag_pipeline", status: result.status,
        created: Date.now() / 1000,
      }, ...prev]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to start mag pipeline");
    } finally {
      setSubmitting(false);
    }
  };

  const openPath = async (path: string) => {
    try {
      const mod = await import("@tauri-apps/api/core");
      await mod.invoke("open_output_path", { path });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : `Failed to open: ${path}`);
    }
  };

  const fmtTime = (t?: number) => t ? new Date(t * 1000).toLocaleTimeString() : "—";
  const fmtDuration = (start?: number, end?: number) => {
    if (!start) return "";
    const d = (end || Date.now() / 1000) - start;
    return `${d.toFixed(1)}s`;
  };

  const inputStyle: React.CSSProperties = {
    padding: "4px 8px",
    background: "var(--bg)", color: "var(--text)",
    border: "1px solid var(--border)", borderRadius: "var(--radius)",
    fontFamily: "var(--mono)", fontSize: 13,
  };

  return (
    <div style={{ padding: 16, maxWidth: 900 }}>
      <h2 style={{ marginBottom: 16 }}>Magnetic Anomaly Pipeline</h2>

      {/* Mode toggle */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 16, alignItems: "center", marginBottom: 12 }}>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Pipeline mode</label>
          <label style={{ fontSize: 13, cursor: "pointer" }}>
            <input type="radio" checked={mode === "full"} onChange={() => setMode("full")} /> Full (acquire &amp; detect)
          </label>
          <label style={{ fontSize: 13, cursor: "pointer" }}>
            <input type="radio" checked={mode === "validate"} onChange={() => setMode("validate")} /> Validate (model training only)
          </label>
        </div>
      </div>

      {/* Data sources — only for full mode */}
      {mode === "full" && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <label style={{ fontSize: 13, fontWeight: 600, display: "block", marginBottom: 8 }}>Data sources</label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {ALL_SOURCES.map(s => (
              <label key={s.key} style={{
                fontSize: 12, cursor: "pointer", padding: "4px 10px",
                borderRadius: "var(--radius)", border: "1px solid var(--border)",
                background: sources.includes(s.key) ? "var(--accent-dim)" : "var(--bg)",
              }}>
                <input
                  type="checkbox" checked={sources.includes(s.key)}
                  onChange={() => toggleSource(s.key)}
                  style={{ marginRight: 4 }}
                />{s.label}
              </label>
            ))}
          </div>
        </div>
      )}

      {/* Bounding box */}
      {mode === "full" && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <label style={{ fontSize: 13, fontWeight: 600, display: "block", marginBottom: 8 }}>
            Bounding box (lon_min, lat_min, lon_max, lat_max)
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            {["West", "South", "East", "North"].map((lbl, i) => (
              <div key={lbl} style={{ flex: 1 }}>
                <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 2 }}>{lbl}</div>
                <input type="number" step="0.5" value={bbox[i]}
                  onChange={e => updateBbox(i, e.target.value)}
                  style={{ ...inputStyle, width: "100%" }}
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stage selection & threshold */}
      {mode === "full" && (
        <div className="card" style={{ padding: 16, marginBottom: 16, display: "flex", gap: 16 }}>
          <div style={{ flex: 2 }}>
            <label style={{ fontSize: 13, fontWeight: 600, display: "block", marginBottom: 4 }}>Stages</label>
            <select value={stages} onChange={e => setStages(e.target.value)}
              style={{ ...inputStyle, width: "100%" }}>
              {STAGE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 13, fontWeight: 600, display: "block", marginBottom: 4 }}>
              Detection threshold ({threshold.toFixed(2)})
            </label>
            <input type="range" min={0.05} max={0.95} step={0.05} value={threshold}
              onChange={e => setThreshold(parseFloat(e.target.value))}
              style={{ width: "100%" }}
            />
          </div>
        </div>
      )}

      {/* Directories */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 12, marginBottom: 12, alignItems: "center" }}>
          <label style={{ fontSize: 13, fontWeight: 600, whiteSpace: "nowrap" }}>Models dir</label>
          <input value={modelsDir} onChange={e => setModelsDir(e.target.value)}
            style={{ ...inputStyle, flex: 1 }} />
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <label style={{ fontSize: 13, fontWeight: 600, whiteSpace: "nowrap" }}>Output dir</label>
          <input value={outputDir} onChange={e => setOutputDir(e.target.value)}
            style={{ ...inputStyle, flex: 1 }} />
          <button onClick={handleSelectOutputDir} style={{ padding: "4px 12px", fontSize: 13 }}>Select Folder…</button>
        </div>

        {error && (
          <div style={{ marginTop: 12, color: "var(--red)", fontSize: 13 }}>⚠ {error}</div>
        )}

        <button
          onClick={handleStart}
          disabled={submitting || !backendOnline}
          style={{
            marginTop: 16, padding: "8px 24px",
            background: "var(--accent-dim)", color: "var(--text)",
            border: "none", borderRadius: "var(--radius)",
            fontWeight: 600, cursor: submitting ? "not-allowed" : "pointer",
            opacity: (submitting || !backendOnline) ? 0.6 : 1,
          }}
        >
          {submitting ? "Starting…" : (!backendOnline ? "Run (Backend Offline)" :
            mode === "full" ? "Run Mag Pipeline" : "Run Model Validation")}
        </button>
      </div>

      {/* Job history */}
      {jobs.length > 0 && (
        <div className="card" style={{ padding: 16 }}>
          <h3 style={{ marginBottom: 12 }}>Pipeline Jobs</h3>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                <th style={{ padding: "6px 8px" }}>Status</th>
                <th style={{ padding: "6px 8px" }}>Job ID</th>
                <th style={{ padding: "6px 8px" }}>Mode</th>
                <th style={{ padding: "6px 8px" }}>Started</th>
                <th style={{ padding: "6px 8px" }}>Duration</th>
                <th style={{ padding: "6px 8px" }}>Detections</th>
                <th style={{ padding: "6px 8px" }}>Output</th>
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
                  <td style={{ padding: "6px 8px", fontSize: 12 }}>{(j.result?.mode as string) ?? "—"}</td>
                  <td style={{ padding: "6px 8px" }}>{fmtTime(j.start_time)}</td>
                  <td style={{ padding: "6px 8px" }}>{fmtDuration(j.start_time, j.end_time)}</td>
                  <td style={{ padding: "6px 8px" }}>
                    {j.result?.detections != null ? String(j.result.detections) :
                     j.result?.candidates_count != null ? `${j.result.candidates_count} candidates` : "—"}
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    <button
                      onClick={() => {
                        const p = (j.result?.output_json ?? j.result?.output_dir) as string | undefined;
                        if (p) openPath(p);
                      }}
                      disabled={!j.result?.output_json && !j.result?.output_dir}
                      style={{ fontSize: 11, padding: "2px 8px", opacity: (j.result?.output_json || j.result?.output_dir) ? 1 : 0.5 }}
                    >Open</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {jobs.some(j => j.error) && (
            <div style={{ marginTop: 8 }}>
              {jobs.filter(j => j.error).map(j => (
                <div key={j.id} style={{ color: "var(--red)", fontSize: 12, marginTop: 4 }}>
                  {j.id.slice(0, 8)}: {j.error}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div style={{ marginTop: 16, fontSize: 12, color: "var(--text-dim)" }}>
        <strong>Full mode</strong> downloads aerial magnetic data (EMAG2, USGS NAmag, NRCan, WDMAM, ESA Swarm),
        grids it into GeoTIFF tiles, runs statistical and ML-based anomaly detection, and cross-references
        detections against the wrecks database.{" "}
        <strong>Validate mode</strong> runs leave-one-out and saved-model validation for training refinement.
      </div>

      {/* ── Detection Review & Labeling ──────────────────────────────── */}
      <div className="card" style={{ padding: 16, marginTop: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Detection Review &amp; Refinement</h3>
          <button
            onClick={async () => {
              setLoadingDet(true);
              try {
                const data = await getMagDetections(outputDir);
                setDetections(data.detections as Detection[]);
                setDetTotal(data.total);
                setDetLabeled(data.labeled);
                // Seed label map from existing labels
                const map: Record<string, number> = {};
                for (const d of data.detections) {
                  if (d.label != null && d.patch_file) map[d.patch_file as string] = d.label as number;
                }
                setDetLabelMap(map);
              } catch (e: unknown) {
                setError(e instanceof Error ? e.message : "Failed to load detections");
              } finally { setLoadingDet(false); }
            }}
            disabled={!backendOnline || loadingDet}
            style={{ fontSize: 12, padding: "4px 12px" }}
          >{loadingDet ? "Loading…" : "Load Detections"}</button>
          {detTotal > 0 && (
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
              {detTotal} detections, {detLabeled} labeled
            </span>
          )}
        </div>

        {detections.length > 0 && (
          <>
            <div style={{ maxHeight: 350, overflowY: "auto", border: "1px solid var(--border)", borderRadius: "var(--radius)" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead style={{ position: "sticky", top: 0, background: "var(--bg)" }}>
                  <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                    <th style={{ padding: "6px 8px" }}>Score</th>
                    <th style={{ padding: "6px 8px" }}>Source</th>
                    <th style={{ padding: "6px 8px" }}>Method</th>
                    <th style={{ padding: "6px 8px" }}>Lat</th>
                    <th style={{ padding: "6px 8px" }}>Lon</th>
                    <th style={{ padding: "6px 8px" }}>RMS</th>
                    <th style={{ padding: "6px 8px", textAlign: "center" }}>Label</th>
                  </tr>
                </thead>
                <tbody>
                  {detections.map((d, i) => {
                    const pf = (d.patch_file ?? `det_${i}`) as string;
                    const currentLabel = detLabelMap[pf];
                    return (
                      <tr key={i} style={{
                        borderBottom: "1px solid var(--border)",
                        background: currentLabel === 1 ? "rgba(0,200,80,0.08)" :
                                    currentLabel === 0 ? "rgba(200,0,0,0.06)" : undefined,
                      }}>
                        <td style={{ padding: "4px 8px", fontWeight: 600 }}>{((d.anomaly_score as number) ?? 0).toFixed(3)}</td>
                        <td style={{ padding: "4px 8px", fontFamily: "var(--mono)", fontSize: 11 }}>{(d.source_grid as string) ?? "—"}</td>
                        <td style={{ padding: "4px 8px" }}>{(d.method as string) ?? "—"}</td>
                        <td style={{ padding: "4px 8px" }}>{d.lat != null ? (d.lat as number).toFixed(4) : "—"}</td>
                        <td style={{ padding: "4px 8px" }}>{d.lon != null ? (d.lon as number).toFixed(4) : "—"}</td>
                        <td style={{ padding: "4px 8px" }}>{d.rms != null ? (d.rms as number).toFixed(1) : "—"}</td>
                        <td style={{ padding: "4px 8px", textAlign: "center" }}>
                          <button onClick={() => setDetLabelMap(m => ({ ...m, [pf]: 1 }))}
                            style={{ fontSize: 11, padding: "2px 6px", marginRight: 4,
                              background: currentLabel === 1 ? "#2a7" : "var(--bg)",
                              color: currentLabel === 1 ? "#fff" : "var(--text)",
                              border: "1px solid var(--border)", borderRadius: 3, cursor: "pointer" }}
                          >Wreck</button>
                          <button onClick={() => setDetLabelMap(m => ({ ...m, [pf]: 0 }))}
                            style={{ fontSize: 11, padding: "2px 6px",
                              background: currentLabel === 0 ? "#a33" : "var(--bg)",
                              color: currentLabel === 0 ? "#fff" : "var(--text)",
                              border: "1px solid var(--border)", borderRadius: 3, cursor: "pointer" }}
                          >Not</button>
                          {currentLabel != null && (
                            <button onClick={() => setDetLabelMap(m => {
                              const copy = { ...m }; delete copy[pf]; return copy;
                            })}
                              style={{ fontSize: 10, padding: "2px 4px", marginLeft: 2, opacity: 0.5,
                                border: "none", background: "none", cursor: "pointer", color: "var(--text)" }}
                            >✕</button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Save labels + Refine buttons */}
            <div style={{ display: "flex", gap: 12, marginTop: 12, alignItems: "center" }}>
              <button
                onClick={async () => {
                  setSavingLabels(true);
                  try {
                    const labels = Object.entries(detLabelMap).map(([patch_file, label]) => ({ patch_file, label }));
                    if (labels.length === 0) { setError("No labels to save"); return; }
                    const result = await labelMagDetections(outputDir, labels);
                    setDetLabeled(result.total_labeled);
                    setError(null);
                  } catch (e: unknown) {
                    setError(e instanceof Error ? e.message : "Failed to save labels");
                  } finally { setSavingLabels(false); }
                }}
                disabled={savingLabels || Object.keys(detLabelMap).length === 0}
                style={{ padding: "6px 16px", fontSize: 13, fontWeight: 600,
                  background: "var(--accent-dim)", color: "var(--text)",
                  border: "none", borderRadius: "var(--radius)", cursor: "pointer",
                  opacity: Object.keys(detLabelMap).length === 0 ? 0.5 : 1 }}
              >{savingLabels ? "Saving…" : `Save ${Object.keys(detLabelMap).length} Labels`}</button>

              <button
                onClick={async () => {
                  setRefining(true);
                  setError(null);
                  try {
                    const result = await startMagPipeline({
                      output_dir: outputDir,
                      stages: "refine",
                      mode: "full",
                      config: { mag_models_dir: modelsDir },
                    });
                    setJobs(prev => [{
                      id: result.job_id, tool: "mag_pipeline", status: result.status,
                      created: Date.now() / 1000,
                    }, ...prev]);
                  } catch (e: unknown) {
                    setError(e instanceof Error ? e.message : "Failed to start refinement");
                  } finally { setRefining(false); }
                }}
                disabled={refining || !backendOnline || detLabeled === 0}
                style={{ padding: "6px 16px", fontSize: 13, fontWeight: 600,
                  background: "#2a4", color: "#fff",
                  border: "none", borderRadius: "var(--radius)", cursor: "pointer",
                  opacity: (detLabeled === 0 || !backendOnline) ? 0.5 : 1 }}
              >{refining ? "Refining…" : "Refine Models with Labels"}</button>

              <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
                {Object.values(detLabelMap).filter(v => v === 1).length} wreck / {Object.values(detLabelMap).filter(v => v === 0).length} not-wreck
              </span>
            </div>
          </>
        )}

        {detections.length === 0 && detTotal === 0 && (
          <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
            Run the pipeline first, then load detections here to label them as wreck/not-wreck.
            Labeled detections feed into model refinement training.
          </div>
        )}
      </div>
    </div>
  );
}
