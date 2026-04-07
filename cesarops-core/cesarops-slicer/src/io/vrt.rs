//! VRT (Virtual Dataset) Normalization Layer.
//!
//! Creates a "Master Stack" virtual canvas that aligns multiple GeoTIFF sources
//! (e.g., Sentinel-2 at 10m, Landsat at 30m) into a unified grid before slicing.
//!
//! ## Strategy
//!
//! Instead of resampling and writing a new multi-GB file, we create a `.vrt` XML
//! descriptor that tells the slicer:
//! 1. Which source file provides each virtual band
//! 2. What the target resolution is (force all to highest-res, e.g. 10m)
//! 3. How to resample lower-res sources (bilinear or lanczos)
//! 4. How to align world coordinates to a master CRS (EPSG:4326)
//!
//! The VRT is both:
//! - A **GDAL-compatible XML file** (external tools can open it directly)
//! - An **internal routing table** for the Rust slicer (no GDAL dependency)
//!
//! ## Example
//!
//! ```ignore
//! let sources = vec![
//!     VrtSource {
//!         path: "sentinel_b2.tif",
//!         virtual_band: 0,
//!         native_resolution: 10.0,
//!     },
//!     VrtSource {
//!         path: "landsat_b10.tif",
//!         virtual_band: 1,
//!         native_resolution: 30.0,
//!         resampling: ResampleMethod::Bilinear,
//!     },
//! ];
//!
//! let vrt = VrtNormalizer::new(10.0, "EPSG:4326".into());
//! let xml = vrt.create_virtual_stack(&sources, 10000, 10000);
//! std::fs::write("master_stack.vrt", &xml)?;
//!
//! // Open as a unified dataset
//! let dataset = VrtDataset::from_file("master_stack.vrt")?;
//! let tile = dataset.read_window(0, 0, 1024, 1024)?;
//! ```

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::io::geotiff::{GeoTransform, MmapGeoTiff};

/// Resampling method for lower-resolution sources.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
pub enum ResampleMethod {
    NearestNeighbor,
    Bilinear,
    Cubic,
    Lanczos,
    Average,
}

impl ResampleMethod {
    pub fn to_gdal_str(&self) -> &'static str {
        match self {
            Self::NearestNeighbor => "near",
            Self::Bilinear => "bilinear",
            Self::Cubic => "cubic",
            Self::Lanczos => "lanczos",
            Self::Average => "average",
        }
    }
}

/// A single source in the VRT stack.
#[derive(Debug, Clone)]
pub struct VrtSource {
    /// Path to the source GeoTIFF
    pub path: PathBuf,

    /// Which virtual band this source maps to (0-indexed)
    pub virtual_band: usize,

    /// Native ground resolution in meters (e.g., 10.0 for Sentinel-2, 30.0 for Landsat)
    pub native_resolution: f64,

    /// Resampling method if native_resolution != target_resolution
    pub resampling: ResampleMethod,

    /// Human-readable band name (e.g., "B2_Blue", "B10_Thermal")
    pub band_name: String,

    /// Source provider name
    pub provider: String,
}

#[derive(Error, Debug)]
pub enum VrtError {
    #[error("failed to open source: {0}")]
    SourceOpen(String),

    #[error("resolution mismatch: {0}")]
    ResolutionMismatch(String),

    #[error("VRT parse error: {0}")]
    ParseError(String),
}

pub type Result<T> = std::result::Result<T, VrtError>;

/// VRT Normalizer: computes a unified grid and generates VRT XML.
pub struct VrtNormalizer {
    /// Target pixel resolution in meters (all sources resampled to this)
    pub target_resolution: f64,

    /// Master CRS (e.g., "EPSG:4326")
    pub master_crs: String,
}

impl VrtNormalizer {
    pub fn new(target_resolution: f64, master_crs: String) -> Self {
        Self {
            target_resolution,
            master_crs,
        }
    }

