//! KML/KMZ generator with polygon overlays and Swayze wreck matching.
//!
//! Reads scan results JSON produced by the Python BAG scanner and generates
//! KMZ files with:
//!   - Polygon overlays (shaded shapes) for each redacted area
//!   - Hover tooltips: BAG source, depth, position, size, redaction technique
//!   - Swayze DB matching with confidence scores
//!   - Organised folders by confidence tier

use rusqlite::Connection;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::io::Write;
use zip::write::{FileOptions, ZipWriter};
use zip::CompressionMethod;

// ── Data structures matching Python scanner output ──────────────────────────

#[derive(Debug, Deserialize, Clone)]
#[allow(dead_code)]
pub struct ScanResults {
    pub file: String,
    #[serde(default)]
    pub scan_id: Option<i64>,
    pub candidates: Vec<WreckCandidate>,
    pub redaction_signatures: Vec<RedactionSignature>,
    #[serde(default)]
    pub total_candidates: usize,
    #[serde(default)]
    pub total_signatures: usize,
    #[serde(default)]
    pub processing_time_ms: u64,
}

#[derive(Debug, Deserialize, Clone)]
#[allow(dead_code)]
pub struct WreckCandidate {
    pub latitude: f64,
    pub longitude: f64,
    pub size_sq_meters: f64,
    pub size_sq_feet: f64,
    pub width_meters: f64,
    pub height_meters: f64,
    pub width_feet: f64,
    pub height_feet: f64,
    pub confidence: f64,
    pub method: String,
    pub processing_time_ms: u64,
    /// (min_lon, min_lat, max_lon, max_lat)
    pub bounding_box: (f64, f64, f64, f64),
    #[serde(default)]
    pub redaction_signatures: Option<Vec<RedactionSignature>>,
    #[serde(default)]
    pub elevation_stats: Option<HashMap<String, f64>>,
    #[serde(default)]
    pub uncertainty_stats: Option<HashMap<String, f64>>,
    #[serde(default)]
    pub anomaly_score: f64,
    #[serde(default)]
    pub shape_complexity: f64,
    #[serde(default)]
    pub depth_gradient: f64,
}

#[derive(Debug, Deserialize, Clone)]
pub struct RedactionSignature {
    pub signature_type: String,
    pub confidence: f64,
    /// (lat, lon)
    pub location: (f64, f64),
    /// (min_lon, min_lat, max_lon, max_lat)
    pub bounding_box: (f64, f64, f64, f64),
    pub size_pixels: u32,
    pub size_meters_sq: f64,
    #[serde(default)]
    pub redactor_id: Option<String>,
    #[serde(default)]
    pub technique_used: Option<String>,
    #[serde(default)]
    pub evidence: Option<HashMap<String, serde_json::Value>>,
}

// ── Swayze DB wreck record ──────────────────────────────────────────────────

#[derive(Debug, Serialize, Clone)]
pub struct SwayzeMatch {
    pub wreck_id: i64,
    pub name: String,
    pub date: Option<String>,
    pub latitude: f64,
    pub longitude: f64,
    pub depth: Option<f64>,
    pub feature_type: Option<String>,
    pub hull_material: Option<String>,
    pub distance_meters: f64,
    pub confidence_score: f64,
}

// ── KMZ generation input ────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
pub struct KmzRequest {
    /// Path to scan results JSON (single file or batch summary)
    pub scan_results_path: String,
    /// Path to wrecks.db for Swayze matching
    pub wrecks_db_path: String,
    /// Output KMZ file path
    pub output_path: String,
    /// Search radius in meters for Swayze matching (default 1000)
    #[serde(default = "default_search_radius")]
    pub search_radius_m: f64,
}

#[allow(dead_code)]
fn default_search_radius() -> f64 {
    1000.0
}

// ── Core logic ──────────────────────────────────────────────────────────────

/// Haversine distance in meters between two (lat, lon) pairs.
fn haversine_m(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let r = 6_371_000.0; // Earth radius in metres
    let d_lat = (lat2 - lat1).to_radians();
    let d_lon = (lon2 - lon1).to_radians();
    let a = (d_lat / 2.0).sin().powi(2)
        + lat1.to_radians().cos() * lat2.to_radians().cos() * (d_lon / 2.0).sin().powi(2);
    let c = 2.0 * a.sqrt().atan2((1.0 - a).sqrt());
    r * c
}

