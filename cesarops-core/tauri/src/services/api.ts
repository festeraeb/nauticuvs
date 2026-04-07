// API adapter — talks to the FastAPI wrecks_api backend started by Tauri Rust.
import type {
  Wreck,
  WreckListResponse,
  SearchResponse,
  BboxResponse,
  StatsResponse,
  ScanJob,
  ScanStatus,
  ScanRequest,
  ScanResultsResponse,
  PdfBreakerRequest,
  ToolJob,
  ToolJobStatus,
  MagPipelineRequest,
  RestorationRequest,
  AzureVisionStatus,
  VisionAnalyzeRequest,
} from "../types";

let _apiBasePromise: Promise<string> | null = null;

async function resolveApiBase(): Promise<string> {
  const envBase = (import.meta as { env?: { VITE_API_BASE?: string } }).env?.VITE_API_BASE;
  if (envBase) return envBase;

  // Are we running inside a true Tauri window context?
  const isTauri = typeof window !== "undefined" && (window as any).__TAURI_INTERIPC__ !== undefined;

  if (!isTauri) {
    console.warn("Running in standard browser mode (Vite/Chrome). Relying on manually started uvicorn at 127.0.0.1:8099.");
    return "http://127.0.0.1:8099";
  }

  try {
    const core = await import("@tauri-apps/api/core");
    // This calls Rust's ensure_backend() which dynamically assigns the port
    // and returns the exact URL without any guesswork or scanning.
    const dynamicUrl = await core.invoke<string>("ensure_backend");
    console.log(`Backend securely bound by Tauri dynamically at: ${dynamicUrl}`);
    return dynamicUrl;
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new Error(`Rust Backend failed to start: ${msg}`);
  }
}

export async function getApiBase(): Promise<string> {
  if (!_apiBasePromise) _apiBasePromise = resolveApiBase();
  return _apiBasePromise;
}

async function fetchWithApiBase(path: string, init?: RequestInit): Promise<{ res: Response; apiBase: string }> {
  const apiBase = await getApiBase();
  try {
    const res = await fetch(`${apiBase}${path}`, init);
    return { res, apiBase };
  } catch {
    throw new Error(`Cannot reach API at ${apiBase}. ${backendHint(apiBase)}`);
  }
}

function backendHint(apiBase: string): string {
  return `Backend expected at ${apiBase}.`;
}

async function apiFetch<T>(path: string): Promise<T> {
  const { res } = await fetchWithApiBase(path);
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return res.json();
}

/** Reset the cached connection state (e.g. on retry button click). */
export function resetConnectionState() {
  _apiBasePromise = null;
}

// ── Public API ───────────────────────────────────────────────────────────────

export async function getStats(): Promise<StatsResponse> {
  return apiFetch("/stats");
}

export async function listWrecks(
  page = 1, limit = 50,
  filters?: { name?: string; is_steel?: boolean; magnetic_potential?: string; has_coords?: boolean },
): Promise<WreckListResponse> {
  const params = new URLSearchParams({ page: String(page), limit: String(limit) });
  if (filters?.name) params.set("name", filters.name);
  if (filters?.is_steel !== undefined) params.set("is_steel", String(filters.is_steel));
  if (filters?.magnetic_potential) params.set("magnetic_potential", filters.magnetic_potential);
  if (filters?.has_coords !== undefined) params.set("has_coords", String(filters.has_coords));
  return apiFetch(`/wrecks?${params}`);
}

export async function getWreck(id: number): Promise<Wreck> {
  return apiFetch(`/wrecks/${id}`);
}

export async function searchWrecks(q: string, limit = 50): Promise<SearchResponse> {
  return apiFetch(`/wrecks/search/query?q=${encodeURIComponent(q)}&limit=${limit}`);
}

export async function bboxQuery(
  minLat: number, maxLat: number, minLon: number, maxLon: number, limit = 500,
): Promise<BboxResponse> {
  return apiFetch(
    `/wrecks/bbox/query?min_lat=${minLat}&max_lat=${maxLat}&min_lon=${minLon}&max_lon=${maxLon}&limit=${limit}`
  );
}

export async function steelFreighters(page = 1, limit = 100): Promise<WreckListResponse> {
  return apiFetch(`/wrecks/steel-freighters/list?page=${page}&limit=${limit}`);
}

export async function magneticWrecks(
  onlyPositive = false, page = 1, limit = 100,
): Promise<WreckListResponse> {
  return apiFetch(
    `/wrecks/magnetic/list?only_positive=${onlyPositive}&page=${page}&limit=${limit}`
  );
}

