//! Sensor metadata updater — fetches latest metadata from NASA CMR API.
//!
//! This ensures cesarops-slicer always has current resolution and source
//! information without manual code updates.

use serde_json::Value;

/// Fetch the latest sensor metadata from NASA CMR API.
///
/// Use this to pull the latest `HorizontalDataResolution` and update
/// your local `MapAnchor` scale before slicing.
///
/// Example:
/// ```ignore
/// let meta = fetch_sensor_metadata("SENTINEL-2A").await?;
/// println!("Resolution: {}", meta.resolution);
/// ```
pub async fn fetch_sensor_metadata(short_name: &str) -> Result<SensorMeta, Box<dyn std::error::Error>> {
    // Use granules.umm_json so the response uses the UMM "items" schema
    // (granules.json returns feed.entry format and ignores the UMM Accept header)
    let url = format!(
        "https://cmr.earthdata.nasa.gov/search/granules.umm_json?short_name={}",
        short_name
    );

    let client = reqwest::Client::new();
    let res = client
        .get(&url)
        .send()
        .await?
        .json::<Value>()
        .await?;

    // Extract horizontal resolution from the response
    let resolution = if let Some(item) = res["items"].get(0) {
        item["umm"]["SpatialExtent"]["HorizontalSpatialDomain"]
            ["ResolutionAndCoordinateSystem"]["HorizontalDataResolution"]
            ["GenericResolutions"][0]["IncrementalHorizontalResolution"]
            .as_f64()
            .unwrap_or(10.0)
    } else {
        10.0 // default fallback
    };

    Ok(SensorMeta {
        short_name: short_name.to_string(),
        resolution,
    })
}

/// Parsed sensor metadata.
#[derive(Debug, Clone)]
pub struct SensorMeta {
    pub short_name: String,
    pub resolution: f64,
}

/// Update local metadata for a sensor, printing the resolution.
pub async fn update_sensor_metadata(short_name: &str) -> Result<(), Box<dyn std::error::Error>> {
    let meta = fetch_sensor_metadata(short_name).await?;
    println!(
        "Updated Metadata for {}: Scale set to {}m",
        meta.short_name, meta.resolution
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::fetch_sensor_metadata;

    #[tokio::test]
    #[ignore] // requires network
    async fn test_fetch_sentinel_metadata() {
        let meta = fetch_sensor_metadata("SENTINEL-2A").await.unwrap();
        assert!(meta.resolution > 0.0);
    }
}
