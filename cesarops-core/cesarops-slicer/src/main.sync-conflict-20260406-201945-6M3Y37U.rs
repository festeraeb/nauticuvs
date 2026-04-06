//! CESAROPS Unified Slicer CLI
//!
//! Usage:
//!   slicer slice <geotiff> --output <dir> --tile-size 1024 --provider sentinel-2a
//!   slicer run <mission.json> <geotiff> --output <dir>
//!
//! The `run` command ingests a Qwen-generated mission spec JSON and
//! routes tiles to the appropriate hardware delegates.

use std::path::PathBuf;

use anyhow::Result;
use clap::{Parser, Subcommand};
use tracing::info;

use cesarops_slicer::spec::delegate::DelegateRouter;
use cesarops_slicer::io::geotiff::MmapGeoTiff;
use cesarops_slicer::io::vrt::{VrtDataset, VrtNormalizer, VrtSource, ResampleMethod};
use cesarops_slicer::spec::mission::MissionSpec;
use cesarops_slicer::tiles::slicer::TileSlicer;
use cesarops_slicer::tiles::vrt_slicer::VrtTileSlicer;

#[derive(Parser)]
#[command(name = "slicer", about = "CESAROPS Zero-Copy GeoTIFF Tile Slicer")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Slice a single GeoTIFF into tiles
    Slice {
        /// Path to the GeoTIFF file
        input: PathBuf,

        /// Output directory for tiles
        #[arg(short, long, default_value = "./output")]
        output: PathBuf,

        /// Tile size in pixels (default: 1024)
        #[arg(long, default_value = "1024")]
        tile_size: usize,

        /// Source provider name
        #[arg(long, default_value = "unknown")]
        provider: String,

        /// Band indices (e.g., 0 1 2 for RGB, space-separated)
        #[arg(long, num_args = 1.., default_values_t = vec![0u16])]
        bands: Vec<u16>,

        /// GeoTransform override: "origin_x,pixel_width,row_rot,origin_y,col_rot,pixel_height"
        #[arg(long)]
        geo_transform: Option<String>,
    },

    /// Run a full mission from a Qwen JSON spec
    Run {
        /// Path to the mission spec JSON
        mission: PathBuf,

        /// Path to the GeoTIFF file(s) to process
        #[arg(required = true)]
        inputs: Vec<PathBuf>,

        /// Output directory for tiles
        #[arg(short, long, default_value = "./output")]
        output: PathBuf,
    },

    /// Build and slice from a multi-source VRT stack (Master Stack)
    Vrt {
        /// Source GeoTIFF files with band labels (format: path:band_name:provider)
        /// Example: sentinel.tif:B2_Blue:sentinel-2a
        #[arg(required = true, value_name = "SOURCE")]
        sources: Vec<String>,

        /// Output directory for tiles
        #[arg(short, long, default_value = "./output")]
        output: PathBuf,

        /// Tile size in pixels (default: 1024)
        #[arg(long, default_value = "1024")]
        tile_size: usize,

        /// Target resolution in meters (default: 10.0)
        #[arg(long, default_value = "10.0")]
        target_resolution: f64,

        /// Path to optional mission spec JSON for routing
        #[arg(long)]
        mission: Option<PathBuf>,

        /// CRS (default: EPSG:4326)
        #[arg(long, default_value = "EPSG:4326")]
        crs: String,

        /// GeoTransform: "origin_x,pixel_width,row_rot,origin_y,col_rot,pixel_height"
        #[arg(long)]
        geo_transform: Option<String>,
    },
}

