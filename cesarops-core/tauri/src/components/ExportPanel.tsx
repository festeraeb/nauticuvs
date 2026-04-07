import { useState } from "react";
import { pickFiles, pickSaveLocation } from "../services/dialog";
import { generateKmz, generateKml } from "../services/api";

let invokeFn: ((cmd: string, args?: Record<string, unknown>) => Promise<unknown>) | null = null;
try { import("@tauri-apps/api/core").then(m => { invokeFn = m.invoke; }).catch(() => {}); } catch {}

export default function ExportPanel() {
  const [scanResultsPath, setScanResultsPath] = useState("");
  const [wrecksDbPath, setWrecksDbPath] = useState("db/wrecks.db");
  const [outputPath, setOutputPath] = useState("");
  const [searchRadius, setSearchRadius] = useState(1000);
  const [format, setFormat] = useState<"kmz" | "kml">("kmz");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handlePickScanResults = async () => {
    const files = await pickFiles({ multiple: false, accept: ".json", extensions: ["json"], title: "Select scan results JSON" });
    if (files.length > 0) setScanResultsPath(files[0]);
  };

  const handlePickWrecksDb = async () => {
    const files = await pickFiles({ multiple: false, accept: ".db,.sqlite", extensions: ["db", "sqlite"], title: "Select wrecks database" });
    if (files.length > 0) setWrecksDbPath(files[0]);
  };

  const pickOutput = async () => {
    const ext = format;
    const selected = await pickSaveLocation({
      title: `Save ${ext.toUpperCase()} file`,
      filters: [{ name: ext.toUpperCase(), extensions: [ext] }],
      defaultPath: `wreck_export.${ext}`,
    });
    if (selected) setOutputPath(selected);
  };

  const handleExport = async () => {
    if (!scanResultsPath.trim()) { setError("Select a scan results JSON file."); return; }
    if (!outputPath.trim()) { setError("Choose an output path."); return; }
    setError(null);
    setResult(null);
    setBusy(true);
    try {
      let out: string;
      if (format === "kmz") {
        out = await generateKmz(scanResultsPath.trim(), wrecksDbPath.trim(), outputPath.trim(), searchRadius);
      } else {
        out = await generateKml(scanResultsPath.trim(), wrecksDbPath.trim(), outputPath.trim(), searchRadius);
      }
      setResult(out);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const openResult = async (path: string) => {
    try {
      if (invokeFn) await invokeFn("open_output_path", { path });
    } catch { /* no Tauri */ }
  };

  return (
    <div>
      <h2 style={{ marginBottom: 4 }}>KML / KMZ Export</h2>
      <p style={{ color: "var(--text-dim)", fontSize: 13, marginBottom: 20 }}>
        Combine BAG scan results with the Swayze wreck database into a Google Earth file.
      </p>

      <div className="tool-form">
        {/* Format */}
        <div className="form-row">
          <label className="form-label">Format</label>
          <div style={{ display: "flex", gap: 12 }}>
            {(["kmz", "kml"] as const).map(f => (
              <label key={f} style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                <input type="radio" value={f} checked={format === f} onChange={() => setFormat(f)} />
                <span style={{ textTransform: "uppercase", fontWeight: 600 }}>{f}</span>
                <span style={{ color: "var(--text-dim)", fontSize: 12 }}>
                  {f === "kmz" ? "(compressed, recommended)" : "(plain XML)"}
                </span>
              </label>
            ))}
          </div>
        </div>

        {/* Scan results */}
        <div className="form-row">
          <label className="form-label">Scan Results JSON</label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              className="form-input"
              type="text"
              value={scanResultsPath}
              onChange={e => setScanResultsPath(e.target.value)}
              placeholder="advanced_scan_results/scan_results_*.json"
              style={{ flex: 1 }}
            />
            <button className="btn-secondary" onClick={handlePickScanResults}>Browse…</button>
          </div>
          <div className="form-hint">JSON output from the BAG Scanner (contains candidate positions)</div>
        </div>

        {/* Wrecks DB */}
        <div className="form-row">
          <label className="form-label">Wrecks Database</label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              className="form-input"
              type="text"
              value={wrecksDbPath}
              onChange={e => setWrecksDbPath(e.target.value)}
              placeholder="db/wrecks.db"
              style={{ flex: 1 }}
            />
            <button className="btn-secondary" onClick={handlePickWrecksDb}>Browse…</button>
          </div>
          <div className="form-hint">Swayze wrecks SQLite database (default: db/wrecks.db)</div>
        </div>

        {/* Output path */}
        <div className="form-row">
          <label className="form-label">Output Path</label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              className="form-input"
              type="text"
              value={outputPath}
              onChange={e => setOutputPath(e.target.value)}
              placeholder={`exports/wreck_export.${format}`}
              style={{ flex: 1 }}
            />
            <button className="btn-secondary" onClick={pickOutput}>Save As…</button>
          </div>
        </div>

        {/* Search radius */}
        <div className="form-row">
          <label className="form-label">Match Radius</label>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <input
              type="range" min={250} max={5000} step={250}
              value={searchRadius}
              onChange={e => setSearchRadius(Number(e.target.value))}
              style={{ width: 200 }}
            />
            <span style={{ fontFamily: "var(--mono)", fontSize: 13 }}>{searchRadius.toLocaleString()} m</span>
          </div>
          <div className="form-hint">Radius to match scan candidates against Swayze DB records</div>
        </div>

        {error && <div className="error-banner">{error}</div>}

        <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
          <button
            className="btn-primary"
            onClick={handleExport}
            disabled={busy}
          >
            {busy ? "Generating…" : `Generate ${format.toUpperCase()}`}
          </button>
        </div>

        {result && (
          <div className="success-banner" style={{ marginTop: 16 }}>
            <div style={{ marginBottom: 6, fontWeight: 600 }}>Export complete</div>
            <div style={{ fontFamily: "var(--mono)", fontSize: 12, marginBottom: 8, wordBreak: "break-all" }}>{result}</div>
            <button className="btn-secondary" onClick={() => openResult(result)}>Open in Explorer</button>
          </div>
        )}
      </div>
    </div>
  );
}