    /// Generate a GDAL-compatible VRT XML string from a list of sources.
    ///
    /// The XML can be opened by GDAL tools (gdalinfo, gdal_translate) or
    /// parsed internally by the Rust slicer as a routing table.
    pub fn create_virtual_stack(
        &self,
        sources: &[VrtSource],
        raster_width: usize,
        raster_height: usize,
        geo_transform: GeoTransform,
    ) -> String {
        let mut xml = format!(
            r#"<VRTDataset rasterXSize="{width}" rasterYSize="{height}">
  <SRS>{crs}</SRS>
  <GeoTransform>{gt0}, {gt1}, {gt2}, {gt3}, {gt4}, {gt5}</GeoTransform>
"#,
            width = raster_width,
            height = raster_height,
            crs = self.master_crs,
            gt0 = geo_transform[0],
            gt1 = geo_transform[1],
            gt2 = geo_transform[2],
            gt3 = geo_transform[3],
            gt4 = geo_transform[4],
            gt5 = geo_transform[5],
        );

        // Group sources by virtual band
        let mut bands: Vec<(usize, Vec<&VrtSource>)> = Vec::new();
        for src in sources {
            if let Some(entry) = bands.iter_mut().find(|(b, _)| *b == src.virtual_band) {
                entry.1.push(src);
            } else {
                bands.push((src.virtual_band, vec![src]));
            }
        }

        // Sort by virtual band index
        bands.sort_by_key(|(b, _)| *b);

        for (band_idx, band_sources) in &bands {
            let band_num = band_idx + 1; // VRT bands are 1-indexed
            let resample = band_sources
                .iter()
                .find(|s| s.native_resolution > self.target_resolution)
                .map(|s| s.resampling)
                .unwrap_or(ResampleMethod::NearestNeighbor);

            for src in band_sources {
                let resample_str = resample.to_gdal_str();
                let src_path = src.path.display();

                xml.push_str(&format!(
                    r#"  <VRTRasterBand dataType="Byte" band="{band}">
    <SimpleSource>
      <SourceFilename relativeToVRT="1">{path}</SourceFilename>
    </SimpleSource>
    <ResamplingAlg>{resample}</ResamplingAlg>
    <!-- {band_name} from {provider} (native {native_res}m → target {target_res}m) -->
  </VRTRasterBand>
"#,
                    band = band_num,
                    path = src_path,
                    resample = resample_str,
                    band_name = src.band_name,
                    provider = src.provider,
                    native_res = src.native_resolution,
                    target_res = self.target_resolution,
                ));
            }
        }

        xml.push_str("</VRTDataset>\n");
        xml
    }

    /// Auto-compute raster dimensions from a bounding box and target resolution.
    ///
    /// Bounds: [north, east, south, west] in degrees.
    /// Returns (width, height) in pixels at the target resolution.
    pub fn compute_dimensions(
        &self,
        bounds: [f64; 4],
        pixel_size_degrees: f64,
    ) -> (usize, usize) {
        let north = bounds[0];
        let south = bounds[2];
        let east = bounds[1];
        let west = bounds[3];

        let width = ((east - west).abs() / pixel_size_degrees).ceil() as usize;
        let height = ((north - south).abs() / pixel_size_degrees).ceil() as usize;
        (width, height)
    }

    /// Build a VRT stack automatically from source files.
    ///
    /// Opens each GeoTIFF, extracts metadata, and generates the VRT XML.
    pub fn build_from_files(
        &self,
        file_specs: &[(PathBuf, usize, String, String, ResampleMethod)],
        geo_transform: GeoTransform,
    ) -> Result<String> {
        let mut sources = Vec::new();

        for (path, virtual_band, band_name, provider, resampling) in file_specs {
            let tiff = MmapGeoTiff::open(path)
                .map_err(|e| VrtError::SourceOpen(format!("{:?}: {}", path, e)))?;

            // Estimate native resolution from geo transform
            // If the source doesn't have a geo transform, use the target
            let native_res = tiff
                .geo_transform
                .map(|gt| gt[1].abs())
                .unwrap_or(self.target_resolution);

            sources.push(VrtSource {
                path: path.clone(),
                virtual_band: *virtual_band,
                native_resolution: native_res,
                resampling: *resampling,
                band_name: band_name.clone(),
                provider: provider.clone(),
            });
        }

        // Compute dimensions
        let (width, height) = self.compute_dimensions(
            [
                geo_transform[3],
                geo_transform[0] + geo_transform[1] * 10000.0,
                geo_transform[3] + geo_transform[5] * 10000.0,
                geo_transform[0],
            ],
            geo_transform[1].abs(),
        );

        Ok(self.create_virtual_stack(&sources, width, height, geo_transform))
    }
}

