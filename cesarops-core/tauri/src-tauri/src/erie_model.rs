//! Lake Erie Off-Axis Detector — Rust ONNX Inference Stub
//!
//! Loads trained ONNX models exported from the Python XGBoost training pipeline
//! and runs inference on magnetic anomaly feature vectors.
//!
//! ## Stage 2 Integration
//!
//! This module will be activated when the Python prototype is validated.
//! To enable:
//! 1. Add `ort = "2"` to `[dependencies]` in Cargo.toml
//! 2. Uncomment the `ort` usage lines below
//! 3. Place ONNX model files in a known location (e.g., `models/erie/`)
//!
//! ## Feature Vector (17 features, matching Python FEATURE_NAMES)
//!
//! | Index | Feature                            | Unit  |
//! |-------|------------------------------------|-------|
//! |   0   | amplitude_peak_abs                 | nT    |
//! |   1   | amplitude_mean_abs                 | nT    |
//! |   2   | gradient_contrast                  | ratio |
//! |   3   | dipole_separation_m                | m     |
//! |   4   | lobe_symmetry_ratio                | ratio |
//! |   5   | axis_offset_deg                    | deg   |
//! |   6   | aspect_ratio                       | ratio |
//! |   7   | flip_distance_km                   | km    |
//! |   8   | pixel_count                        | count |
//! |   9   | distance_to_nearest_flight_line_m  | m     |
//! |  10   | basin_western                      | 0/1   |
//! |  11   | basin_central                      | 0/1   |
//! |  12   | basin_eastern                      | 0/1   |
//! |  13   | local_snr_vs_basin_median           | ratio |
//! |  14   | curvelet_edge_score                | ratio |
//! |  15   | width_m                            | m     |
//! |  16   | height_m                           | m     |

#![allow(dead_code)]

/// Number of features in the model input vector.
pub const FEATURE_COUNT: usize = 17;

/// Feature names matching the Python training pipeline (for documentation).
pub const FEATURE_NAMES: [&str; FEATURE_COUNT] = [
    "amplitude_peak_abs",
    "amplitude_mean_abs",
    "gradient_contrast",
    "dipole_separation_m",
    "lobe_symmetry_ratio",
    "axis_offset_deg",
    "aspect_ratio",
    "flip_distance_km",
    "pixel_count",
    "distance_to_nearest_flight_line_m",
    "basin_western",
    "basin_central",
    "basin_eastern",
    "local_snr_vs_basin_median",
    "curvelet_edge_score",
    "width_m",
    "height_m",
];

/// Basin classification by longitude.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ErieBasin {
    Western,  // west of -82.0°
    Central,  // -82.0° to -80.0°
    Eastern,  // east of -80.0°
}

impl ErieBasin {
    pub fn from_longitude(lon: f64) -> Self {
        if lon < -82.0 {
            ErieBasin::Western
        } else if lon < -80.0 {
            ErieBasin::Central
        } else {
            ErieBasin::Eastern
        }
    }

    pub fn name(&self) -> &'static str {
        match self {
            ErieBasin::Western => "western",
            ErieBasin::Central => "central",
            ErieBasin::Eastern => "eastern",
        }
    }

    /// One-hot encoding for feature vector indices 10, 11, 12.
    pub fn one_hot(&self) -> [f64; 3] {
        match self {
            ErieBasin::Western => [1.0, 0.0, 0.0],
            ErieBasin::Central => [0.0, 1.0, 0.0],
            ErieBasin::Eastern => [0.0, 0.0, 1.0],
        }
    }
}

/// Prediction result from the Erie off-axis detector.
#[derive(Debug, Clone)]
pub struct EriePredict {
    /// Probability that the anomaly is a wreck (0.0–1.0).
    pub wreck_prob: f64,
    /// Estimated distance from nearest flight line (metres).
    pub off_axis_distance_m: f64,
    /// Which basin the target is in.
    pub basin: ErieBasin,
    /// Which model produced this prediction.
    pub model_used: String,
}

/// Lake Erie Off-Axis Detector model.
///
/// Loads an ONNX model exported from the Python XGBoost training pipeline
/// and runs inference on 17-feature input vectors.
pub struct ErieModel {
    // Uncomment when `ort` crate is added to Cargo.toml:
    // session: ort::Session,
    model_name: String,
    #[allow(dead_code)]
    model_path: String,
}

impl ErieModel {
    /// Load an ONNX model from disk.
    ///
    /// # Example (when ort crate is available)
    /// ```no_run
    /// let model = ErieModel::load("models/erie/erie_erie_wide.onnx")?;
    /// ```
    pub fn load(onnx_path: &str) -> Result<Self, String> {
        // ── Stage 2: Uncomment when ort crate is added ──
        // let session = ort::Session::builder()
        //     .map_err(|e| format!("ONNX session builder error: {e}"))?
        //     .with_optimization_level(ort::GraphOptimizationLevel::Level3)
        //     .map_err(|e| format!("Optimization config error: {e}"))?
        //     .commit_from_file(onnx_path)
        //     .map_err(|e| format!("Failed to load ONNX model '{onnx_path}': {e}"))?;

        let model_name = std::path::Path::new(onnx_path)
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("unknown")
            .to_string();

        Ok(Self {
            // session,
            model_name,
            model_path: onnx_path.to_string(),
        })
    }

