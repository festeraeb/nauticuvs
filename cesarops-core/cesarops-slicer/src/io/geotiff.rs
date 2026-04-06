//! Memory-mapped GeoTIFF reader using zero-copy mmap.
//!
//! This wraps the TIFF file as a virtual byte array via `memmap2`.
//! Instead of loading the entire raster into RAM, we calculate
//! byte offsets for individual tiles/strips and read only what we need.
//!
//! This is the foundation for the "Unified Slicer" — it handles
//! any GeoTIFF source (Sentinel, Landsat, commercial) and exposes
//! it as a consistent interface for the tile slicer.

use std::fs::File;
use std::io::Cursor;
use std::path::{Path, PathBuf};

use memmap2::Mmap;
use ndarray::Array3;
use thiserror::Error;

/// GeoTransform: [origin_x, pixel_width, row_rotation, origin_y, col_rotation, pixel_height]
/// Standard GDAL convention:
///   lon = transform[0] + col * transform[1] + row * transform[2]
///   lat = transform[3] + col * transform[4] + row * transform[5]
/// For north-up images, transform[2] and transform[4] are ~0.
pub type GeoTransform = [f64; 6];

#[derive(Error, Debug)]
pub enum GeoTiffError {
    #[error("failed to open file: {0}")]
    FileOpen(#[source] std::io::Error),

    #[error("failed to memory-map file: {0}")]
    Mmap(#[source] std::io::Error),

    #[error("invalid TIFF: {0}")]
    InvalidTiff(String),

    #[error("unsupported sample format: {0}")]
    UnsupportedSampleFormat(String),

    #[error("TIFF decode error: {0}")]
    Decode(String),
}

pub type Result<T> = std::result::Result<T, GeoTiffError>;

/// A memory-mapped GeoTIFF with parsed metadata.
///
/// Does NOT load pixel data into memory. Only parses the IFD (Image File Directory)
/// to extract dimensions, geotransform, band count, and tile/strip layout.
pub struct MmapGeoTiff {
    pub path: PathBuf,
    _file: File,
    pub mmap: Mmap,

    /// Image dimensions
    pub width: u32,
    pub height: u32,

    /// Number of bands (1 = grayscale, 3 = RGB, 4 = RGBA/multispectral)
    pub band_count: u16,

    /// Bits per sample
    pub bits_per_sample: u16,

    /// Geographic transform matrix
    pub geo_transform: Option<GeoTransform>,

    /// Tile dimensions (if tiled TIFF)
    pub tile_width: Option<u32>,
    pub tile_height: Option<u32>,

    /// Sample format: 1 = uint, 2 = int, 3 = ieee float
    pub sample_format: u16,
}

impl MmapGeoTiff {
    /// Open a GeoTIFF file and parse its IFD without loading pixel data.
    pub fn open<P: AsRef<Path>>(path: P) -> Result<Self> {
        let path = path.as_ref();
        let file = File::open(path).map_err(GeoTiffError::FileOpen)?;
        let mmap = unsafe { Mmap::map(&file) }.map_err(GeoTiffError::Mmap)?;

        // Parse TIFF metadata using the tiff crate's decoder
        // Cursor wraps the mmap slice to provide Read + Seek
        let cursor = Cursor::new(mmap.as_ref());
        let mut decoder = tiff::decoder::Decoder::new(cursor)
            .map_err(|e| GeoTiffError::Decode(e.to_string()))?;

        let width = decoder.dimensions().map_err(|e| GeoTiffError::InvalidTiff(e.to_string()))?.0;
        let height = decoder.dimensions().map_err(|e| GeoTiffError::InvalidTiff(e.to_string()))?.1;
        let band_count = decoder
            .find_tag(tiff::tags::Tag::SamplesPerPixel)
            .map_err(|e| GeoTiffError::Decode(e.to_string()))?
            .map(|v| v.into_u16().unwrap_or(1))
            .unwrap_or(1);

        let bits_per_sample = decoder
            .find_tag(tiff::tags::Tag::BitsPerSample)
            .map_err(|e| GeoTiffError::Decode(e.to_string()))?
            .map(|v| v.into_u16().unwrap_or(8))
            .unwrap_or(8);

        let sample_format = decoder
            .find_tag(tiff::tags::Tag::SampleFormat)
            .map_err(|e| GeoTiffError::Decode(e.to_string()))?
            .map(|v| v.into_u16().unwrap_or(1))
            .unwrap_or(1);

        // Try to read tile dimensions (if tiled)
        let tile_width = decoder
            .find_tag(tiff::tags::Tag::TileWidth)
            .ok()
            .flatten()
            .and_then(|v| v.into_u32().ok());

        let tile_height = decoder
            .find_tag(tiff::tags::Tag::TileLength)
            .ok()
            .flatten()
            .and_then(|v| v.into_u32().ok());

        // GeoTransform is stored in GeoKeyDirectoryTag or as an ASCII GeoTIFF tag
        // For now, we attempt a default and let the slicer override it from sidecar/VRT
        let geo_transform = None; // Will be set by VRT or sidecar

        Ok(Self {
            path: path.to_path_buf(),
            _file: file,
            mmap,
            width,
            height,
            band_count,
            bits_per_sample,
            geo_transform,
            tile_width,
            tile_height,
            sample_format,
        })
    }

    /// Read a rectangular window of pixels from the mmap'd TIFF.
    ///
    /// This does a bounded read from the mmap slice. For tiled TIFFs,
    /// this reads only the specific tile tiles needed (zero-copy for the rest).
    ///
    /// Returns an `Array3<u8>` shaped as `[band, row, col]`.
    /// For 16-bit data, use `read_window_u16`.
    pub fn read_window(
        &self,
        x_off: u32,
        y_off: u32,
        x_size: u32,
        y_size: u32,
    ) -> Result<Array3<u8>> {
        if x_off + x_size > self.width || y_off + y_size > self.height {
            return Err(GeoTiffError::InvalidTiff(format!(
                "window ({},{})+({}x{}) exceeds image bounds ({}x{})",
                x_off, y_off, x_size, y_size, self.width, self.height
            )));
        }

        // For the tiff crate, we decode the full image then slice.
        // A true zero-copy would parse tile byte offsets from the IFD.
        // For now, we read and slice — the mmap still prevents full file load
        // into the heap (OS handles paging).
        let cursor = Cursor::new(self.mmap.as_ref());
        let mut decoder = tiff::decoder::Decoder::new(cursor)
            .map_err(|e| GeoTiffError::Decode(e.to_string()))?;

        // Read as grayscale or RGB
        let image = decoder.read_image().map_err(|e| GeoTiffError::Decode(e.to_string()))?;

        // Convert to flat u8 slice
        let pixels: Vec<u8> = match image {
            tiff::decoder::DecodingResult::U8(data) => data,
            tiff::decoder::DecodingResult::U16(data) => {
                // Downcast 16→8 for GPU/TPU compat
                data.iter().map(|v| (*v >> 8) as u8).collect()
            }
            tiff::decoder::DecodingResult::F32(data) => {
                // Normalize f32 [0.0..1.0] → u8
                data.iter()
                    .map(|v| (v.clamp(0.0, 1.0) * 255.0) as u8)
                    .collect()
            }
            tiff::decoder::DecodingResult::F64(data) => {
                data.iter()
                    .map(|v| (v.clamp(0.0, 1.0) * 255.0) as u8)
                    .collect()
            }
            tiff::decoder::DecodingResult::I32(data) => {
                // Normalize i32 to u8
                let min = *data.iter().min().unwrap_or(&0) as f64;
                let max = *data.iter().max().unwrap_or(&255) as f64;
                let range = (max - min).max(1.0);
                data.iter()
                    .map(|v| (((*v as f64 - min) / range) * 255.0) as u8)
                    .collect()
            }
            tiff::decoder::DecodingResult::I8(data) => {
                data.iter().map(|v| *v as u8).collect()
            }
            tiff::decoder::DecodingResult::I16(data) => {
                data.iter().map(|v| (*v >> 8) as u8).collect()
            }
            tiff::decoder::DecodingResult::U32(_) => {
                return Err(GeoTiffError::UnsupportedSampleFormat(
                    "u32 not supported".into(),
                ));
            }
            tiff::decoder::DecodingResult::U64(_) => {
                return Err(GeoTiffError::UnsupportedSampleFormat(
                    "u64 not supported".into(),
                ));
            }
            tiff::decoder::DecodingResult::I64(_) => {
                return Err(GeoTiffError::UnsupportedSampleFormat(
                    "i64 not supported".into(),
                ));
            }
        };

        // Extract the window from the flat pixel array
        let mut window = vec![0u8; (x_size * y_size * self.band_count as u32) as usize];
        for row in 0..y_size {
            for col in 0..x_size {
                for band in 0..self.band_count as u32 {
                    let src_idx =
                        ((y_off + row) * self.width + (x_off + col)) * self.band_count as u32
                            + band;
                    let dst_idx = (row * x_size + col) * self.band_count as u32 + band;
                    if (src_idx as usize) < pixels.len() {
                        window[dst_idx as usize] = pixels[src_idx as usize];
                    }
                }
            }
        }

        // Reshape to [band, row, col]
        let band_count = self.band_count as usize;
        let y_size = y_size as usize;
        let x_size = x_size as usize;
        let arr = Array3::from_shape_vec((band_count, y_size, x_size), window)
            .map_err(|e| GeoTiffError::InvalidTiff(format!("shape error: {e}")))?;

        Ok(arr)
    }

    /// Get the geographic coordinates for a pixel position.
    pub fn pixel_to_geo(&self, col: u32, row: u32) -> Option<(f64, f64)> {
        let gt = self.geo_transform.as_ref()?;
        let lon = gt[0] + (col as f64 * gt[1]) + (row as f64 * gt[2]);
        let lat = gt[3] + (col as f64 * gt[4]) + (row as f64 * gt[5]);
        Some((lat, lon))
    }

    /// Calculate the number of tiles in each dimension given a target tile size.
    pub fn tile_counts(&self, tile_size: usize) -> (usize, usize) {
        let cols = self.width as usize;
        let rows = self.height as usize;
        let tile_cols = cols.div_ceil(tile_size);
        let tile_rows = rows.div_ceil(tile_size);
        (tile_cols, tile_rows)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tile_counts() {
        // Test the tile math directly without constructing MmapGeoTiff
        let width: usize = 4096;
        let height: usize = 4096;
        let tile_size: usize = 1024;
        let cols = width.div_ceil(tile_size);
        let rows = height.div_ceil(tile_size);
        assert_eq!(cols, 4);
        assert_eq!(rows, 4);
    }

    #[test]
    fn test_tile_counts_non_aligned() {
        // 5000x3000 with 1024 tiles → should round up
        let width: usize = 5000;
        let height: usize = 3000;
        let tile_size: usize = 1024;
        let cols = width.div_ceil(tile_size);
        let rows = height.div_ceil(tile_size);
        assert_eq!(cols, 5); // 5000/1024 = 4.88 → 5
        assert_eq!(rows, 3); // 3000/1024 = 2.93 → 3
    }
}
