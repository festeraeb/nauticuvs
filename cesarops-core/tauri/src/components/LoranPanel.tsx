import { useState, useEffect } from "react";
import type { DatumCorrectionResult } from "../services/api";
import { datumCorrectSingle, datumCorrectBatch, datumListAnchors } from "../services/api";

export default function LoranPanel() {
  // Single-point correction
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [datum, setDatum] = useState<"nad27" | "wgs84">("nad27");
  const [result, setResult] = useState<DatumCorrectionResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Batch
  const [batchText, setBatchText] = useState("");
  const [batchResults, setBatchResults] = useState<unknown[] | null>(null);
  const [batchBusy, setBatchBusy] = useState(false);

  // Anchors
  const [anchors, setAnchors] = useState<Array<Record<string, unknown>> | null>(null);
  const [showAnchors, setShowAnchors] = useState(false);
  const [anchorStats, setAnchorStats] = useState<{ total: number; ready: number } | null>(null);

  useEffect(() => {
    datumListAnchors()
      .then(d => { setAnchorStats({ total: d.total, ready: d.ready }); })
      .catch(() => {});
  }, []);

  const handleCorrect = async () => {
    const latN = parseFloat(lat);
    const lonN = parseFloat(lon);
    if (isNaN(latN) || isNaN(lonN)) {
      setError("Enter valid latitude and longitude.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const r = await datumCorrectSingle(latN, lonN, datum);
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleBatch = async () => {
    setError(null);
    setBatchBusy(true);
    try {
      // Parse lines: "lat, lon" per line
      const candidates = batchText
        .split("\n")
        .map(line => line.trim())
        .filter(Boolean)
        .map(line => {
          const parts = line.split(/[,\s\t]+/);
          return { center_lat: parseFloat(parts[0]), center_lon: parseFloat(parts[1]) };
        })
        .filter(c => !isNaN(c.center_lat) && !isNaN(c.center_lon));

      if (candidates.length === 0) {
        setError("Enter at least one coordinate pair (lat, lon per line).");
        setBatchBusy(false);
        return;
      }

      const r = await datumCorrectBatch(candidates, datum);
      setBatchResults(r.results);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBatchBusy(false);
    }
  };

  const handleLoadAnchors = async () => {
    try {
      const d = await datumListAnchors();
      setAnchors(d.anchors);
      setShowAnchors(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div>
      <h2 style={{ marginBottom: 4 }}>Loran-C Datum Correction</h2>
      <p style={{ color: "var(--text-dim)", fontSize: 13, marginBottom: 16 }}>
        NAD27→WGS84 Molodensky transform + triangulated rubber-sheet correction
        for Loran-C spatial bias using verified Great Lakes anchors.
      </p>

      {anchorStats && (
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 16 }}>
          Anchor library: {anchorStats.total} total, {anchorStats.ready} verified + characterised (ready for rubber-sheeting)
        </div>
      )}

      {/* ── Single-point correction ──────────────────────── */}
      <div className="tool-form" style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>Single Point Correction</div>

        <div className="form-row">
          <label className="form-label">Input Datum</label>
          <div style={{ display: "flex", gap: 16 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 13 }}>
              <input type="radio" value="nad27" checked={datum === "nad27"} onChange={() => setDatum("nad27")} />
              NAD27 (Loran-C era surveys)
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 13 }}>
              <input type="radio" value="wgs84" checked={datum === "wgs84"} onChange={() => setDatum("wgs84")} />
              WGS84 (rubber-sheet only)
            </label>
          </div>
        </div>

        <div style={{ display: "flex", gap: 12 }}>
          <div className="form-row" style={{ flex: 1 }}>
            <label className="form-label">Latitude</label>
            <input
              className="form-input"
              type="text"
              value={lat}
              onChange={e => setLat(e.target.value)}
              placeholder="42.4250"
              style={{ fontFamily: "var(--mono)" }}
            />
          </div>
          <div className="form-row" style={{ flex: 1 }}>
            <label className="form-label">Longitude</label>
            <input
              className="form-input"
              type="text"
              value={lon}
              onChange={e => setLon(e.target.value)}
              placeholder="-80.8130"
              style={{ fontFamily: "var(--mono)" }}
            />
          </div>
        </div>

        {error && <div className="error-banner">{error}</div>}

        <button className="btn-primary" onClick={handleCorrect} disabled={busy}>
          {busy ? "Correcting…" : "Compute Correction"}
        </button>

        {result && (
          <div style={{ marginTop: 12 }}>
            <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "2px solid var(--border)", textAlign: "left" }}>
                  <th style={{ padding: "6px 8px" }}>Stage</th>
                  <th style={{ padding: "6px 8px" }}>Latitude</th>
                  <th style={{ padding: "6px 8px" }}>Longitude</th>
                  <th style={{ padding: "6px 8px" }}>Shift</th>
                </tr>
              </thead>
              <tbody>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "4px 8px", color: "var(--text-dim)" }}>Raw Input</td>
                  <td style={{ padding: "4px 8px", fontFamily: "var(--mono)" }}>{result.raw.lat.toFixed(6)}</td>
                  <td style={{ padding: "4px 8px", fontFamily: "var(--mono)" }}>{result.raw.lon.toFixed(6)}</td>
                  <td style={{ padding: "4px 8px" }}>—</td>
                </tr>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "4px 8px", color: "var(--text-dim)" }}>After Molodensky</td>
                  <td style={{ padding: "4px 8px", fontFamily: "var(--mono)" }}>{result.molodensky.lat.toFixed(6)}</td>
                  <td style={{ padding: "4px 8px", fontFamily: "var(--mono)" }}>{result.molodensky.lon.toFixed(6)}</td>
                  <td style={{ padding: "4px 8px", fontSize: 12, color: "var(--yellow)" }}>
                    ΔN {result.molodensky.delta_lat_m.toFixed(1)}m, ΔE {result.molodensky.delta_lon_m.toFixed(1)}m
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "4px 8px", fontWeight: 600, color: "var(--green)" }}>Corrected</td>
                  <td style={{ padding: "4px 8px", fontFamily: "var(--mono)", fontWeight: 600 }}>{result.corrected.lat.toFixed(6)}</td>
                  <td style={{ padding: "4px 8px", fontFamily: "var(--mono)", fontWeight: 600 }}>{result.corrected.lon.toFixed(6)}</td>
                  <td style={{ padding: "4px 8px", fontSize: 12, color: "var(--green)" }}>
                    Total: {result.total_shift_m.toFixed(1)}m
                  </td>
                </tr>
              </tbody>
            </table>

            {/* Rubber-sheet metadata */}
            <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-dim)" }}>
              Method: {String(result.rubber_sheet?.method ?? "—")}
              {result.rubber_sheet?.n_used != null && ` · ${result.rubber_sheet.n_used} anchors used`}
              {result.rubber_sheet?.delta_lat_m != null && (
                <span> · ΔN {String(result.rubber_sheet.delta_lat_m)}m, ΔE {String(result.rubber_sheet.delta_lon_m)}m (rubber-sheet)</span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── Batch correction ──────────────────────────────── */}
      <div className="tool-form" style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>Batch Correction</div>
        <div className="form-row">
          <label className="form-label">Coordinates (lat, lon per line)</label>
          <textarea
            value={batchText}
            onChange={e => setBatchText(e.target.value)}
            rows={5}
            placeholder={"42.4250, -80.8130\n42.4708, -80.6528\n41.7500, -82.2000"}
            style={{
              width: "100%", padding: 8,
              background: "var(--bg)", color: "var(--text)",
              border: "1px solid var(--border)", borderRadius: 6,
              fontFamily: "var(--mono)", fontSize: 13, resize: "vertical",
            }}
          />
        </div>
        <button className="btn-primary" onClick={handleBatch} disabled={batchBusy}>
          {batchBusy ? "Processing…" : "Correct Batch"}
        </button>

        {batchResults && (
          <div style={{ marginTop: 12, overflowX: "auto" }}>
            <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "2px solid var(--border)", textAlign: "left" }}>
                  <th style={{ padding: "4px 6px" }}>#</th>
                  <th style={{ padding: "4px 6px" }}>Raw Lat</th>
                  <th style={{ padding: "4px 6px" }}>Raw Lon</th>
                  <th style={{ padding: "4px 6px" }}>→ Corrected Lat</th>
                  <th style={{ padding: "4px 6px" }}>→ Corrected Lon</th>
                  <th style={{ padding: "4px 6px" }}>Total Shift</th>
                  <th style={{ padding: "4px 6px" }}>Method</th>
                </tr>
              </thead>
              <tbody>
                {batchResults.map((r: any, i: number) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "3px 6px" }}>{i + 1}</td>
                    <td style={{ padding: "3px 6px", fontFamily: "var(--mono)" }}>{r.center_lat?.toFixed(6)}</td>
                    <td style={{ padding: "3px 6px", fontFamily: "var(--mono)" }}>{r.center_lon?.toFixed(6)}</td>
                    <td style={{ padding: "3px 6px", fontFamily: "var(--mono)", color: "var(--green)" }}>{r.corrected_lat?.toFixed(6)}</td>
                    <td style={{ padding: "3px 6px", fontFamily: "var(--mono)", color: "var(--green)" }}>{r.corrected_lon?.toFixed(6)}</td>
                    <td style={{ padding: "3px 6px" }}>{r.datum_total_shift_m?.toFixed(1)}m</td>
                    <td style={{ padding: "3px 6px", color: "var(--text-dim)" }}>{r.datum_method}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Anchor library ──────────────────────────────── */}
      <div style={{ marginTop: 8 }}>
        <button
          className="btn-secondary"
          onClick={showAnchors ? () => setShowAnchors(false) : handleLoadAnchors}
        >
          {showAnchors ? "Hide Anchor Library" : "View Anchor Library"}
        </button>

        {showAnchors && anchors && (
          <div style={{ marginTop: 12, overflowX: "auto" }}>
            <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "2px solid var(--border)", textAlign: "left" }}>
                  <th style={{ padding: "4px 6px" }}>ID</th>
                  <th style={{ padding: "4px 6px" }}>Lake</th>
                  <th style={{ padding: "4px 6px" }}>Verified</th>
                  <th style={{ padding: "4px 6px" }}>GPS Position</th>
                  <th style={{ padding: "4px 6px" }}>Survey Pos</th>
                  <th style={{ padding: "4px 6px" }}>Shift</th>
                  <th style={{ padding: "4px 6px" }}>Source</th>
                </tr>
              </thead>
              <tbody>
                {anchors.map((a: any, i: number) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "3px 6px", fontWeight: 500 }}>{a.id}</td>
                    <td style={{ padding: "3px 6px" }}>{a.lake}</td>
                    <td style={{ padding: "3px 6px" }}>
                      <span className={`badge ${a.verified ? "badge-strong" : "badge-weak"}`}>
                        {a.verified ? "YES" : "no"}
                      </span>
                    </td>
                    <td style={{ padding: "3px 6px", fontFamily: "var(--mono)", fontSize: 11 }}>
                      {a.verified_gps ? `${a.verified_gps[0]?.toFixed(4)}, ${a.verified_gps[1]?.toFixed(4)}` : "—"}
                    </td>
                    <td style={{ padding: "3px 6px", fontFamily: "var(--mono)", fontSize: 11 }}>
                      {a.survey_pos ? `${a.survey_pos[0]?.toFixed(4)}, ${a.survey_pos[1]?.toFixed(4)}` : "MISSING"}
                    </td>
                    <td style={{ padding: "3px 6px", fontSize: 11 }}>
                      {a.shift_m ? `N${a.shift_m.north > 0 ? "+" : ""}${a.shift_m.north}m E${a.shift_m.east > 0 ? "+" : ""}${a.shift_m.east}m` : "—"}
                    </td>
                    <td style={{ padding: "3px 6px", fontSize: 11, color: "var(--text-dim)", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {a.source}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