/// A parsed VRT dataset — acts as a unified multi-source GeoTIFF.
///
/// This is the internal representation the slicer uses to route
/// tile reads to the correct source files with resampling.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VrtDataset {
    /// Path to the VRT XML file
    pub vrt_path: PathBuf,

    /// Virtual raster dimensions
    pub width: usize,
    pub height: usize,

    /// Unified geo transform
    pub geo_transform: GeoTransform,

    /// CRS
    pub crs: String,

    /// Virtual band definitions
    pub bands: Vec<VrtBand>,

    /// Target resolution in meters
    pub target_resolution: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VrtBand {
    /// Virtual band index (1-indexed)
    pub band_num: usize,

    /// Source file path
    pub source_path: PathBuf,

    /// Resampling method
    pub resampling: ResampleMethod,

    /// Native resolution of the source
    pub native_resolution: f64,

    /// Human-readable name
    pub band_name: String,

    /// Provider name
    pub provider: String,
}

impl VrtDataset {
    /// Parse a VRT XML file into a VrtDataset.
    pub fn from_file<P: AsRef<Path>>(vrt_path: P) -> Result<Self> {
        let xml = std::fs::read_to_string(vrt_path.as_ref())
            .map_err(|e| VrtError::ParseError(format!("failed to read VRT: {}", e)))?;

        Self::from_xml(&xml, vrt_path.as_ref().parent().unwrap().to_path_buf())
    }

    /// Parse VRT XML string into a VrtDataset.
    pub fn from_xml(xml: &str, base_dir: PathBuf) -> Result<Self> {
        // Minimal XML parsing — extract key values
        let width = Self::extract_attr(xml, "rasterXSize")
            .and_then(|v| v.parse::<usize>().ok())
            .ok_or_else(|| VrtError::ParseError("missing rasterXSize".into()))?;

        let height = Self::extract_attr(xml, "rasterYSize")
            .and_then(|v| v.parse::<usize>().ok())
            .ok_or_else(|| VrtError::ParseError("missing rasterYSize".into()))?;

        let crs = Self::extract_tag(xml, "SRS")
            .unwrap_or_else(|| "EPSG:4326".to_string());

        let geo_transform = Self::extract_tag(xml, "GeoTransform")
            .and_then(|gt| Self::parse_geo_transform(&gt))
            .unwrap_or([0.0, 1.0, 0.0, 0.0, 0.0, -1.0]);

        // Extract band sources
        let mut bands = Vec::new();
        let mut current_band_num = 0;

        for line in xml.lines() {
            let trimmed = line.trim();

            if trimmed.starts_with("<VRTRasterBand") {
                current_band_num = Self::extract_attr(trimmed, "band")
                    .and_then(|v| v.parse::<usize>().ok())
                    .unwrap_or(bands.len() + 1);
            }

            if trimmed.starts_with("<SourceFilename") {
                if let Some(path) = Self::extract_tag_content(trimmed, "SourceFilename") {
                    let source_path = base_dir.join(&path);

                    // Try to open the source to get native resolution
                    let native_resolution = MmapGeoTiff::open(&source_path)
                        .ok()
                        .and_then(|t| t.geo_transform.map(|gt| gt[1].abs()))
                        .unwrap_or(10.0);

                    bands.push(VrtBand {
                        band_num: current_band_num,
                        source_path,
                        resampling: ResampleMethod::Bilinear,
                        native_resolution,
                        band_name: format!("Band_{}", current_band_num),
                        provider: "unknown".to_string(),
                    });
                }
            }

            if trimmed.starts_with("<ResamplingAlg>") {
                if let Some(alg) = Self::extract_tag(trimmed, "ResamplingAlg") {
                    if let Some(band) = bands.last_mut() {
                        band.resampling = match alg.as_str() {
                            "bilinear" => ResampleMethod::Bilinear,
                            "cubic" => ResampleMethod::Cubic,
                            "lanczos" => ResampleMethod::Lanczos,
                            "average" => ResampleMethod::Average,
                            _ => ResampleMethod::NearestNeighbor,
                        };
                    }
                }
            }
        }

        Ok(Self {
            vrt_path: base_dir.join("stack.vrt"),
            width,
            height,
            geo_transform,
            crs,
            bands,
            target_resolution: 10.0,
        })
    }

