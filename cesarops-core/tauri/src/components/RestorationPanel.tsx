import { useState, useEffect, useRef } from "react";
import { pickFiles, pickFolder } from "../services/dialog";
import type { ToolJobStatus, AzureVisionStatus } from "../types";
import {
  getApiBase,
  resetConnectionState,
  startRestoration,
  getRestorationStatus,
  getAzureVisionStatus,
  configureAzureVision,
  startVisionAnalysis,
  getVisionAnalysisStatus,
  generateKmz,
} from "../services/api";

// Tauri invoke for opening paths
let invokeFn: ((cmd: string, args?: Record<string, unknown>) => Promise<unknown>) | null = null;
import("@tauri-apps/api/core").then(m => { invokeFn = m.invoke; }).catch(() => {});

export default function RestorationPanel() {
  const [bagPath, setBagPath] = useState("");
  const [outputDir, setOutputDir] = useState("restoration_output");
  const [amplification, setAmplification] = useState(3.0);
  const [sigma, setSigma] = useState(2.0);

  const [jobs, setJobs] = useState<ToolJobStatus[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [backendOnline, setBackendOnline] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [visionStatus, setVisionStatus] = useState<AzureVisionStatus | null>(null);
  const [kmzBusy, setKmzBusy] = useState(false);
  const [kmzResult, setKmzResult] = useState<string | null>(null);
  const [showKeyEntry, setShowKeyEntry] = useState(false);
  const [visionKeyInput, setVisionKeyInput] = useState("");
  const [keySaving, setKeySaving] = useState(false);
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

  // Check Azure Vision availability on mount
  useEffect(() => {
    if (!backendOnline) return;
    getAzureVisionStatus().then(setVisionStatus).catch(() => {});
  }, [backendOnline]);

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
            if (j.tool === "azure_vision") return await getVisionAnalysisStatus(j.id);
            return await getRestorationStatus(j.id);
          } catch { return j; }
        })
      );
      setJobs(updated);
    }, 2000);
    return () => { if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; } };
  }, [jobs]);

  async function pickBagFile() {
    const files = await pickFiles({ multiple: false, accept: ".bag", extensions: ["bag"], title: "Select BAG file" });
    if (files.length > 0) setBagPath(files[0]);
  }

  async function pickOutputDir() {
    const dirs = await pickFolder({ title: "Select output directory" });
    if (dirs.length > 0) setOutputDir(dirs[0]);
  }

  async function handleStartRestoration() {
    if (!bagPath.trim()) { setError("Select a BAG file first"); return; }
    setError(null);
    setSubmitting(true);
    try {
      const job = await startRestoration({
        bag_path: bagPath.trim(),
        output_dir: outputDir.trim(),
        amplification,
        sigma,
      });
      setJobs(prev => [{
        id: job.job_id, tool: "bag_restoration", status: job.status,
        created: Date.now() / 1000,
      }, ...prev]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleVisionAnalysis(restoreJob: ToolJobStatus) {
    if (!visionStatus?.configured) {
      setError("Azure AI Vision not configured. Set AZURE_VISION_ENDPOINT and AZURE_VISION_KEY.");
      return;
    }
    const result = restoreJob.result as Record<string, unknown> | undefined;
    if (!result) return;
    const bagStem = (result.bag_path as string)?.split(/[/\\]/).pop()?.replace(/\.bag$/i, "");
    if (!bagStem) return;
    try {
      const job = await startVisionAnalysis({
        output_dir: restoreJob.result?.output_dir as string ?? outputDir,
        bag_stem: bagStem,
      });
      setJobs(prev => [{
        id: job.job_id, tool: "azure_vision", status: job.status,
        created: Date.now() / 1000,
      }, ...prev]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleGenerateKmz(scanResultsPath: string) {
    setKmzBusy(true); setKmzResult(null); setError(null);
    try {
      const out = scanResultsPath.replace(/\.json$/i, ".kmz");
      const wrecksDb = "db/wrecks.db";
      const result = await generateKmz(scanResultsPath, wrecksDb, out);
      setKmzResult(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setKmzBusy(false); }
  }

  function openPath(p: string) {
    invokeFn?.("open_output_path", { path: p }).catch(() => {});
  }

  const statusColor = (s: string) =>
    s === "completed" ? "#4caf50" : s === "failed" ? "#f44336" :
    s === "running" ? "#2196f3" : "#888";

  return (
    <div style={{ padding: 24, maxWidth: 900 }}>
      <h2>BAG Depth Restoration</h2>
      <p style={{ color: "#aaa", fontSize: 13, marginBottom: 16 }}>
        Restore masked/redacted BAG depth grid features using over-exaggeration,
        deconvolution, and AI vision analysis. Operates directly on BAG files — no PDF required.
      </p>

      {/* Backend status */}
      <div style={{ marginBottom: 12 }}>
        <span style={{ color: backendOnline ? "#4caf50" : "#f44336", fontWeight: 600 }}>
          {backendOnline ? "● Backend online" : "● Backend offline"}
        </span>
        {visionStatus && (
          <span
            style={{ marginLeft: 16, color: visionStatus.configured ? "#4caf50" : "#888", cursor: "pointer" }}
            onClick={() => setShowKeyEntry(v => !v)}
            title="Click to configure Azure Vision API key"
          >
            {visionStatus.configured ? "● Azure Vision ready" : "○ Azure Vision — click to configure"}
          </span>
        )}
        {!visionStatus && backendOnline && (
          <span
            style={{ marginLeft: 16, color: "#888", cursor: "pointer" }}
            onClick={() => setShowKeyEntry(v => !v)}
          >
            ○ Azure Vision — click to configure
          </span>
        )}
      </div>

      {/* Azure Vision key entry */}
      {showKeyEntry && (
        <div style={{
          marginBottom: 16, padding: 16, background: "#1e1e2e",
          border: "1px solid #444", borderRadius: 8,
        }}>
          <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 14 }}>
            🔑 Azure Computer Vision — Free Tier (F0)
          </div>
          <div style={{ fontSize: 12, color: "#aaa", marginBottom: 10 }}>
            Endpoint: <code style={{ color: "#79c0ff" }}>https://wreckhunter2000.cognitiveservices.azure.com/</code>
            <br />Region: <code style={{ color: "#79c0ff" }}>eastus</code>
            <br />Limits: 20 calls/min · 5,000 calls/month
            {visionStatus?.calls_this_session != null && (
              <span> · Used this session: {visionStatus.calls_this_session}</span>
            )}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              type="password"
              value={visionKeyInput}
              onChange={e => setVisionKeyInput(e.target.value)}
              placeholder="Paste your Azure Vision API key here"
              style={{
                flex: 1, padding: "7px 10px", borderRadius: 4,
                border: "1px solid #555", background: "#161622", color: "#eee",
                fontFamily: "var(--mono)", fontSize: 13,
              }}
            />
            <button
              disabled={keySaving || visionKeyInput.length < 10}
              onClick={async () => {
                setKeySaving(true);
                try {
                  const updated = await configureAzureVision(visionKeyInput.trim());
                  setVisionStatus(updated);
                  setError(null);
                  setShowKeyEntry(false);
                  setVisionKeyInput("");
                } catch (e) {
                  setError(e instanceof Error ? e.message : "Failed to save key");
                } finally {
                  setKeySaving(false);
                }
              }}
              style={{
                ...btnStyle, background: "#1976d2", color: "#fff",
                opacity: keySaving || visionKeyInput.length < 10 ? 0.5 : 1,
              }}
            >
              {keySaving ? "Saving…" : "Save Key"}
            </button>
          </div>
          {visionStatus?.configured && (
            <div style={{ marginTop: 8, fontSize: 12, color: "#4caf50" }}>
              ✓ Key is saved and active. SDK installed: {visionStatus.sdk_installed ? "yes" : "no — run: pip install azure-cognitiveservices-vision-computervision msrest"}
            </div>
          )}
        </div>
      )}

      {/* BAG file input */}
      <div style={{ marginBottom: 12 }}>
        <label style={{ fontWeight: 600 }}>BAG File:</label>
        <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
          <input
            value={bagPath}
            onChange={e => setBagPath(e.target.value)}
            placeholder="Path to .bag file"
            style={{ flex: 1, padding: "6px 10px", borderRadius: 4, border: "1px solid #555", background: "#1e1e2e", color: "#eee" }}
          />
          <button onClick={pickBagFile} style={btnStyle}>Browse</button>
        </div>
      </div>

      {/* Output dir */}
      <div style={{ marginBottom: 12 }}>
        <label style={{ fontWeight: 600 }}>Output Directory:</label>
        <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
          <input
            value={outputDir}
            onChange={e => setOutputDir(e.target.value)}
            style={{ flex: 1, padding: "6px 10px", borderRadius: 4, border: "1px solid #555", background: "#1e1e2e", color: "#eee" }}
          />
          <button onClick={pickOutputDir} style={btnStyle}>Browse</button>
        </div>
      </div>

      {/* Parameters */}
      <div style={{ display: "flex", gap: 24, marginBottom: 16 }}>
        <div>
          <label style={{ fontSize: 13 }}>Amplification: <b>{amplification}×</b></label>
          <input type="range" min={1} max={10} step={0.5} value={amplification}
            onChange={e => setAmplification(Number(e.target.value))} style={{ width: 140 }} />
        </div>
        <div>
          <label style={{ fontSize: 13 }}>Sigma: <b>{sigma}</b></label>
          <input type="range" min={0.5} max={6} step={0.5} value={sigma}
            onChange={e => setSigma(Number(e.target.value))} style={{ width: 140 }} />
        </div>
      </div>

      {/* Action buttons */}
      <div style={{ display: "flex", gap: 12, marginBottom: 16 }}>
        <button
          onClick={handleStartRestoration}
          disabled={!backendOnline || submitting || !bagPath.trim()}
          style={{ ...btnStyle, background: "#1976d2", color: "#fff", padding: "8px 20px" }}
        >
          {submitting ? "Starting..." : "Run Restoration"}
        </button>
      </div>

      {error && <div style={{ color: "#f44336", marginBottom: 12 }}>{error}</div>}
      {kmzResult && (
        <div style={{ color: "#4caf50", marginBottom: 12 }}>
          KMZ generated: <a href="#" onClick={() => openPath(kmzResult)} style={{ color: "#64b5f6" }}>{kmzResult}</a>
        </div>
      )}

      {/* Job results */}
      {jobs.length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid #444" }}>
              <th style={th}>Tool</th>
              <th style={th}>Status</th>
              <th style={th}>Techniques</th>
              <th style={th}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map(j => (
              <tr key={j.id} style={{ borderBottom: "1px solid #333" }}>
                <td style={td}>{j.tool === "azure_vision" ? "AI Vision" : "BAG Restore"}</td>
                <td style={{ ...td, color: statusColor(j.status) }}>{j.status}</td>
                <td style={td}>
                  {j.status === "completed" && j.tool === "bag_restoration" && j.result
                    ? (j.result as { techniques_run?: string[] }).techniques_run?.join(", ")
                    : "—"}
                </td>
                <td style={td}>
                  {j.status === "completed" && j.tool === "bag_restoration" && (
                    <>
                      <button onClick={() => {
                        const files = (j.result as { output_files?: Record<string, string> })?.output_files;
                        if (files) openPath(Object.values(files)[0] ?? outputDir);
                      }} style={smallBtn}>Open</button>
                      {visionStatus?.configured && (
                        <button onClick={() => handleVisionAnalysis(j)} style={smallBtn}>AI Analyze</button>
                      )}
                    </>
                  )}
                  {j.status === "completed" && j.tool === "azure_vision" && (
                    <span style={{ color: "#4caf50" }}>Done</span>
                  )}
                  {j.status === "failed" && (
                    <span style={{ color: "#f44336", fontSize: 12 }}>
                      {(j as { error?: string }).error?.slice(0, 60)}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  padding: "6px 14px", borderRadius: 4, border: "1px solid #555",
  background: "#2a2a3e", color: "#eee", cursor: "pointer",
};
const smallBtn: React.CSSProperties = {
  ...btnStyle, padding: "3px 8px", fontSize: 12, marginRight: 6,
};
const th: React.CSSProperties = { textAlign: "left", padding: "6px 8px" };
const td: React.CSSProperties = { padding: "6px 8px" };