// ── Scan API ─────────────────────────────────────────────────────────────────

export async function startScan(req: ScanRequest): Promise<ScanJob> {
  const { res, apiBase } = await fetchWithApiBase("/scan/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(`Scan start failed: ${res.status}`);
  // Keep latest known-good backend URL in memory after a successful scan start.
  _apiBasePromise = Promise.resolve(apiBase);
  return res.json();
}

export async function getScanStatus(jobId: string): Promise<ScanStatus> {
  return apiFetch(`/scan/status/${encodeURIComponent(jobId)}`);
}

export async function getScanResults(jobId: string): Promise<ScanResultsResponse> {
  return apiFetch(`/scan/results/${encodeURIComponent(jobId)}`);
}

export async function scanToRestore(jobId: string, candidateIndex: number = 0): Promise<ToolJob & { bag_path: string; candidate: Record<string, unknown> }> {
  const { res } = await fetchWithApiBase(`/scan/results/${encodeURIComponent(jobId)}/restore?candidate_index=${candidateIndex}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Restore from scan failed: ${res.status}`);
  return res.json();
}

// ── Standalone tool APIs ─────────────────────────────────────────────────────

export async function startMagPipeline(req: MagPipelineRequest): Promise<ToolJob> {
  const { res } = await fetchWithApiBase("/tools/mag-pipeline/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(`Mag pipeline start failed: ${res.status}`);
  return res.json();
}

export async function getMagPipelineStatus(jobId: string): Promise<ToolJobStatus> {
  return apiFetch(`/tools/mag-pipeline/status/${encodeURIComponent(jobId)}`);
}

// ── PDF Breaker APIs ─────────────────────────────────────────────────────────

export async function startPdfBreaker(req: PdfBreakerRequest): Promise<ToolJob> {
  const { res } = await fetchWithApiBase("/tools/pdf-breaker/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(`PDF breaker start failed: ${res.status}`);
  return res.json();
}

export async function getPdfBreakerStatus(jobId: string): Promise<ToolJobStatus> {
  return apiFetch(`/tools/pdf-breaker/status/${encodeURIComponent(jobId)}`);
}

export async function getMagDetections(outputDir: string): Promise<{
  detections: Array<Record<string, unknown>>;
  total: number;
  labeled: number;
}> {
  return apiFetch(`/tools/mag-pipeline/detections?output_dir=${encodeURIComponent(outputDir)}`);
}

export async function labelMagDetections(
  outputDir: string,
  labels: Array<{ patch_file: string; label: number; notes?: string }>,
): Promise<{ saved: number; total_labeled: number }> {
  const { res } = await fetchWithApiBase("/tools/mag-pipeline/label", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ output_dir: outputDir, labels }),
  });
  if (!res.ok) throw new Error(`Label save failed: ${res.status}`);
  return res.json();
}

// ── BAG Depth Restoration APIs ───────────────────────────────────────────────

export async function startRestoration(req: RestorationRequest): Promise<ToolJob> {
  const { res } = await fetchWithApiBase("/tools/restoration/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(`Restoration start failed: ${res.status}`);
  return res.json();
}

export async function getRestorationStatus(jobId: string): Promise<ToolJobStatus> {
  return apiFetch(`/tools/restoration/status/${encodeURIComponent(jobId)}`);
}

// ── Azure AI Vision APIs ─────────────────────────────────────────────────────

export async function getAzureVisionStatus(): Promise<AzureVisionStatus> {
  return apiFetch("/tools/azure-vision/status");
}

export async function configureAzureVision(key: string, endpoint?: string): Promise<AzureVisionStatus> {
  const { res } = await fetchWithApiBase("/tools/azure-vision/configure", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      key,
      endpoint: endpoint || "https://wreckhunter2000.cognitiveservices.azure.com/",
      region: "eastus",
    }),
  });
  if (!res.ok) throw new Error(`Failed to save key: ${res.status}`);
  return res.json();
}