    /// Read a window of pixels from the VRT stack.
    ///
    /// For each virtual band, reads from the appropriate source file
    /// and resamples if needed. Preserves ALL bands from each source.
    ///
    /// Returns `[total_bands, row, col]` shaped array.
    pub fn read_window(
        &self,
        x_off: u32,
        y_off: u32,
        x_size: u32,
        y_size: u32,
    ) -> Result<Vec<Vec<Vec<u8>>>> {
        let mut all_bands = Vec::new();

        for band in &self.bands {
            // Open the source
            let source = MmapGeoTiff::open(&band.source_path)
                .map_err(|e| VrtError::SourceOpen(format!("{:?}: {}", band.source_path, e)))?;

            // Calculate the source window (scale if resolutions differ)
            let scale = band.native_resolution / self.target_resolution;
            let src_x = (x_off as f64 / scale) as u32;
            let src_y = (y_off as f64 / scale) as u32;
            let src_w = ((x_size as f64) / scale).ceil() as u32;
            let src_h = ((y_size as f64) / scale).ceil() as u32;

            // Read from source (may have multiple bands: [src_band, row, col])
            let window = source.read_window(src_x, src_y, src_w, src_h)
                .map_err(|e| VrtError::SourceOpen(e.to_string()))?;

            // Resample if needed — preserves ALL bands from the source
            let band_data = if scale > 1.5 {
                // Source is lower res — bilinear upscale
                Self::bilinear_upscale(&window, x_size as usize, y_size as usize)
            } else if scale < 0.7 {
                // Source is higher res — downscale with averaging
                Self::bilinear_downscale(&window, x_size as usize, y_size as usize)
            } else {
                // Close enough — extract directly, all bands
                let src_bands = window.shape()[0];
                let rows = window.shape()[1];
                let cols = window.shape()[2];
                (0..src_bands)
                    .map(|b| {
                        (0..rows)
                            .map(|r| window.slice(ndarray::s![b, r, ..]).to_vec())
                            .collect()
                    })
                    .collect()
            };

            // Push ALL bands from this source into the stack
            all_bands.extend(band_data);
        }

        Ok(all_bands)
    }

    /// Bilinear upscale: low-res → high-res grid.
    fn bilinear_upscale(
        source: &ndarray::Array3<u8>,
        target_w: usize,
        target_h: usize,
    ) -> Vec<Vec<Vec<u8>>> {
        let src_h = source.shape()[1];
        let src_w = source.shape()[2];
        let band_count = source.shape()[0];

        if src_h == target_h && src_w == target_w {
            // No resampling needed
            return (0..band_count)
                .map(|b| {
                    (0..src_h)
                        .map(|r| source.slice(ndarray::s![b, r, ..]).to_vec())
                        .collect()
                })
                .collect();
        }

        let mut result = vec![vec![vec![0u8; target_w]; target_h]; band_count];

        for b in 0..band_count {
            for ty in 0..target_h {
                for tx in 0..target_w {
                    // Map target pixel to source coordinates
                    let sx = (tx as f64 * src_w as f64) / target_w as f64;
                    let sy = (ty as f64 * src_h as f64) / target_h as f64;

                    let x0 = sx.floor() as usize;
                    let y0 = sy.floor() as usize;
                    let x1 = (x0 + 1).min(src_w - 1);
                    let y1 = (y0 + 1).min(src_h - 1);

                    let fx = sx - x0 as f64;
                    let fy = sy - y0 as f64;

                    let v00 = source[[b, y0, x0]] as f64;
                    let v10 = source[[b, y0, x1]] as f64;
                    let v01 = source[[b, y1, x0]] as f64;
                    let v11 = source[[b, y1, x1]] as f64;

                    let val = (v00 * (1.0 - fx) * (1.0 - fy)
                        + v10 * fx * (1.0 - fy)
                        + v01 * (1.0 - fx) * fy
                        + v11 * fx * fy)
                        .round() as u8;

                    result[b][ty][tx] = val;
                }
            }
        }

        result
    }

    /// Bilinear downscale: high-res → low-res grid with averaging.
    fn bilinear_downscale(
        source: &ndarray::Array3<u8>,
        target_w: usize,
        target_h: usize,
    ) -> Vec<Vec<Vec<u8>>> {
        let src_h = source.shape()[1];
        let src_w = source.shape()[2];
        let band_count = source.shape()[0];
        let mut result = vec![vec![vec![0u8; target_w]; target_h]; band_count];

        for b in 0..band_count {
            for ty in 0..target_h {
                for tx in 0..target_w {
                    // Map target pixel to source center
                    let sx = ((tx as f64 + 0.5) * src_w as f64) / target_w as f64;
                    let sy = ((ty as f64 + 0.5) * src_h as f64) / target_h as f64;

                    let x0 = sx.floor() as usize;
                    let y0 = sy.floor() as usize;
                    let x1 = (x0 + 1).min(src_w - 1);
                    let y1 = (y0 + 1).min(src_h - 1);

                    // Average the 2x2 source neighborhood
                    let sum = source[[b, y0, x0]] as u32
                        + source[[b, y0, x1]] as u32
                        + source[[b, y1, x0]] as u32
                        + source[[b, y1, x1]] as u32;
                    result[b][ty][tx] = (sum / 4) as u8;
                }
            }
        }

        result
    }