    /// Run inference on a single feature vector.
    ///
    /// Returns the wreck probability and off-axis distance estimate.
    pub fn predict(&self, features: &[f64; FEATURE_COUNT]) -> Result<EriePredict, String> {
        // ── Stage 2: Uncomment when ort crate is added ──
        // use ndarray::Array2;
        //
        // let input = Array2::from_shape_vec(
        //     (1, FEATURE_COUNT),
        //     features.iter().map(|&x| x as f32).collect(),
        // ).map_err(|e| format!("Input shape error: {e}"))?;
        //
        // let outputs = self.session
        //     .run(ort::inputs!["features" => input.view()])
        //     .map_err(|e| format!("Inference error: {e}"))?;
        //
        // // XGBoost ONNX exports probabilities as second output
        // let probs = outputs[1]
        //     .try_extract_tensor::<f32>()
        //     .map_err(|e| format!("Output extraction error: {e}"))?;
        //
        // let wreck_prob = probs[[0, 1]] as f64;

        // Stub: return 0.0 until ONNX runtime is integrated
        let wreck_prob = 0.0_f64;

        // Determine basin from one-hot features
        let basin = if features[10] > 0.5 {
            ErieBasin::Western
        } else if features[11] > 0.5 {
            ErieBasin::Central
        } else {
            ErieBasin::Eastern
        };

        Ok(EriePredict {
            wreck_prob,
            off_axis_distance_m: features[9], // distance_to_nearest_flight_line_m
            basin,
            model_used: self.model_name.clone(),
        })
    }

    /// Run inference on the appropriate basin-specific model.
    ///
    /// Loads the basin model matching the target's longitude, falling back
    /// to the Erie-wide model if the basin model is unavailable.
    pub fn predict_with_basin_model(
        features: &[f64; FEATURE_COUNT],
        models_dir: &str,
    ) -> Result<EriePredict, String> {
        let basin = if features[10] > 0.5 {
            ErieBasin::Western
        } else if features[11] > 0.5 {
            ErieBasin::Central
        } else {
            ErieBasin::Eastern
        };

        // Try basin-specific model first
        let basin_path = format!("{}/erie_{}.onnx", models_dir, basin.name());
        if std::path::Path::new(&basin_path).exists() {
            let model = ErieModel::load(&basin_path)?;
            return model.predict(features);
        }

        // Fall back to Erie-wide model
        let wide_path = format!("{}/erie_erie_wide.onnx", models_dir);
        if std::path::Path::new(&wide_path).exists() {
            let model = ErieModel::load(&wide_path)?;
            return model.predict(features);
        }

        Err(format!(
            "No model found in '{}' (tried {} and erie_wide)",
            models_dir,
            basin.name()
        ))
    }
}

/// Build a feature vector from raw candidate data.
///
/// This mirrors the Python `extract_features_from_grid()` function
/// for candidates that come through the Rust pipeline.
pub fn build_feature_vector(
    amplitude_peak: f64,
    amplitude_mean: f64,
    gradient_contrast: f64,
    dipole_separation_m: f64,
    lobe_symmetry: f64,
    axis_offset_deg: f64,
    aspect_ratio: f64,
    flip_distance_km: f64,
    pixel_count: f64,
    flight_line_distance_m: f64,
    longitude: f64,
    curvelet_edge_score: f64,
    width_m: f64,
    height_m: f64,
) -> [f64; FEATURE_COUNT] {
    let basin = ErieBasin::from_longitude(longitude);
    let basin_noise = match basin {
        ErieBasin::Western => 12.0,
        ErieBasin::Central => 6.0,
        ErieBasin::Eastern => 3.5,
    };
    let oh = basin.one_hot();

    [
        amplitude_peak,
        amplitude_mean,
        gradient_contrast,
        dipole_separation_m,
        lobe_symmetry,
        axis_offset_deg,
        aspect_ratio,
        flip_distance_km,
        pixel_count,
        flight_line_distance_m,
        oh[0], // basin_western
        oh[1], // basin_central
        oh[2], // basin_eastern
        amplitude_peak / (basin_noise + 1e-10), // local_snr_vs_basin_median
        curvelet_edge_score,
        width_m,
        height_m,
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basin_from_longitude() {
        assert_eq!(ErieBasin::from_longitude(-83.0), ErieBasin::Western);
        assert_eq!(ErieBasin::from_longitude(-81.0), ErieBasin::Central);
        assert_eq!(ErieBasin::from_longitude(-79.0), ErieBasin::Eastern);
    }

    #[test]
    fn test_one_hot() {
        assert_eq!(ErieBasin::Western.one_hot(), [1.0, 0.0, 0.0]);
        assert_eq!(ErieBasin::Central.one_hot(), [0.0, 1.0, 0.0]);
        assert_eq!(ErieBasin::Eastern.one_hot(), [0.0, 0.0, 1.0]);
    }

    #[test]
    fn test_build_feature_vector() {
        let features = build_feature_vector(
            100.0, 50.0, 5.0, 200.0, 0.8, 30.0, 1.5, 0.5,
            25.0, 500.0, -81.0, 0.3, 300.0, 200.0,
        );
        assert_eq!(features.len(), FEATURE_COUNT);
        assert_eq!(features[0], 100.0); // amplitude_peak
        assert_eq!(features[11], 1.0);  // basin_central
    }

    #[test]
    fn test_stub_predict() {
        let model = ErieModel::load("nonexistent.onnx").unwrap();
        let features = [0.0_f64; FEATURE_COUNT];
        let result = model.predict(&features);
        assert!(result.is_ok());
    }
}
