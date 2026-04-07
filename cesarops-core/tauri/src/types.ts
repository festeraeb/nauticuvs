// Great Lakes Wreck Recovery — TypeScript types

export interface Wreck {
  id: number;
  name: string;
  date: string | null;
  latitude: number | null;
  longitude: number | null;
  depth: number | null;
  feature_type: string | null;
  source: string | null;
  magnetic_potential: string | null;
  is_steel_freighter: number | null;
  is_iron_ore_carrier: number | null;
  hull_material: string | null;
  size_category: string | null;
  salvage_status: string | null;
  mag_mean: number | null;
  mag_label: number | null;
  training_confidence: number | null;
  coord_quality: string | null;
  // Detail fields (single wreck)
  historical_place_names?: string | null;

  mag_std?: number | null;
  mag_max?: number | null;
  mag_min?: number | null;
  mag_median?: number | null;
  mag_as_peak?: number | null;
  mag_vd_peak?: number | null;
  mag_tmi_peak?: number | null;
  mag_spike_w_m?: number | null;
  mag_polarity?: string | null;
}

export interface WreckListResponse {
  total: number;
  page: number;
  limit: number;
  pages: number;
  results: Wreck[];
}

export interface SearchResponse {
  query: string;
  count: number;
  results: Wreck[];
}

export interface BboxResponse {
  bbox: number[];
  count: number;
  results: Wreck[];
}

export interface StatsResponse {
  total_wrecks: number;
  with_coordinates: number;
  with_hull_material: number;
  with_namag_features: number;
  steel_freighters: number;
  iron_ore_carriers: number;
  strong_mag_potential: number;
  moderate_mag_potential: number;
}

export interface ScanJob {
  job_id: string;
  status: string;
}

export interface ScanStatus {
  id: string;
  status: string;
  created: number;
  start_time?: number;
  end_time?: number;
  error?: string;
  pipeline?: {
    mag_pipeline?: { status?: string; [key: string]: unknown };
    [key: string]: unknown;
  };
}

export interface ScanRequest {
  paths: string[];
  output_dir?: string;
  config?: Record<string, unknown>;
  swayze_match?: boolean;
  swayze_radius_m?: number;
}

export interface SwayzeMatch {
  candidate_index: number;
  wreck_id: number;
  name: string;
  date: string | null;
  latitude: number;
  longitude: number;
  depth: number | null;
  feature_type: string | null;
  hull_material: string | null;
  vessel_class: string | null;
  magnetic_weight: number | null;
  length_ft: number | null;
  distance_m: number;
  match_score: number;
}

export interface ScanCandidate {
  source_file: string;
  latitude: number;
  longitude: number;
  confidence: number;
  size_sq_meters: number;
  size_sq_feet: number;
  width_meters: number;
  height_meters: number;
  width_feet: number;
  height_feet: number;
  anomaly_score: number;
  method: string;
  swayze_matches?: SwayzeMatch[];
}

export interface ScanResultsResponse {
  status: string;
  result?: {
    total_files: number;
    successful_scans: number;
    total_candidates: number;
    total_signatures: number;
    results: Array<{
      file: string;
      candidates: ScanCandidate[];
      redaction_signatures: Array<Record<string, unknown>>;
      success: boolean;
      outputs?: { kml?: string; kmz?: string };
    }>;
    export_files?: { json?: string; csv?: string; kml?: string; kmz?: string };
    swayze_matches?: SwayzeMatch[];
  };
  export_files?: { json?: string; csv?: string; kml?: string; kmz?: string };
  swayze_matches?: SwayzeMatch[];
  total_candidates?: number;
  total_signatures?: number;
}

export interface PdfBreakerRequest {
  paths: string[];
  output_dir?: string;
  config?: Record<string, unknown>;
}

export type MagPotential = "strong" | "moderate" | "weak" | "unknown";
export type ActivePanel = "list" | "detail" | "stats" | "scan" | "mag" | "erie" | "restore" | "pdf" | "map" | "export" | "loran" | "sensors";

// ── Lake Erie Scanner types ──────────────────────────────────────────────────