/// Query wrecks.db for known wrecks near a candidate and score them.
fn match_swayze_wrecks(
    conn: &Connection,
    candidate: &WreckCandidate,
    search_radius_m: f64,
) -> Vec<SwayzeMatch> {
    // Rough degree offset for the search box (~1° lat ≈ 111 km)
    let lat_offset = search_radius_m / 111_000.0;
    let lon_offset = search_radius_m / (111_000.0 * candidate.latitude.to_radians().cos());

    let min_lat = candidate.latitude - lat_offset;
    let max_lat = candidate.latitude + lat_offset;
    let min_lon = candidate.longitude - lon_offset;
    let max_lon = candidate.longitude + lon_offset;

    let mut stmt = conn
        .prepare(
            "SELECT id, name, date, latitude, longitude, depth, feature_type, hull_material \
             FROM features \
             WHERE latitude BETWEEN ?1 AND ?2 AND longitude BETWEEN ?3 AND ?4",
        )
        .unwrap_or_else(|_| {
            // Fallback if column names differ
            conn.prepare(
                "SELECT rowid, name, '', latitude, longitude, 0.0, '', '' \
                 FROM features \
                 WHERE latitude BETWEEN ?1 AND ?2 AND longitude BETWEEN ?3 AND ?4",
            )
            .expect("Failed to query wrecks database")
        });

    let rows = stmt
        .query_map(
            rusqlite::params![min_lat, max_lat, min_lon, max_lon],
            |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1).unwrap_or_default(),
                    row.get::<_, Option<String>>(2).unwrap_or(None),
                    row.get::<_, f64>(3)?,
                    row.get::<_, f64>(4)?,
                    row.get::<_, Option<f64>>(5).unwrap_or(None),
                    row.get::<_, Option<String>>(6).unwrap_or(None),
                    row.get::<_, Option<String>>(7).unwrap_or(None),
                ))
            },
        )
        .ok();

    let mut matches: Vec<SwayzeMatch> = Vec::new();

    if let Some(rows) = rows {
        for row in rows.flatten() {
            let (id, name, date, lat, lon, depth, feature_type, hull_material) = row;
            let distance = haversine_m(candidate.latitude, candidate.longitude, lat, lon);

            if distance > search_radius_m {
                continue; // outside radius after precise check
            }

            // Confidence scoring:
            //   - Distance component (0..0.5): closer = higher
            //   - Size component (0..0.3): similar area = higher
            //   - Depth component (0..0.2): matching depth = higher
            let dist_score = 0.5 * (1.0 - (distance / search_radius_m).min(1.0));

            let size_score = if let Some(d) = depth {
                // Use depth as rough proxy for "expected wreck footprint" if no dimensions
                let depth_area_proxy = d.abs() * 30.0; // rough heuristic
                let ratio = if depth_area_proxy > 0.0 {
                    (candidate.size_sq_meters / depth_area_proxy).min(5.0)
                } else {
                    1.0
                };
                0.3 * (1.0 - (ratio - 1.0).abs().min(1.0))
            } else {
                0.15 // neutral if no depth info
            };

            let depth_score = if let (Some(d), Some(ref elev)) =
                (depth, &candidate.elevation_stats)
            {
                if let Some(&mean_depth) = elev.get("mean") {
                    let diff = (d.abs() - mean_depth.abs()).abs();
                    0.2 * (1.0 - (diff / 20.0).min(1.0))
                } else {
                    0.1
                }
            } else {
                0.1
            };

            let confidence = dist_score + size_score + depth_score;

            matches.push(SwayzeMatch {
                wreck_id: id,
                name,
                date,
                latitude: lat,
                longitude: lon,
                depth,
                feature_type,
                hull_material,
                distance_meters: distance,
                confidence_score: confidence,
            });
        }
    }

    // Sort by confidence descending, take top 5
    matches.sort_by(|a, b| b.confidence_score.partial_cmp(&a.confidence_score).unwrap());
    matches.truncate(5);
    matches
}

/// Escape XML special characters.
fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

