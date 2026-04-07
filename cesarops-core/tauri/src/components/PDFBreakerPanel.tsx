import { useState, useEffect, useRef } from "react";
import { pickFolder } from "../services/dialog";
import type { ToolJobStatus } from "../types";
import { getApiBase, resetConnectionState, startPdfBreaker, getPdfBreakerStatus } from "../services/api";

export default function PDFBreakerPanel() {
  const [paths, setPaths] = useState("");
  const [outputDir, setOutputDir] = useState("pdf_breaker_output");
  const [saveImages, setSaveImages] = useState(false);
  const [skipOcr, setSkipOcr] = useState(true);
  const [jobs, setJobs] = useState<(ToolJobStatus & { paths?: string })[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [backendOnline, setBackendOnline] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

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
          if (j.status === "completed" || j.status === "failed" || j.status === "skipped") return j;
          try { return { ...j, ...(await getPdfBreakerStatus(j.id)) }; }
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

  const handleStart = async () => {
    const pathList = paths.split("\n").map(p => p.trim()).map(p => p.replace(/^['"]|['"]$/g, "")).filter(Boolean);
    if (pathList.length === 0) { setError("Enter at least one PDF file or directory."); return; }
    if (!backendOnline) { setError("Backend is offline."); return; }
    setError(null);
    setSubmitting(true);
    try {
      const result = await startPdfBreaker({
        paths: pathList,
        output_dir: outputDir,
        config: { pdf_save_images: saveImages, pdf_skip_ocr: skipOcr },
      });
      setJobs(prev => [{
        id: result.job_id, tool: "pdf_breaker", status: result.status,
        created: Date.now() / 1000, paths: pathList.join(", "),
      }, ...prev]);
      setPaths("");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to start PDF breaker");
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

  const fmtTime = (t?: number) => t ? new Date(t * 1000).toLocaleTimeString() : "\u2014";
  const fmtDuration = (start?: number, end?: number) => {
    if (!start) return "";
    const d = (end || Date.now() / 1000) - start;
    return `${d.toFixed(1)}s`;
  };

  return (
    <div style={{ padding: 16, maxWidth: 800 }}>
      <h2 style={{ marginBottom: 16 }}>PDF Redaction Breaker</h2>

      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <label style={{ fontWeight: 600 }}>PDF files or directories (one per line)</label>
        <textarea
          value={paths}
          onChange={e => setPaths(e.target.value)}
          rows={4}
          placeholder={"C:\\path\\to\\pdfs\nC:\\another\\report.pdf"}
          style={{
            width: "100%", marginTop: 8, padding: 8,
            background: "var(--bg)", color: "var(--text)",
            border: "1px solid var(--border)", borderRadius: "var(--radius)",
            fontFamily: "var(--mono)", fontSize: 13, resize: "vertical",
          }}
        />

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
          <button onClick={handleSelectOutputDir} style={{ padding: "4px 12px", fontSize: 13 }}>Select Folder\u2026</button>
        </div>

        <div style={{ display: "flex", gap: 24, marginTop: 12, fontSize: 13 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={saveImages} onChange={e => setSaveImages(e.target.checked)} />
            Save extracted images
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={skipOcr} onChange={e => setSkipOcr(e.target.checked)} />
            Skip OCR (faster)
          </label>
        </div>

        {error && (
          <div style={{ marginTop: 12, color: "var(--red)", fontSize: 13 }}>\u26A0 {error}</div>
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
          {submitting ? "Starting\u2026" : (!backendOnline ? "Run (Backend Offline)" : "Run PDF Breaker")}
        </button>
      </div>

      {/* Job history */}
      {jobs.length > 0 && (
        <div className="card" style={{ padding: 16 }}>
          <h3 style={{ marginBottom: 12 }}>PDF Breaker Jobs</h3>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                <th style={{ padding: "6px 8px" }}>Status</th>
                <th style={{ padding: "6px 8px" }}>Job ID</th>
                <th style={{ padding: "6px 8px" }}>Started</th>
                <th style={{ padding: "6px 8px" }}>Duration</th>
                <th style={{ padding: "6px 8px" }}>PDFs</th>
                <th style={{ padding: "6px 8px" }}>Findings</th>
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
                  <td style={{ padding: "6px 8px", fontFamily: "var(--mono)", fontSize: 11 }}>{j.id.slice(0, 8)}\u2026</td>
                  <td style={{ padding: "6px 8px" }}>{fmtTime(j.start_time)}</td>
                  <td style={{ padding: "6px 8px" }}>{fmtDuration(j.start_time, j.end_time)}</td>
                  <td style={{ padding: "6px 8px" }}>{j.result?.pdfs_analyzed as number ?? "\u2014"}</td>
                  <td style={{ padding: "6px 8px" }}>{j.result?.target_hits as number ?? "\u2014"}</td>
                  <td style={{ padding: "6px 8px" }}>
                    <button
                      onClick={() => j.result?.output_json && openPath(j.result.output_json as string)}
                      disabled={!j.result?.output_json}
                      style={{ fontSize: 11, padding: "2px 8px", opacity: j.result?.output_json ? 1 : 0.5 }}
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
        The PDF Redaction Breaker analyzes government PDF documents for redacted or masked content,
        extracting hidden text layers and identifying redaction zones. Point it at a directory of PDFs
        or individual files to analyze.
      </div>
    </div>
  );
}