export interface ErieCandidate {
  label_id: number;
  center_lat: number;
  center_lon: number;
  composite_score: number;
  dipole_score: number;
  bonus_score: number;
  tier: string;
  dipole_verdict: string;
  amplitude_peak_abs: number;
  width_m: number;
  height_m: number;
  all_reasons: string[];
  ground_truth?: "wreck" | "wellhead" | "geological" | "unknown";
  ground_truth_name?: string;
  wellhead_distance_m?: number | null;
  nearest_wellhead?: string | null;
  nearest_known_wreck?: string | null;
  wreck_distance_m?: number | null;
  loran_corrected_lat?: number;
  loran_corrected_lon?: number;
}

export interface ErieWellhead {
  well_id: string;
  name: string;
  lat: number;
  lon: number;
  status: string;
  well_type: string;
  township: string;
}

export interface ErieKnownWreck {
  name: string;
  lat: number;
  lon: number;
  vessel_type?: string;
  length_ft?: number;
  depth_ft?: number;
  source: string;
}

export interface ErieScanRequest {
  candidates_csv?: string;
  wells_csv?: string;
  output_dir?: string;
  wellhead_radius_m?: number;
  satellite_sources?: string[];
  apply_loran_correction?: boolean;
  retrain?: boolean;
}

export interface ErieScanResult {
  candidates: ErieCandidate[];
  wellheads_loaded: number;
  known_wrecks_loaded: number;
  candidates_filtered: number;
  wellhead_matches: number;
  wreck_matches: number;
  model_accuracy?: number;
}

// ── Standalone tool types ────────────────────────────────────────────────────

export interface ToolJob {
  job_id: string;
  status: string;
}

export interface ToolJobStatus {
  id: string;
  tool: string;
  status: string;
  created: number;
  start_time?: number;
  end_time?: number;
  error?: string;
  result?: Record<string, unknown>;
}

export interface MagPipelineRequest {
  output_dir?: string;
  sources?: string[];
  bbox?: [number, number, number, number];
  stages?: string;
  threshold?: number;
  mode?: 'full' | 'validate';
  config?: Record<string, unknown>;
}

export interface RestorationRequest {
  bag_path: string;
  output_dir?: string;
  min_row?: number;
  max_row?: number;
  min_col?: number;
  max_col?: number;
  amplification?: number;
  sigma?: number;
  techniques?: string[];
}

export interface AzureVisionStatus {
  configured: boolean;
  endpoint: string | null;
  sdk_installed: boolean;
  rate_limit_per_minute?: number;
  rate_limit_per_month?: number;
  calls_this_session?: number;
  calls_last_minute?: number;
}

export interface VisionAnalyzeRequest {
  output_dir: string;
  bag_stem: string;
}

// ── Raw Harvester types ──────────────────────────────────────────────────────

export interface WarpFieldRequest {
  lake?: string;
  spacing_km?: number;
}

export interface WarpFieldInfo {
  exists: boolean;
  generated?: string;
  lake?: string;
  anchor_count?: number;
  grid_spacing_km?: number;
  bbox?: number[];
  size_kb?: number;
  anchors_used?: Array<{ id: string; name: string; type: string; gps: number[]; shift_m: { north: number; east: number } }>;
  message?: string;
  error?: string;
}

export interface HarvesterRequest {
  lake?: string;
  sources?: string[];   // 'ngdc' | 'sciencebase' | 'nrcan' | 'swarm'
  apply_warp?: boolean;
  max_surveys?: number;
  dry_run?: boolean;
  swarm_token?: string;
}

export interface HarvesterJobStatus {
  id: string;
  tool: string;
  status: string;      // 'queued' | 'running' | 'completed' | 'failed'
  lake?: string;
  sources?: string[];
  created: number;
  start_time?: number;
  end_time?: number;
  error?: string;
  pipeline?: Record<string, string>;   // source => 'pending'|'running'|'done'
  progress_pct?: number;
  progress_msg?: string;
}

export interface HarvesterSurveyResult {
  survey_id: string;
  source: string;
  status: string;
  pings: number;
  file: string;
  error: string;
}

export interface HarvesterResult {
  lake: string;
  sources_tried: string[];
  surveys: HarvesterSurveyResult[];
  total_pings: number;
  output_csv: string;
  warp_applied: boolean;
  errors: string[];
}

// Update ActivePanel to include harvester panel
export type ActivePanel =
  | "list" | "detail" | "stats" | "scan" | "mag" | "erie"
  | "restore" | "pdf" | "map" | "export" | "loran" | "sensors"
  | "harvest";
