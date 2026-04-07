import { useState, useEffect, useRef } from "react";
import { pickFiles, pickFolder, getDroppedPaths } from "../services/dialog";
import type { ScanStatus, ScanResultsResponse, SwayzeMatch, ScanCandidate } from "../types";
import { getApiBase, resetConnectionState, startScan, getScanStatus, getScanResults, scanToRestore, getRestorationStatus } from "../services/api";

interface ScanConfig {
  min_wreck_size_sq_ft: number;
  max_wreck_size_sq_ft: number;
  min_confidence: number;
  anomaly_threshold: number;
}



const DEFAULT_CONFIG: ScanConfig = {
  min_wreck_size_sq_ft: 25,
  max_wreck_size_sq_ft: 50000,
  min_confidence: 0.3,
  anomaly_threshold: 2.5,
};

export default function ScanPanel() {
  const [paths, setPaths] = useState("");
  const [outputDir, setOutputDir] = useState("advanced_scan_results");
  const [outputName, setOutputName] = useState("");
  const [showNamePrompt, setShowNamePrompt] = useState(false);
  const [config, setConfig] = useState<ScanConfig>(DEFAULT_CONFIG);
  const [scannerMode, setScannerMode] = useState<"rust_fast" | "black_hole">("rust_fast");

  const [autoRestore, setAutoRestore] = useState(false);
  const [useVisionAI, setUseVisionAI] = useState(false);

  const [showAdvanced, setShowAdvanced] = useState(false);
  const [jobs, setJobs] = useState<(ScanStatus & { paths?: string })[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [backendError, setBackendError] = useState<string | null>(null);
  const [backendOnline, setBackendOnline] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState(false);
  const [scanResults, setScanResults] = useState<ScanResultsResponse | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [restoringIdx, setRestoringIdx] = useState<number | null>(null);
  const [restoreStatus, setRestoreStatus] = useState<string | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;

    const checkBackend = async () => {
      try {
        const apiBase = await getApiBase();
        const res = await fetch(`${apiBase}/health`, { signal: AbortSignal.timeout(2500) });
        if (!cancelled) {
          setBackendOnline(res.ok);
          setBackendError(res.ok ? null : `Health check failed at ${apiBase}`);
        }
      } catch (firstErr) {
        // Re-resolve backend once because cached host:port can be stale after restarts.
        resetConnectionState();
        try {
          const apiBase = await getApiBase();
          const res = await fetch(`${apiBase}/health`, { signal: AbortSignal.timeout(2500) });
          if (!cancelled) {
            setBackendOnline(res.ok);
            setBackendError(res.ok ? null : `Health check failed at ${apiBase}`);
          }
        } catch (retryErr) {
          if (!cancelled) {
            setBackendOnline(false);
            const first = firstErr instanceof Error ? firstErr.message : String(firstErr);
            const retry = retryErr instanceof Error ? retryErr.message : String(retryErr);
            setBackendError(`Backend resolve failed. first=${first} retry=${retry}`);
          }
        }
      }
    };

    void checkBackend();
    const id = setInterval(() => { void checkBackend(); }, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // Poll active jobs + auto-fetch results on completion
  useEffect(() => {
    const activeJobs = jobs.filter(j => j.status === "queued" || j.status === "running");
    if (activeJobs.length === 0) {
      if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; }
      return;
    }
    if (pollingRef.current) return; // already polling
    pollingRef.current = setInterval(async () => {
      const updated = await Promise.all(
        jobs.map(async j => {
          if (j.status === "completed" || j.status === "failed") return j;
          try {
            const s = await getScanStatus(j.id);
            // Auto-load results when job completes
            if (s.status === "completed" && j.status !== "completed") {
              try {
                const results = await getScanResults(j.id);
                setScanResults(results);
                setSelectedJobId(j.id);
              } catch { /* ignore */ }
            }
            return { ...j, ...s };
          } catch { return j; }
        })
      );
      setJobs(updated);
    }, 2000);
    return () => { if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; } };
  }, [jobs]);

  const handleStart = async () => {
    const pathList = paths
      .split("\n")
      .map(p => p.trim())
      .map(p => p.replace(/^['\"]|['\"]$/g, ""))
      .filter(Boolean);
    if (pathList.length === 0) { setError("Enter at least one BAG file path or directory"); return; }
    if (!backendOnline) { setError("Backend is offline. Start backend/Tauri and try again."); return; }
    setError(null);
    setSubmitting(true);
    try {
      const result = await startScan({
        paths: pathList,
        output_dir: outputDir,
        config: { 
          ...config,
          auto_restore: autoRestore,
          use_vision_ai: useVisionAI,
          scanner_mode: scannerMode
        },
      });
      setJobs(prev => [{
        id: result.job_id,
        status: result.status,
        created: Date.now() / 1000,
        paths: pathList.join(", "),
      }, ...prev]);
      setPaths("");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to start scan — is the API running?");
    } finally {
      setSubmitting(false);
    }
  };

  const fmtTime = (t?: number) => t ? new Date(t * 1000).toLocaleTimeString() : "—";
  const fmtDuration = (start?: number, end?: number) => {
    if (!start) return "";
    const d = (end || Date.now() / 1000) - start;
    return `${d.toFixed(1)}s`;
  };



  const handleSelectOutputDir = async () => {
    const dirs = await pickFolder({ title: "Select output directory" });
    if (dirs.length > 0) setOutputDir(dirs[0]);
  };

  const latestCompletedJob = jobs.find(j => j.status === "completed");

  const loadResults = async (jobId: string) => {
    try {
      const results = await getScanResults(jobId);
      setScanResults(results);
      setSelectedJobId(jobId);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load results");
    }
  };

  const handleRestore = async (candidateIndex: number) => {
    if (!selectedJobId) return;
    setRestoringIdx(candidateIndex);
    setRestoreStatus("starting…");
    try {
      const resp = await scanToRestore(selectedJobId, candidateIndex);
      setRestoreStatus(`Restoration queued (${resp.job_id.slice(0, 8)}…)`);
      // Poll for completion
      const pollRestore = setInterval(async () => {
        try {
          const s = await getRestorationStatus(resp.job_id);
          if (s.status === "completed") {
            clearInterval(pollRestore);
            setRestoreStatus(`✓ Restoration complete`);
            setRestoringIdx(null);
          } else if (s.status === "failed") {
            clearInterval(pollRestore);
            setRestoreStatus(`✗ Failed: ${s.error || "unknown"}`);
            setRestoringIdx(null);
          } else {
            setRestoreStatus(`Restoring… (${s.status})`);
          }
        } catch { /* keep polling */ }
      }, 2000);
    } catch (e: unknown) {
      setRestoreStatus(null);
      setRestoringIdx(null);
      setError(e instanceof Error ? e.message : "Failed to start restoration");
    }
  };

  const openPath = async (path: string) => {
    try {
      const mod = await import("@tauri-apps/api/core");
      await mod.invoke("open_output_path", { path });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : `Failed to open path: ${path}`);
    }
  };

  const handleOpenOutput = async () => {
    await openPath(outputDir);
  };

  const handleLaunchResults = async () => {
    await openPath(outputDir);
  };



  const handleNamePrompt = () => {
    setShowNamePrompt(true);
  };

  const handleNameConfirm = () => {
    setShowNamePrompt(false);
  };

  // ── File / folder pickers ──────────────────────
  const [dragging, setDragging] = useState(false);

  const handleBrowseFiles = async () => {
    const files = await pickFiles({ multiple: true, accept: ".bag", extensions: ["bag"], title: "Select BAG Files" });
    if (files.length > 0) {
      setPaths(prev => {
        const existing = prev.trim();
        const joined = files.join("\n");
        return existing ? existing + "\n" + joined : joined;
      });
    }
  };

  const handleBrowseFolder = async () => {
    const dirs = await pickFolder({ multiple: true, title: "Select BAG folder" });
    if (dirs.length > 0) {
      setPaths(prev => {
        const existing = prev.trim();
        const joined = dirs.join("\n");
        return existing ? existing + "\n" + joined : joined;
      });
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const newPaths = getDroppedPaths(e);
    if (newPaths.length > 0) {
      setPaths(prev => {
        const existing = prev.trim();
        const joined = newPaths.join("\n");
        return existing ? existing + "\n" + joined : joined;
      });
    }
  };

  // Flatten candidates from results for the table
  const allCandidates: (ScanCandidate & { _globalIdx: number })[] = [];
  if (scanResults?.result?.results) {
    let idx = 0;
    for (const fr of scanResults.result.results) {
      for (const c of (fr.candidates || [])) {
        allCandidates.push({ ...c, source_file: c.source_file || fr.file, _globalIdx: idx++ });
      }
    }
  }
  const swayzeMatches: SwayzeMatch[] = scanResults?.swayze_matches || scanResults?.result?.swayze_matches || [];
  const exportFiles = scanResults?.export_files || scanResults?.result?.export_files || {};

  return (
    <div style={{ padding: 16, maxWidth: 900 }}>
      <h2 style={{ marginBottom: 16 }}>BAG File Scanner</h2>

      {/* ── Path input ──────────────────────────────── */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <label style={{ fontWeight: 600 }}>BAG file paths or directories (one per line)</label>

        {/* Drop zone + textarea */}
        <div
          onDragOver={e => { e.preventDefault(); setDragging(true); }}
          onDragEnter={e => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          style={{
            position: "relative",
            marginTop: 8,
            border: dragging ? "2px dashed var(--accent)" : "1px solid var(--border)",
            borderRadius: "var(--radius)",
            background: dragging ? "rgba(100,181,246,0.08)" : "var(--bg)",
            transition: "border 0.15s, background 0.15s",
          }}
        >
          {dragging && (
            <div style={{
              position: "absolute", inset: 0, display: "flex",
              alignItems: "center", justifyContent: "center",
              background: "rgba(100,181,246,0.12)", borderRadius: "var(--radius)",
              pointerEvents: "none", zIndex: 2,
              fontSize: 14, fontWeight: 600, color: "var(--accent)",
            }}>
              Drop BAG files or folders here
            </div>
          )}
          <textarea
            value={paths}
            onChange={e => setPaths(e.target.value)}
            rows={4}
            placeholder={"Drop BAG files here, or browse below\nC:\\path\\to\\bag_files\nC:\\another\\file.bag"}
            style={{
              width: "100%", padding: 8,
              background: "transparent", color: "var(--text)",
              border: "none", outline: "none",
              fontFamily: "var(--mono)", fontSize: 13, resize: "vertical",
            }}
          />
        </div>

        {/* Browse buttons */}
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <button onClick={handleBrowseFiles} style={{ padding: "4px 12px", fontSize: 13 }}>
            Browse BAG Files…
          </button>
          <button onClick={handleBrowseFolder} style={{ padding: "4px 12px", fontSize: 13 }}>
            Browse Folder…
          </button>
          {paths.trim() && (
            <button
              onClick={() => setPaths("")}
              style={{ padding: "4px 12px", fontSize: 13, marginLeft: "auto", color: "var(--text-dim)" }}
            >
              Clear
            </button>
          )}
        </div>

        <div style={{ display: "flex", gap: 12, marginTop: 12, alignItems: "center" }}>
          <label style={{ fontSize: 13 }}>Output directory</label>
          <input
            value={outputDir}
            onChange={e => setOutputDir(e.target.value)}
            style={{
              flex: 1, padding: "4px 8px",
              background: "var(--bg)", color: "var(--text)",
              border: "1px solid var(--border)", borderRadius: "var(--radius)",
              fontFamily: "var(--mono)", fontSize: 13,
            }}
          />
          <button onClick={handleSelectOutputDir} style={{ padding: "4px 12px", fontSize: 13 }}>Select Folder…</button>
        </div>

        {showNamePrompt && (
          <div style={{ marginTop: 12 }}>
            <label>Batch output name:</label>
            <input value={outputName} onChange={e => setOutputName(e.target.value)} style={{ marginLeft: 8 }} />
            <button onClick={handleNameConfirm} style={{ marginLeft: 8 }}>OK</button>
          </div>
        )}

        <div style={{ marginTop: 16, display: "flex", gap: 12, alignItems: "center" }}>
          <label style={{ fontWeight: 600, fontSize: 13 }}>Scanner Engine:</label>
          <select 
            value={scannerMode}
            onChange={(e) => setScannerMode(e.target.value as "rust_fast" | "black_hole")}
            style={{
              padding: "4px 8px", background: "var(--bg)", color: "var(--text)",
              border: "1px solid var(--border)", borderRadius: "var(--radius)",
              fontSize: 13
            }}
          >
            <option value="rust_fast">Fast Rust Engine (General Wrecks)</option>
            <option value="black_hole">Deep Interpolation (Black Hole Redactions)</option>
          </select>
        </div>

        {/* ── Advanced config ──────────────────────── */}
        <button
          onClick={() => setShowAdvanced(!showAdvanced)}
          style={{
            marginTop: 12, background: "none", border: "none",
            color: "var(--accent)", cursor: "pointer", fontSize: 13,
          }}
        >
          {showAdvanced ? "▾ Hide" : "▸ Show"} advanced config
        </button>

        {showAdvanced && (
          <div style={{
            display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 16px",
            marginTop: 8, fontSize: 13,
          }}>
            {(Object.entries(config) as [keyof ScanConfig, number][]).map(([key, val]) => (
              <label key={key} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span style={{ color: "var(--text-dim)" }}>{key.replace(/_/g, " ")}</span>
                <input
                  type="number"
                  step={key.includes("confidence") || key.includes("threshold") ? 0.1 : 1}
                  value={val}
                  onChange={e => setConfig(c => ({ ...c, [key]: parseFloat(e.target.value) || 0 }))}
                  style={{
                    padding: "4px 8px", background: "var(--bg)", color: "var(--text)",
                    border: "1px solid var(--border)", borderRadius: "var(--radius)",
                    fontFamily: "var(--mono)",
                  }}
                />
              </label>
            ))}
          </div>
        )}

        {error && (
          <div style={{ marginTop: 12, color: "var(--red)", fontSize: 13 }}>
            ⚠ {error}
          </div>
        )}

        {!backendOnline && backendError && (
          <div style={{ marginTop: 8, color: "var(--text-dim)", fontSize: 12 }}>
            Backend detail: {backendError}
          </div>
        )}

        <div style={{ marginTop: 12, display: "flex", gap: "16px", alignItems: "center" }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={autoRestore}
              onChange={e => setAutoRestore(e.target.checked)}
            />
            Auto-Restore Masked Areas
          </label>
          {autoRestore && (
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, cursor: "pointer", color: "var(--accent)" }}>
              <input
                type="checkbox"
                checked={useVisionAI}
                onChange={e => setUseVisionAI(e.target.checked)}
              />
              Use Vision AI for Restoration
            </label>
          )}
        </div>

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
          {submitting ? "Starting…" : (!backendOnline ? "Start Scan (Backend Offline)" : "Start Scan")}
        </button>
        {jobs.length > 0 && (
          <div style={{ marginTop: 16, display: "flex", gap: 12 }}>
            <button onClick={handleOpenOutput} style={{ padding: "6px 18px" }}>Open Output Folder</button>
            <button onClick={handleLaunchResults} style={{ padding: "6px 18px" }}>Open Latest Results</button>
          </div>
        )}
      </div>

      {/* ── Job history ─────────────────────────────── */}
      {jobs.length > 0 && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <h3 style={{ marginBottom: 12 }}>Scan Jobs</h3>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                <th style={{ padding: "6px 8px" }}>Status</th>
                <th style={{ padding: "6px 8px" }}>Job ID</th>
                <th style={{ padding: "6px 8px" }}>Started</th>
                <th style={{ padding: "6px 8px" }}>Duration</th>
                <th style={{ padding: "6px 8px" }}>Paths</th>
                <th style={{ padding: "6px 8px" }}></th>
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
                    }`}>
                      {j.status}
                    </span>
                  </td>
                  <td style={{ padding: "6px 8px", fontFamily: "var(--mono)", fontSize: 11 }}>
                    {j.id.slice(0, 8)}…
                  </td>
                  <td style={{ padding: "6px 8px" }}>{fmtTime(j.start_time)}</td>
                  <td style={{ padding: "6px 8px" }}>{fmtDuration(j.start_time, j.end_time)}</td>
                  <td style={{ padding: "6px 8px", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {j.paths || "—"}
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    {j.status === "completed" && (
                      <button
                        onClick={() => loadResults(j.id)}
                        style={{ padding: "2px 10px", fontSize: 12 }}
                      >
                        View Results
                      </button>
                    )}
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

      {/* ── Scan Results ─────────────────────────────── */}
      {scanResults && scanResults.status === "completed" && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <h3 style={{ marginBottom: 8 }}>
            Scan Results
            <span style={{ fontSize: 13, fontWeight: 400, marginLeft: 12, color: "var(--text-dim)" }}>
              {scanResults.total_candidates ?? 0} candidates · {scanResults.total_signatures ?? 0} signatures · {swayzeMatches.length} Swayze matches
            </span>
          </h3>

          {/* Export file links */}
          {Object.keys(exportFiles).length > 0 && (
            <div style={{ display: "flex", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
              {exportFiles.json && (
                <button onClick={() => openPath(exportFiles.json!)} style={{ padding: "4px 12px", fontSize: 12 }}>
                  📄 Open JSON
                </button>
              )}
              {exportFiles.csv && (
                <button onClick={() => openPath(exportFiles.csv!)} style={{ padding: "4px 12px", fontSize: 12 }}>
                  📊 Open CSV Table
                </button>
              )}
              {exportFiles.kml && (
                <button onClick={() => openPath(exportFiles.kml!)} style={{ padding: "4px 12px", fontSize: 12 }}>
                  🌍 Open KML
                </button>
              )}
              {exportFiles.kmz && (
                <button onClick={() => openPath(exportFiles.kmz!)} style={{ padding: "4px 12px", fontSize: 12 }}>
                  🌍 Open KMZ
                </button>
              )}
            </div>
          )}

          {/* Candidates table */}
          {allCandidates.length > 0 ? (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: "2px solid var(--border)", textAlign: "left" }}>
                    <th style={{ padding: "6px 6px" }}>#</th>
                    <th style={{ padding: "6px 6px" }}>File</th>
                    <th style={{ padding: "6px 6px" }}>Position</th>
                    <th style={{ padding: "6px 6px" }}>Conf</th>
                    <th style={{ padding: "6px 6px" }}>Size (m²)</th>
                    <th style={{ padding: "6px 6px" }}>Dims</th>
                    <th style={{ padding: "6px 6px" }}>Anomaly</th>
                    <th style={{ padding: "6px 6px" }}>Swayze Match</th>
                    <th style={{ padding: "6px 6px" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {allCandidates.map((c, i) => {
                    const candMatches = swayzeMatches.filter(m => m.candidate_index === c._globalIdx);
                    const best = candMatches[0];
                    return (
                      <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                        <td style={{ padding: "4px 6px" }}>{i + 1}</td>
                        <td style={{ padding: "4px 6px", fontFamily: "var(--mono)", fontSize: 11, maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {c.source_file}
                        </td>
                        <td style={{ padding: "4px 6px", fontFamily: "var(--mono)", fontSize: 11 }}>
                          {c.latitude?.toFixed(5)}, {c.longitude?.toFixed(5)}
                        </td>
                        <td style={{ padding: "4px 6px" }}>
                          <span className={`badge badge-${c.confidence > 0.7 ? "strong" : c.confidence > 0.4 ? "moderate" : "weak"}`}>
                            {(c.confidence * 100).toFixed(0)}%
                          </span>
                        </td>
                        <td style={{ padding: "4px 6px" }}>{c.size_sq_meters?.toFixed(0)}</td>
                        <td style={{ padding: "4px 6px", fontSize: 11 }}>
                          {c.width_meters?.toFixed(1)}×{c.height_meters?.toFixed(1)}m
                        </td>
                        <td style={{ padding: "4px 6px" }}>{c.anomaly_score?.toFixed(2)}</td>
                        <td style={{ padding: "4px 6px", fontSize: 11 }}>
                          {best ? (
                            <span title={`${best.distance_m}m away · score ${best.match_score}`}>
                              <strong>{best.name}</strong>
                              <span style={{ color: "var(--text-dim)", marginLeft: 4 }}>
                                ({best.distance_m}m, {(best.match_score * 100).toFixed(0)}%)
                              </span>
                              {candMatches.length > 1 && (
                                <span style={{ color: "var(--text-dim)" }}> +{candMatches.length - 1}</span>
                              )}
                            </span>
                          ) : (
                            <span style={{ color: "var(--text-dim)" }}>—</span>
                          )}
                        </td>
                        <td style={{ padding: "4px 6px" }}>
                          <button
                            onClick={() => handleRestore(c._globalIdx)}
                            disabled={restoringIdx === c._globalIdx}
                            style={{ padding: "2px 8px", fontSize: 11 }}
                            title="Send this candidate region to BAG depth restoration / unmasking"
                          >
                            {restoringIdx === c._globalIdx ? "…" : "Unmask"}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div style={{ color: "var(--text-dim)", fontSize: 13 }}>No candidates found.</div>
          )}

          {restoreStatus && (
            <div style={{ marginTop: 8, fontSize: 12, color: restoreStatus.startsWith("✗") ? "var(--red)" : "var(--text-dim)" }}>
              {restoreStatus}
            </div>
          )}
        </div>
      )}

      {/* ── Swayze Matches Detail ────────────────────── */}
      {swayzeMatches.length > 0 && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <h3 style={{ marginBottom: 8 }}>
            Nearby Swayze Wrecks ({swayzeMatches.length})
          </h3>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: "2px solid var(--border)", textAlign: "left" }}>
                  <th style={{ padding: "5px 6px" }}>Name</th>
                  <th style={{ padding: "5px 6px" }}>Date</th>
                  <th style={{ padding: "5px 6px" }}>Type</th>
                  <th style={{ padding: "5px 6px" }}>Hull</th>
                  <th style={{ padding: "5px 6px" }}>Class</th>
                  <th style={{ padding: "5px 6px" }}>Length</th>
                  <th style={{ padding: "5px 6px" }}>Mag Wt</th>
                  <th style={{ padding: "5px 6px" }}>Dist (m)</th>
                  <th style={{ padding: "5px 6px" }}>Score</th>
                  <th style={{ padding: "5px 6px" }}>Cand #</th>
                </tr>
              </thead>
              <tbody>
                {swayzeMatches.slice(0, 50).map((m, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "4px 6px", fontWeight: 600 }}>{m.name}</td>
                    <td style={{ padding: "4px 6px" }}>{m.date || "—"}</td>
                    <td style={{ padding: "4px 6px" }}>{m.feature_type || "—"}</td>
                    <td style={{ padding: "4px 6px" }}>{m.hull_material || "—"}</td>
                    <td style={{ padding: "4px 6px" }}>{m.vessel_class || "—"}</td>
                    <td style={{ padding: "4px 6px" }}>{m.length_ft ? `${m.length_ft} ft` : "—"}</td>
                    <td style={{ padding: "4px 6px" }}>{m.magnetic_weight ?? "—"}</td>
                    <td style={{ padding: "4px 6px" }}>{m.distance_m}</td>
                    <td style={{ padding: "4px 6px" }}>
                      <span className={`badge badge-${m.match_score > 0.6 ? "strong" : m.match_score > 0.35 ? "moderate" : "weak"}`}>
                        {(m.match_score * 100).toFixed(0)}%
                      </span>
                    </td>
                    <td style={{ padding: "4px 6px" }}>{m.candidate_index + 1}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Info ────────────────────────────────────── */}
      <div style={{ marginTop: 16, fontSize: 12, color: "var(--text-dim)" }}>
        The BAG scanner detects redacted areas via multi-resolution anomaly detection, generates
        KML/KMZ with polygon overlays, exports JSON & CSV results, and cross-references candidates
        against the Swayze wrecks database. Use the <strong>Unmask</strong> button to send candidates
        to the BAG depth restoration pipeline. Paths can be directories (recursively scanned for *.bag)
        or individual .bag files.
      </div>
    </div>
  );
}