export async function startVisionAnalysis(req: VisionAnalyzeRequest): Promise<ToolJob> {
  const { res } = await fetchWithApiBase("/tools/azure-vision/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(`Vision analysis start failed: ${res.status}`);
  return res.json();
}

export async function getVisionAnalysisStatus(jobId: string): Promise<ToolJobStatus> {
  return apiFetch(`/tools/azure-vision/status/${encodeURIComponent(jobId)}`);
}

// ── Rust KMZ Generator (Tauri command) ───────────────────────────────────────

export async function generateKmz(
  scanResultsPath: string,
  wrecksDbPath: string,
  outputPath: string,
  searchRadiusM?: number,
): Promise<string> {
  const mod = await import("@tauri-apps/api/core");
  return mod.invoke("gen_kmz", {
    scanResultsPath,
    wrecksDbPath,
    outputPath,
    searchRadiusM: searchRadiusM ?? 1000.0,
  });
}

export async function generateKml(
  scanResultsPath: string,
  wrecksDbPath: string,
  outputPath: string,
  searchRadiusM?: number,
): Promise<string> {
  const mod = await import("@tauri-apps/api/core");
  return mod.invoke("gen_kml", {
    scanResultsPath,
    wrecksDbPath,
    outputPath,
    searchRadiusM: searchRadiusM ?? 1000.0,
  });
}

// ── Datum Correction / Loran-C Warp ──────────────────────────────────────────

export interface DatumCorrectionResult {
  raw: { lat: number; lon: number };
  molodensky: { lat: number; lon: number; delta_lat_m: number; delta_lon_m: number };
  corrected: { lat: number; lon: number };
  total_shift_m: number;
  rubber_sheet: Record<string, unknown>;
}

export async function datumCorrectSingle(
  lat: number, lon: number, datum = "nad27",
): Promise<DatumCorrectionResult> {
  const { res } = await fetchWithApiBase("/tools/datum/correct", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lat, lon, datum }),
  });
  if (!res.ok) throw new Error(`Datum correction failed: ${res.status}`);
  return res.json();
}

export async function datumCorrectBatch(
  candidates: Array<{ center_lat: number; center_lon: number; [key: string]: unknown }>,
  datum = "nad27",
): Promise<{ total: number; results: unknown[] }> {
  const { res } = await fetchWithApiBase("/tools/datum/batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ candidates, datum }),
  });
  if (!res.ok) throw new Error(`Batch datum correction failed: ${res.status}`);
  return res.json();
}

export async function datumListAnchors(): Promise<{
  total: number;
  ready: number;
  anchors: Array<Record<string, unknown>>;
}> {
  return apiFetch("/tools/datum/anchors");
}

// ── Extended Sensors (erie_remote) ───────────────────────────────────────────

export interface SensorRunRequest {
  lat: number;
  lon: number;
  amplitude_nt?: number;
  depth_m?: number;
  label?: string;
  dry_run?: boolean;
  data_dir?: string;
  earthdata_token?: string;
}

