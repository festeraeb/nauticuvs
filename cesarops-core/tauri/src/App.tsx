import { useEffect, useState } from "react";
import type { Wreck, ActivePanel, MagPotential } from "./types";
import WreckList from "./components/WreckList";
import WreckDetail from "./components/WreckDetail";

import StatsPanel from "./components/StatsPanel";
import ScanPanel from "./components/ScanPanel";
import MagPipelinePanel from "./components/MagPipelinePanel";
import EriePanel from "./components/EriePanel";
import RestorationPanel from "./components/RestorationPanel";
import MapPanel from "./components/MapPanel";
import PDFBreakerPanel from "./components/PDFBreakerPanel";
import ExportPanel from "./components/ExportPanel";
import AgentPanel from "./components/AgentPanel";
import LoranPanel from "./components/LoranPanel";
import ExtendedSensorsPanel from "./components/ExtendedSensorsPanel";
import HarvesterPanel from "./components/HarvesterPanel";
import AutoBagPrompt from "./components/AutoBagPrompt";
import UpdateChecker from "./components/UpdateChecker";
import { getApiBase, resetConnectionState } from "./services/api";
import "./styles/global.css";

export default function App() {
  const [panel, setPanel] = useState<ActivePanel>("stats");
  const [selectedWreck, setSelectedWreck] = useState<Wreck | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [online, setOnline] = useState<boolean | null>(null);
  const [apiBase, setApiBase] = useState<string>("resolving...");

  // Quick-filter state — passed as initial values to WreckList
  const [quickSteelOnly, setQuickSteelOnly] = useState(false);
  const [quickMagFilter, setQuickMagFilter] = useState<MagPotential | "">("");
  // listKey increments to force WreckList remount when quick filters change
  const [listKey, setListKey] = useState(0);

  // Check backend connectivity on mount
  useEffect(() => {
    getApiBase()
      .then((base) => {
        setApiBase(base);
        return fetch(`${base}/health`, { signal: AbortSignal.timeout(2000) });
      })
      .then(() => setOnline(true))
      .catch(() => setOnline(false));
  }, []);

  const handleSelect = (w: Wreck) => {
    setSelectedWreck(w);
    setPanel("detail");
  };

  const handleRetryConnection = () => {
    resetConnectionState();
    getApiBase()
      .then((base) => {
        setApiBase(base);
        return fetch(`${base}/health`, { signal: AbortSignal.timeout(2000) });
      })
      .then(() => setOnline(true))
      .catch(() => setOnline(false));
  };

  /** Navigate to wreck list with preset filters. */
  const goListWithFilter = (steelOnly: boolean, magFilter: MagPotential | "") => {
    setQuickSteelOnly(steelOnly);
    setQuickMagFilter(magFilter);
    setListKey(k => k + 1); // remount WreckList so it picks up new initial values
    setPanel("list");
  };

  const handleSetPanel = (panel: ActivePanel) => {
    setPanel(panel);
  };

  return (
    <div className="app-shell">
      <UpdateChecker />
      <AutoBagPrompt />
      {/* ── Top bar ────────────────────────────────────── */}
      <div className="topbar">
        <div className="topbar-brand">
          <span className="brand-anchor">⚓</span>
          <h1>WRECKHUNTER <span className="brand-year">2000</span></h1>
        </div>
        <span
          className={`conn-badge ${online === true ? "online" : "offline"}`}
          onClick={handleRetryConnection}
          style={{ cursor: "pointer" }}
          title="Click to retry connection"
        >
          {online === null ? "…" : online ? "API Online" : "Mock Data"}
        </span>
        <span className="topbar-api-url" title="Resolved backend API base URL">
          {apiBase}
        </span>
        <input
          className="search-box"
          type="text"
          placeholder="Search wrecks by name…"
          value={searchQuery}
          onChange={e => {
            setSearchQuery(e.target.value);
            if (panel !== "list" && e.target.value.length >= 2) {
              setQuickSteelOnly(false);
              setQuickMagFilter("");
              setPanel("list");
            }
          }}
        />
      </div>

      {/* ── Sidebar ────────────────────────────────────── */}
      <div className="sidebar">
        <div className="section-label">Intelligence</div>
        <button className={panel === "stats" ? "active" : ""} onClick={() => setPanel("stats")}>
          📊 Dashboard
        </button>
        <button className={panel === "list" ? "active" : ""} onClick={() => setPanel("list")}>
          📋 Wreck Registry
        </button>
        <button className={panel === "map" ? "active" : ""} onClick={() => setPanel("map")}>
          🗺 Map
        </button>

        {selectedWreck && (
          <button className={panel === "detail" ? "active" : ""} onClick={() => setPanel("detail")}>
            🔍 {selectedWreck.name || "Detail"}
          </button>
        )}

        <div className="section-label">Quick Filters</div>
        <button onClick={() => goListWithFilter(false, "")}>
          All Wrecks
        </button>
        <button onClick={() => goListWithFilter(true, "")}>
          🛳 Steel Freighters
        </button>
        <button onClick={() => goListWithFilter(false, "strong")}>
          🧲 Strong Magnetic
        </button>
        <button onClick={() => goListWithFilter(false, "moderate")}>
          〰 Moderate Magnetic
        </button>

        <div className="section-label">Agent</div>
        <button className={panel === "agent" ? "active" : ""} onClick={() => setPanel("agent")}>
          🤖 AI Director
        </button>

        <div className="section-label">Pipelines</div>
        <button className={panel === "scan" ? "active" : ""} onClick={() => setPanel("scan")}>
          📡 Scanner & Restoration
        </button>
        <button className={panel === "mag" ? "active" : ""} onClick={() => setPanel("mag")}>
          🧲 Mag Pipeline
        </button>
        <button className={panel === "erie" ? "active" : ""} onClick={() => setPanel("erie")}>
          🌊 Lake Erie Scanner
        </button>
        <button className={panel === "pdf" ? "active" : ""} onClick={() => setPanel("pdf")}>
          📄 PDF Redactor
        </button>

        <div className="section-label">Analysis</div>
        <button className={panel === "loran" ? "active" : ""} onClick={() => setPanel("loran")}>
          🌍 Loran-C Warp
        </button>
        <button className={panel === "sensors" ? "active" : ""} onClick={() => setPanel("sensors")}>
          🛰 Extended Sensors
        </button>
        <button className={panel === "harvest" ? "active" : ""} onClick={() => handleSetPanel("harvest")}>
          🪝 Raw Harvester
        </button>

        <div className="section-label">Export</div>
        <button className={panel === "export" ? "active" : ""} onClick={() => setPanel("export")}>
          📤 KML / KMZ Export
        </button>
      </div>

      {/* ── Main content ───────────────────────────────── */}
      <div className="main-content">
        <div style={{ display: panel === "stats" ? "block" : "none", height: '100%' }}>
          <StatsPanel />
        </div>
        <div style={{ display: panel === "list" ? "block" : "none", height: '100%' }}>
          <WreckList
            key={`list-${listKey}`}
            searchQuery={searchQuery}
            onSelect={handleSelect}
            initialSteelOnly={quickSteelOnly}
            initialMagFilter={quickMagFilter}
          />
        </div>
        <div style={{ display: panel === "map" ? "block" : "none", height: '100%' }}>
          <MapPanel onSelect={handleSelect} />
        </div>

        <div style={{ display: panel === "scan" ? "block" : "none", height: '100%' }}>
          <ScanPanel />
        </div>
        <div style={{ display: panel === "mag" ? "block" : "none", height: '100%' }}>
          <MagPipelinePanel />
        </div>
        <div style={{ display: panel === "erie" ? "block" : "none", height: '100%' }}>
          <EriePanel />
        </div>
        <div style={{ display: panel === "restore" ? "block" : "none", height: '100%' }}>
          <RestorationPanel />
        </div>
        <div style={{ display: panel === "pdf" ? "block" : "none", height: '100%' }}>
          <PDFBreakerPanel />
        </div>
        <div style={{ display: panel === "export" ? "block" : "none", height: '100%' }}>
          <ExportPanel />
        </div>
        <div style={{ display: panel === "loran" ? "block" : "none", height: '100%' }}>
          <LoranPanel />
        </div>
        <div style={{ display: panel === "sensors" ? "block" : "none", height: '100%' }}>
          <ExtendedSensorsPanel />
        </div>
        <div style={{ display: panel === "harvest" ? "block" : "none", height: '100%' }}>
          <HarvesterPanel />
        </div>
        <div style={{ display: panel === "agent" ? "block" : "none", height: '100%' }}>
          <AgentPanel />
        </div>
        <div style={{ display: panel === "detail" ? "block" : "none", height: '100%' }}>
          {selectedWreck && <WreckDetail wreck={selectedWreck} onBack={() => setPanel("list")} />}
        </div>
      </div>
    </div>
  );
}