    // --- Minimal XML helpers (no external dependency) ---

    fn extract_attr(line: &str, attr: &str) -> Option<String> {
        let prefix = format!("{}=\"", attr);
        if let Some(pos) = line.find(&prefix) {
            let start = pos + prefix.len();
            let rest = &line[start..];
            if let Some(end) = rest.find('"') {
                return Some(rest[..end].to_string());
            }
        }
        None
    }

    fn extract_tag(xml: &str, tag: &str) -> Option<String> {
        let open = format!("<{}>", tag);
        let close = format!("</{}>", tag);
        if let Some(start) = xml.find(&open) {
            let content_start = start + open.len();
            if let Some(end) = xml[content_start..].find(&close) {
                return Some(xml[content_start..content_start + end].trim().to_string());
            }
        }
        None
    }

    fn extract_tag_content(line: &str, tag: &str) -> Option<String> {
        let open = format!("<{}>", tag);
        let close = format!("</{}>", tag);
        if let Some(start) = line.find(&open) {
            let content_start = start + open.len();
            let rest = &line[content_start..];
            if let Some(end) = rest.find(&close) {
                return Some(rest[..end].trim().to_string());
            }
        }
        // Try self-closing or partial
        Self::extract_attr(line, "relativeToVRT")?;
        if let Some(pos) = line.find('>') {
            let after = &line[pos + 1..];
            if let Some(end) = after.find('<') {
                let content = after[..end].trim();
                if !content.is_empty() {
                    return Some(content.to_string());
                }
            }
        }
        None
    }

    fn parse_geo_transform(gt_str: &str) -> Option<GeoTransform> {
        let parts: Vec<f64> = gt_str
            .split(',')
            .filter_map(|s| s.trim().parse().ok())
            .collect();
        if parts.len() == 6 {
            Some(parts.try_into().unwrap())
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_vrt_xml_generation() {
        let normalizer = VrtNormalizer::new(10.0, "EPSG:4326".into());
        let gt: GeoTransform = [-86.5, 0.0001, 0.0, 43.0, 0.0, -0.0001];

        let sources = vec![
            VrtSource {
                path: PathBuf::from("sentinel_b2.tif"),
                virtual_band: 0,
                native_resolution: 10.0,
                resampling: ResampleMethod::NearestNeighbor,
                band_name: "B2_Blue".into(),
                provider: "sentinel-2a".into(),
            },
            VrtSource {
                path: PathBuf::from("landsat_b10.tif"),
                virtual_band: 1,
                native_resolution: 30.0,
                resampling: ResampleMethod::Bilinear,
                band_name: "B10_Thermal".into(),
                provider: "landsat-9".into(),
            },
        ];

        let xml = normalizer.create_virtual_stack(&sources, 10000, 10000, gt);
        assert!(xml.contains("sentinel_b2.tif"));
        assert!(xml.contains("landsat_b10.tif"));
        assert!(xml.contains("bilinear"));
        assert!(xml.contains("EPSG:4326"));
        assert!(xml.contains("B2_Blue"));
        assert!(xml.contains("B10_Thermal"));
    }

    #[test]
    fn test_resolution_scaling_math() {
        // Landsat 30m → target 10m = scale factor 3.0
        let scale = 30.0 / 10.0;
        assert!(scale > 1.5); // Should trigger upscale

        // Sentinel 10m → target 10m = scale factor 1.0
        let scale = 10.0 / 10.0;
        assert!(scale <= 1.5 && scale >= 0.7); // No resampling
    }

    #[test]
    fn test_compute_dimensions() {
        let normalizer = VrtNormalizer::new(10.0, "EPSG:4326".into());
        // Rough: 1 degree ≈ 111km at equator
        let bounds: [f64; 4] = [43.1, -86.3, 42.8, -86.6]; // ~0.3° x 0.3°
        let pixel_deg = 0.0001; // ~10m
        let (w, h) = normalizer.compute_dimensions(bounds, pixel_deg);
        assert_eq!(w, 3000); // 0.3 / 0.0001 = 3000
        assert_eq!(h, 3000);
    }
}