fn normalise_bbox(
    min_lon: f64,
    min_lat: f64,
    max_lon: f64,
    max_lat: f64,
) -> Option<(f64, f64, f64, f64)> {
    if !min_lon.is_finite() || !min_lat.is_finite() || !max_lon.is_finite() || !max_lat.is_finite() {
        return None;
    }

    let mut lo_lon = min_lon.min(max_lon).clamp(-180.0, 180.0);
    let mut hi_lon = min_lon.max(max_lon).clamp(-180.0, 180.0);
    let mut lo_lat = min_lat.min(max_lat).clamp(-90.0, 90.0);
    let mut hi_lat = min_lat.max(max_lat).clamp(-90.0, 90.0);

    // Drop near-zero extents that render as slivers/artifacts in some KML viewers.
    if (hi_lon - lo_lon).abs() < 1e-9 || (hi_lat - lo_lat).abs() < 1e-9 {
        return None;
    }

    // Ensure strict ordering after clamp.
    if lo_lon > hi_lon {
        std::mem::swap(&mut lo_lon, &mut hi_lon);
    }
    if lo_lat > hi_lat {
        std::mem::swap(&mut lo_lat, &mut hi_lat);
    }

    Some((lo_lon, lo_lat, hi_lon, hi_lat))
}

/// Build the full KML XML string with polygon overlays and Swayze matches.
fn build_kml(
    results: &[ScanResults],
    conn: &Connection,
    search_radius_m: f64,
) -> String {
    let mut kml = String::with_capacity(64 * 1024);

    kml.push_str(r#"<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"
     xmlns:gx="http://www.google.com/kml/ext/2.2">
<Document>
  <name>BAG Masking Analysis — Polygon Overlays</name>
  <description>Redacted area polygons with Swayze wreck cross-reference.
Source: BAG depth grid analysis (not PDF). Generated in Rust for speed.</description>
  <open>1</open>

  <!-- ── Shared Styles ────────────────────────────────────────── -->
  <Style id="highConfPoly">
    <LineStyle><color>ff0000ff</color><width>2</width></LineStyle>
    <PolyStyle><color>550000ff</color></PolyStyle>
  </Style>
  <Style id="medConfPoly">
    <LineStyle><color>ff0088ff</color><width>2</width></LineStyle>
    <PolyStyle><color>550088ff</color></PolyStyle>
  </Style>
  <Style id="lowConfPoly">
    <LineStyle><color>ff00ccff</color><width>1</width></LineStyle>
    <PolyStyle><color>4400ccff</color></PolyStyle>
  </Style>
  <Style id="sigSmoothPoly">
    <LineStyle><color>ffff8800</color><width>1</width></LineStyle>
    <PolyStyle><color>44ff8800</color></PolyStyle>
  </Style>
  <Style id="sigRemovalPoly">
    <LineStyle><color>ffff0000</color><width>1</width></LineStyle>
    <PolyStyle><color>44ff0000</color></PolyStyle>
  </Style>
  <Style id="sigAlterPoly">
    <LineStyle><color>ff00ff00</color><width>1</width></LineStyle>
    <PolyStyle><color>4400ff00</color></PolyStyle>
  </Style>
  <Style id="sigPatternPoly">
    <LineStyle><color>ffff00ff</color><width>1</width></LineStyle>
    <PolyStyle><color>44ff00ff</color></PolyStyle>
  </Style>
  <Style id="swayzePin">
    <IconStyle>
      <Icon><href>http://maps.google.com/mapfiles/kml/shapes/shipwreck.png</href></Icon>
      <scale>1.0</scale>
    </IconStyle>
    <LabelStyle><scale>0.7</scale></LabelStyle>
  </Style>

"#);

    for scan in results {
        let bag_name = xml_escape(&scan.file);

        // ── Wreck Candidates folder ──
        kml.push_str(&format!(
            "  <Folder>\n    <name>Candidates — {}</name>\n    <open>1</open>\n",
            bag_name
        ));

        for (i, cand) in scan.candidates.iter().enumerate() {
            let style = if cand.confidence > 0.8 {
                "#highConfPoly"
            } else if cand.confidence > 0.5 {
                "#medConfPoly"
            } else {
                "#lowConfPoly"
            };

            let (raw_min_lon, raw_min_lat, raw_max_lon, raw_max_lat) = cand.bounding_box;
            let Some((min_lon, min_lat, max_lon, max_lat)) = normalise_bbox(
                raw_min_lon,
                raw_min_lat,
                raw_max_lon,
                raw_max_lat,
            ) else {
                continue;
            };

            // Swayze matching
            let matches = match_swayze_wrecks(conn, cand, search_radius_m);

            // Build tooltip HTML
            let mut desc = String::new();
            desc.push_str("<![CDATA[<div style='font-family:Arial;max-width:420px;'>");
            desc.push_str(&format!(
                "<h3>Wreck Candidate #{}</h3><table style='border-collapse:collapse;width:100%;'>",
                i + 1
            ));
            desc.push_str(&format!(
                "<tr><td><b>BAG Source:</b></td><td>{}</td></tr>",
                bag_name
            ));
            desc.push_str(&format!(
                "<tr><td><b>Position:</b></td><td>{:.6}°N, {:.6}°W</td></tr>",
                cand.latitude,
                cand.longitude.abs()
            ));
            desc.push_str(&format!(
                "<tr><td><b>Confidence:</b></td><td>{:.1}%</td></tr>",
                cand.confidence * 100.0
            ));
            desc.push_str(&format!(
                "<tr><td><b>Size:</b></td><td>{:.1} m² ({:.1} ft²)</td></tr>",
                cand.size_sq_meters, cand.size_sq_feet
            ));
            desc.push_str(&format!(
                "<tr><td><b>Dimensions:</b></td><td>{:.1}m × {:.1}m ({:.0}ft × {:.0}ft)</td></tr>",
                cand.width_meters, cand.height_meters, cand.width_feet, cand.height_feet
            ));

            if let Some(ref elev) = cand.elevation_stats {
                if let Some(mean) = elev.get("mean") {
                    desc.push_str(&format!(
                        "<tr><td><b>Depth (mean):</b></td><td>{:.2} m</td></tr>",
                        mean
                    ));
                }
                if let Some(range) = elev.get("range") {
                    desc.push_str(&format!(
                        "<tr><td><b>Depth range:</b></td><td>{:.2} m</td></tr>",
                        range
                    ));
                }
            }

            desc.push_str(&format!(
                "<tr><td><b>Anomaly Score:</b></td><td>{:.3}</td></tr>",
                cand.anomaly_score
            ));

            // Redaction signatures attached to this candidate
            if let Some(ref sigs) = cand.redaction_signatures {
                if !sigs.is_empty() {
                    desc.push_str(&format!(
                        "<tr><td colspan='2'><b>Redaction Signatures: {}</b></td></tr>",
                        sigs.len()
                    ));
                    for sig in sigs.iter().take(4) {
                        desc.push_str(&format!(
                            "<tr><td>  {} ({:.0}%)</td><td>{}</td></tr>",
                            sig.signature_type,
                            sig.confidence * 100.0,
                            sig.technique_used.as_deref().unwrap_or("unknown")
                        ));
                    }
                }
            }

            // Swayze matches
            if !matches.is_empty() {
                desc.push_str("<tr><td colspan='2'><hr/><b>Known Wreck Matches (Swayze DB):</b></td></tr>");
                for m in &matches {
                    desc.push_str(&format!(
                        "<tr><td><b>{}:</b></td><td>{:.0}% — {:.0}m away</td></tr>",
                        xml_escape(&m.name),
                        m.confidence_score * 100.0,
                        m.distance_meters
                    ));
                    if let Some(ref date) = m.date {
                        desc.push_str(&format!(
                            "<tr><td>  Date:</td><td>{}</td></tr>",
                            xml_escape(date)
                        ));
                    }
                    if let Some(d) = m.depth {
                        desc.push_str(&format!(
                            "<tr><td>  Depth:</td><td>{:.1} m</td></tr>",
                            d
                        ));
                    }
                    if let Some(ref hull) = m.hull_material {
                        if !hull.is_empty() {
                            desc.push_str(&format!(
                                "<tr><td>  Hull:</td><td>{}</td></tr>",
                                xml_escape(hull)
                            ));
                        }
                    }
                }
            }

            desc.push_str("</table></div>]]>");

            // Polygon from bounding box
            kml.push_str(&format!(
                r#"    <Placemark>
      <name>Candidate #{idx} ({conf:.0}%)</name>
      <description>{desc}</description>
      <styleUrl>{style}</styleUrl>
      <Polygon>
        <tessellate>1</tessellate>
        <outerBoundaryIs><LinearRing><coordinates>
          {min_lon},{min_lat},0
          {max_lon},{min_lat},0
          {max_lon},{max_lat},0
          {min_lon},{max_lat},0
          {min_lon},{min_lat},0
        </coordinates></LinearRing></outerBoundaryIs>
      </Polygon>
    </Placemark>
"#,
                idx = i + 1,
                conf = cand.confidence * 100.0,
                desc = desc,
                style = style,
                min_lon = min_lon,
                min_lat = min_lat,
                max_lon = max_lon,
                max_lat = max_lat
            ));

            // Add Swayze match pins inside the candidate folder
            for m in &matches {
                kml.push_str(&format!(
                    r#"    <Placemark>
      <name>Swayze: {} ({:.0}%)</name>
      <description><![CDATA[Known wreck from Swayze DB.<br/>Distance: {:.0}m from candidate.]]></description>
      <styleUrl>#swayzePin</styleUrl>
      <Point><coordinates>{},{},0</coordinates></Point>
    </Placemark>
"#,
                    xml_escape(&m.name),
                    m.confidence_score * 100.0,
                    m.distance_meters,
                    m.longitude,
                    m.latitude,
                ));
            }
        }

        kml.push_str("  </Folder>\n");

        // ── Redaction Signatures folder ──
        if !scan.redaction_signatures.is_empty() {
            kml.push_str(&format!(
                "  <Folder>\n    <name>Redaction Signatures — {}</name>\n",
                bag_name
            ));

            for (i, sig) in scan.redaction_signatures.iter().enumerate() {
                let style = match sig.signature_type.as_str() {
                    "smoothing" => "#sigSmoothPoly",
                    "removal" => "#sigRemovalPoly",
                    "alteration" => "#sigAlterPoly",
                    "pattern" => "#sigPatternPoly",
                    _ => "#sigSmoothPoly",
                };

                let (raw_min_lon, raw_min_lat, raw_max_lon, raw_max_lat) = sig.bounding_box;
                let Some((min_lon, min_lat, max_lon, max_lat)) = normalise_bbox(
                    raw_min_lon,
                    raw_min_lat,
                    raw_max_lon,
                    raw_max_lat,
                ) else {
                    continue;
                };

                let mut desc = String::new();
                desc.push_str("<![CDATA[<div style='font-family:Arial;'>");
                desc.push_str(&format!(
                    "<h3>Redaction Signature #{}</h3><table>",
                    i + 1
                ));
                desc.push_str(&format!(
                    "<tr><td><b>BAG Source:</b></td><td>{}</td></tr>",
                    bag_name
                ));
                desc.push_str(&format!(
                    "<tr><td><b>Type:</b></td><td>{}</td></tr>",
                    sig.signature_type
                ));
                desc.push_str(&format!(
                    "<tr><td><b>Confidence:</b></td><td>{:.1}%</td></tr>",
                    sig.confidence * 100.0
                ));
                desc.push_str(&format!(
                    "<tr><td><b>Technique:</b></td><td>{}</td></tr>",
                    sig.technique_used.as_deref().unwrap_or("unknown")
                ));
                desc.push_str(&format!(
                    "<tr><td><b>Size:</b></td><td>{:.1} m² ({} px)</td></tr>",
                    sig.size_meters_sq, sig.size_pixels
                ));
                desc.push_str(&format!(
                    "<tr><td><b>Position:</b></td><td>{:.6}°, {:.6}°</td></tr>",
                    sig.location.0, sig.location.1
                ));

                if let Some(ref redactor) = sig.redactor_id {
                    desc.push_str(&format!(
                        "<tr><td><b>Redactor ID:</b></td><td>{}</td></tr>",
                        xml_escape(redactor)
                    ));
                }

                if let Some(ref ev) = sig.evidence {
                    let mut count = 0;
                    for (k, v) in ev.iter() {
                        if count >= 5 {
                            break;
                        }
                        desc.push_str(&format!(
                            "<tr><td>{}</td><td>{}</td></tr>",
                            xml_escape(k),
                            v
                        ));
                        count += 1;
                    }
                }

                desc.push_str("</table></div>]]>");

                kml.push_str(&format!(
                    r#"    <Placemark>
      <name>Sig #{idx} — {sig_type}</name>
      <description>{desc}</description>
      <styleUrl>{style}</styleUrl>
      <Polygon>
        <tessellate>1</tessellate>
        <outerBoundaryIs><LinearRing><coordinates>
          {min_lon},{min_lat},0
          {max_lon},{min_lat},0
          {max_lon},{max_lat},0
          {min_lon},{max_lat},0
          {min_lon},{min_lat},0
        </coordinates></LinearRing></outerBoundaryIs>
      </Polygon>
    </Placemark>
"#,
                    idx = i + 1,
                    sig_type = sig.signature_type,
                    desc = desc,
                    style = style,
                    min_lon = min_lon,
                    min_lat = min_lat,
                    max_lon = max_lon,
                    max_lat = max_lat
                ));
            }

            kml.push_str("  </Folder>\n");
        }
    }

    kml.push_str("</Document>\n</kml>\n");
    kml
}

// ── Public API ──────────────────────────────────────────────────────────────

/// Generate a KMZ file from scan results JSON + wrecks.db.
/// Returns the output path on success.
pub fn generate_kmz(
    scan_results_path: &str,
    wrecks_db_path: &str,
    output_path: &str,
    search_radius_m: f64,
) -> Result<String, String> {
    // Read and parse scan results
    let json_text = fs::read_to_string(scan_results_path)
        .map_err(|e| format!("Failed to read scan results: {e}"))?;

    // Try parsing as single scan result first, then as batch summary
    let results: Vec<ScanResults> = if let Ok(single) =
        serde_json::from_str::<ScanResults>(&json_text)
    {
        vec![single]
    } else if let Ok(batch) = serde_json::from_str::<BatchSummary>(&json_text) {
        batch
            .results
            .into_iter()
            .filter(|r| !r.candidates.is_empty() || !r.redaction_signatures.is_empty())
            .collect()
    } else {
        return Err("Could not parse scan results JSON (expected single scan or batch summary)".into());
    };

    if results.is_empty() {
        return Err("No scan results with candidates or signatures found".into());
    }

    // Open wrecks database
    let conn = Connection::open(wrecks_db_path)
        .map_err(|e| format!("Failed to open wrecks database: {e}"))?;

    // Build KML
    let kml = build_kml(&results, &conn, search_radius_m);

    // Write KMZ
    let out = fs::File::create(output_path)
        .map_err(|e| format!("Failed to create output file: {e}"))?;
    let mut zip = ZipWriter::new(out);
    let options = FileOptions::default().compression_method(CompressionMethod::Deflated);
    zip.start_file("doc.kml", options)
        .map_err(|e| format!("Failed to start KML entry in KMZ: {e}"))?;
    zip.write_all(kml.as_bytes())
        .map_err(|e| format!("Failed to write KML data: {e}"))?;
    zip.finish()
        .map_err(|e| format!("Failed to finalise KMZ: {e}"))?;

    Ok(output_path.to_string())
}

/// Also support writing plain KML (uncompressed) for debugging.
pub fn generate_kml(
    scan_results_path: &str,
    wrecks_db_path: &str,
    output_path: &str,
    search_radius_m: f64,
) -> Result<String, String> {
    let json_text = fs::read_to_string(scan_results_path)
        .map_err(|e| format!("Failed to read scan results: {e}"))?;

    let results: Vec<ScanResults> = if let Ok(single) =
        serde_json::from_str::<ScanResults>(&json_text)
    {
        vec![single]
    } else if let Ok(batch) = serde_json::from_str::<BatchSummary>(&json_text) {
        batch
            .results
            .into_iter()
            .filter(|r| !r.candidates.is_empty() || !r.redaction_signatures.is_empty())
            .collect()
    } else {
        return Err("Could not parse scan results JSON".into());
    };

    let conn = Connection::open(wrecks_db_path)
        .map_err(|e| format!("Failed to open wrecks database: {e}"))?;

    let kml = build_kml(&results, &conn, search_radius_m);

    fs::write(output_path, &kml)
        .map_err(|e| format!("Failed to write KML: {e}"))?;

    Ok(output_path.to_string())
}

// Batch summary wrapper
#[derive(Debug, Deserialize)]
struct BatchSummary {
    #[serde(default)]
    results: Vec<ScanResults>,
}