fn main() -> Result<()> {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_target(false)
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "cesarops_slicer=info".into()),
        )
        .init();

    let cli = Cli::parse();

    match cli.command {
        Commands::Slice {
            input,
            output,
            tile_size,
            provider,
            bands,
            geo_transform,
        } => {
            info!("Opening GeoTIFF: {:?}", input);
            let tiff = MmapGeoTiff::open(&input)?;
            info!("Parsed: {}x{}, {} bands", tiff.width, tiff.height, tiff.band_count);

            // Default geo transform (identity — override with --geo-transform)
            let gt: [f64; 6] = if let Some(gt_str) = geo_transform {
                let parts: Vec<f64> = gt_str
                    .split(',')
                    .filter_map(|s| s.trim().parse().ok())
                    .collect();
                parts.try_into().unwrap_or([0.0, 1.0, 0.0, 0.0, 0.0, -1.0])
            } else {
                // Default: 1 pixel = 1 degree, origin at (0, 0)
                [0.0, 1.0, 0.0, 0.0, 0.0, -1.0]
            };

            let slicer = TileSlicer::new(tiff, tile_size, output.clone(), gt, provider, bands);

            info!(
                "Slicing with tile_size={} → {:?}",
                tile_size, output
            );
            let manifest = slicer.slice_all(None)?;
            info!("Done: {} tiles written", manifest.tile_count);
        }

        Commands::Run {
            mission,
            inputs,
            output,
        } => {
            info!("Loading mission spec: {:?}", mission);
            let spec = MissionSpec::from_file(&mission)?;
            info!("Mission: {} → target: {}", spec.mission_id, spec.target_ref);

            let mut router = DelegateRouter::default();

            for input in &inputs {
                info!("Processing: {:?}", input);
                let tiff = MmapGeoTiff::open(input)?;
                info!(
                    "  {}x{}, {} bands, provider: {}",
                    tiff.width, tiff.height, tiff.band_count, spec.target_ref
                );

                // Use search_params bounds as a rough geo transform hint
                let gt = [
                    spec.search_params.bounds[3], // west (origin_x)
                    0.0001,                       // ~10m pixel width (rough)
                    0.0,
                    spec.search_params.bounds[0], // north (origin_y)
                    0.0,
                    -0.0001, // ~10m pixel height
                ];

                let bands: Vec<u16> = (0..tiff.band_count).collect();
                let tile_size = spec.search_params.tile_size.unwrap_or(1024);

                let slicer = TileSlicer::new(
                    tiff,
                    tile_size,
                    output.clone(),
                    gt,
                    spec.target_ref.clone(),
                    bands,
                );

                let manifest = slicer.slice_all(Some(&spec))?;

                // Route tiles
                for entry in &manifest.tiles {
                    let delegate = cesarops_slicer::spec::delegate::DelegateTarget::from_str(
                        &entry.delegate,
                    );
                    router.route(&entry.tile_id, delegate);
                }
            }

            info!("Routing: {}", router.summary());
        }

        Commands::Vrt {
            sources,
            output,
            tile_size,
            target_resolution,
            mission,
            crs,
            geo_transform,
        } => {
            info!("Building VRT Master Stack (target {}m)", target_resolution);

            // Parse source specs: "path:band_name:provider"
            let vrt_sources: Vec<VrtSource> = sources
                .iter()
                .enumerate()
                .map(|(i, spec)| {
                    let parts: Vec<&str> = spec.splitn(3, ':').collect();
                    let path = PathBuf::from(parts[0]);
                    let band_name = parts.get(1).unwrap_or(&"unknown").to_string();
                    let provider = parts.get(2).unwrap_or(&"unknown").to_string();

                    // Estimate native resolution from provider
                    let native_res = if provider.contains("landsat") {
                        30.0
                    } else if provider.contains("sentinel") {
                        10.0
                    } else {
                        target_resolution
                    };

                    let resampling = if (native_res - target_resolution).abs() > 1.0 {
                        ResampleMethod::Bilinear
                    } else {
                        ResampleMethod::NearestNeighbor
                    };

                    info!(
                        "  Band {}: {} ({}, {}m native → {}m target, {})",
                        i, band_name, provider, native_res, target_resolution,
                        resampling.to_gdal_str()
                    );

                    VrtSource {
                        path,
                        virtual_band: i,
                        native_resolution: native_res,
                        resampling,
                        band_name,
                        provider,
                    }
                })
                .collect();

            // Default geo transform
            let gt: [f64; 6] = if let Some(gt_str) = geo_transform {
                let parts: Vec<f64> = gt_str
                    .split(',')
                    .filter_map(|s| s.trim().parse().ok())
                    .collect();
                parts.try_into().unwrap_or([0.0, 1.0, 0.0, 0.0, 0.0, -1.0])
            } else {
                [0.0, 1.0, 0.0, 0.0, 0.0, -1.0]
            };

            // Generate VRT XML
            let normalizer = VrtNormalizer::new(target_resolution, crs.clone());
            let vrt_xml = normalizer.create_virtual_stack(
                &vrt_sources,
                10000, // Default — should be computed from bounds
                10000,
                gt,
            );

            let vrt_path = output.join("master_stack.vrt");
            std::fs::create_dir_all(&output).context("failed to create output dir")?;
            std::fs::write(&vrt_path, &vrt_xml).context("failed to write VRT")?;
            info!("VRT written to {:?}", vrt_path);

            // Parse the VRT and slice
            let vrt = VrtDataset::from_file(&vrt_path)?;
            let slicer = VrtTileSlicer::new(
                vrt,
                tile_size,
                output.clone(),
                "vrt-stack".to_string(),
                vrt_sources.iter().map(|s| s.band_name.clone()).collect(),
            );

            let mission_spec = if let Some(mission_path) = mission {
                Some(MissionSpec::from_file(&mission_path)?)
            } else {
                None
            };

            let manifest = slicer.slice_all(mission_spec.as_ref())?;
            info!(
                "VRT Done: {} tiles, {} bands → {:?}",
                manifest.tile_count, manifest.band_count, output
            );
        }
    }

    Ok(())
}