export async function startSensorRun(req: SensorRunRequest): Promise<ToolJob> {
  const { res } = await fetchWithApiBase("/tools/sensors/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(`Sensor run start failed: ${res.status}`);
  return res.json();
}

export async function getSensorRunStatus(jobId: string): Promise<ToolJobStatus> {
  return apiFetch(`/tools/sensors/status/${encodeURIComponent(jobId)}`);
}

export async function listSensorReports(dataDir = "erie_remote_data"): Promise<{
  reports: Array<{
    file: string;
    candidate: Record<string, unknown>;
    sensors_total: number;
    sensors_flagged: number;
    generated: string;
  }>;
  total: number;
}> {
  return apiFetch(`/tools/sensors/reports?data_dir=${encodeURIComponent(dataDir)}`);
}

export async function startAutoBagPipeline(mode: string, customKbps?: number, scanMode: string = "masked"): Promise<{ job_id: string }> {
  const base = await getApiBase();
  const res = await fetch(`${base}/tools/auto-bag/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ throttle_mode: mode, custom_kbps: customKbps || null, scan_mode: scanMode })
  });
  if (!res.ok) throw new Error('Failed to start NOA BAG autoscanner');
  return res.json();
}

// ── Lake Erie Focused Scanner APIs ───────────────────────────────────────────

import type { ErieScanRequest, ErieScanResult, ErieWellhead, ErieKnownWreck } from "../types";

export async function startErieScan(req: ErieScanRequest): Promise<ToolJob> {
  const { res } = await fetchWithApiBase("/tools/erie-scanner/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(`Erie scanner start failed: ${res.status}`);
  return res.json();
}

export async function getErieScanStatus(jobId: string): Promise<ToolJobStatus> {
  return apiFetch(`/tools/erie-scanner/status/${encodeURIComponent(jobId)}`);
}

export async function getErieScanResults(outputDir = "erie_scanner_output"): Promise<ErieScanResult> {
  return apiFetch(`/tools/erie-scanner/results?output_dir=${encodeURIComponent(outputDir)}`);
}

export async function getErieWellheads(wellsCsv?: string): Promise<{
  wells: ErieWellhead[];
  total: number;
}> {
  const params = wellsCsv ? `?wells_csv=${encodeURIComponent(wellsCsv)}` : "";
  return apiFetch(`/tools/erie-scanner/wellheads${params}`);
}

export async function getErieKnownWrecks(): Promise<{
  wrecks: ErieKnownWreck[];
  total: number;
}> {
  return apiFetch("/tools/erie-scanner/known-wrecks");
}

// ── Lake Erie XGBoost Training APIs ──────────────────────────────────────────

export interface ErieTrainRequest {
  candidates_csv?: string;
  wells_csv?: string;
  output_dir?: string;
  n_synth_wreck?: number;
  n_synth_wellhead?: number;
  n_synth_geological?: number;
}

export async function startErieTraining(req: ErieTrainRequest): Promise<ToolJob> {
  const { res } = await fetchWithApiBase("/tools/erie-scanner/train", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(`Erie training start failed: ${res.status}`);
  return res.json();
}

export async function getErieTrainingStatus(jobId: string): Promise<ToolJobStatus> {
  return apiFetch(`/tools/erie-scanner/training-status/${encodeURIComponent(jobId)}`);
}

export async function getErieTrainingReport(outputDir = "models/erie"): Promise<Record<string, unknown>> {
  return apiFetch(`/tools/erie-scanner/training-report?output_dir=${encodeURIComponent(outputDir)}`);
}

export async function getErieFeedbackReport(
  modelDir = "models/erie", feedbackDir = "models/erie/feedback",
): Promise<Record<string, unknown>> {
  return apiFetch(
    `/tools/erie-scanner/feedback-report?model_dir=${encodeURIComponent(modelDir)}&feedback_dir=${encodeURIComponent(feedbackDir)}`
  );
}

// ── Raw Harvester APIs ────────────────────────────────────────────────────────

import type {
  WarpFieldRequest,
  WarpFieldInfo,
  HarvesterRequest,
  HarvesterJobStatus,
  HarvesterResult,
} from "../types";

/** Export the LORAN-C IDW warp field to loran_warp_field.json. */
export async function startWarpFieldExport(req: WarpFieldRequest = {}): Promise<ToolJob> {
  const { res } = await fetchWithApiBase("/tools/warp-field/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(`Warp export start failed: ${res.status}`);
  return res.json();
}

/** Poll warp-field export job status. */
export async function getWarpFieldExportStatus(jobId: string): Promise<ToolJobStatus> {
  return apiFetch(`/tools/warp-field/status/${encodeURIComponent(jobId)}`);
}

/** Get info about the currently built warp field (does not start a job). */
export async function getWarpFieldInfo(): Promise<WarpFieldInfo> {
  return apiFetch("/tools/warp-field/info");
}

/** Start the raw magnetometer harvester. Returns a job to poll. */
export async function startHarvester(req: HarvesterRequest = {}): Promise<ToolJob> {
  const { res } = await fetchWithApiBase("/tools/harvester/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(`Harvester start failed: ${res.status}`);
  return res.json();
}

/** Poll harvester job status — includes per-source pipeline and progress_pct. */
export async function getHarvesterStatus(jobId: string): Promise<HarvesterJobStatus> {
  return apiFetch(`/tools/harvester/status/${encodeURIComponent(jobId)}`);
}

/** Get full results once job is completed. */
export async function getHarvesterResults(jobId: string): Promise<{ status: string; result?: HarvesterResult }> {
  return apiFetch(`/tools/harvester/results/${encodeURIComponent(jobId)}`);
}

/** Return most recent harvest catalog for a lake (no job needed). */
export async function getHarvesterCatalog(lake = "erie"): Promise<Record<string, unknown>> {
  return apiFetch(`/tools/harvester/catalog?lake=${encodeURIComponent(lake)}`);
}

export async function fetchNasaHls(sceneId: string, bands: string[], bbox: [number, number, number, number]): Promise<{ [band: string]: string }> {
  const { res } = await fetchWithApiBase("/nasa-hls/fetch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sceneId, bands, bbox }),
  });
  if (!res.ok) throw new Error(`NASA HLS fetch failed: ${res.status}`);
  return res.json();
}
