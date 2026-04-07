import { useRef, useEffect, useCallback, useState } from "react";
import type { Wreck } from "../types";
import { bboxQuery } from "../services/api";

// Great Lakes approximate bounding box
const GL_CENTER = { lat: 44.5, lon: -83.5 };
const GL_SPAN = { lat: 8, lon: 10 };

interface Props {
  onSelect: (wreck: Wreck) => void;
}

interface MapState {
  centerLat: number;
  centerLon: number;
  zoom: number;
}

export default function MapPanel({ onSelect }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [wrecks, setWrecks] = useState<Wreck[]>([]);
  const [mapState, setMapState] = useState<MapState>({
    centerLat: GL_CENTER.lat,
    centerLon: GL_CENTER.lon,
    zoom: 1,
  });
  const [hovered, setHovered] = useState<Wreck | null>(null);

  // Load wrecks in view
  useEffect(() => {
    const halfLat = GL_SPAN.lat / mapState.zoom / 2;
    const halfLon = GL_SPAN.lon / mapState.zoom / 2;
    bboxQuery(
      mapState.centerLat - halfLat,
      mapState.centerLat + halfLat,
      mapState.centerLon - halfLon,
      mapState.centerLon + halfLon,
      2000,
    ).then(r => setWrecks(r.results));
  }, [mapState]);

  // Convert lat/lon to canvas pixel
  const toPixel = useCallback(
    (lat: number, lon: number, w: number, h: number) => {
      const halfLat = GL_SPAN.lat / mapState.zoom / 2;
      const halfLon = GL_SPAN.lon / mapState.zoom / 2;
      const x = ((lon - (mapState.centerLon - halfLon)) / (halfLon * 2)) * w;
      const y = (1 - (lat - (mapState.centerLat - halfLat)) / (halfLat * 2)) * h;
      return { x, y };
    },
    [mapState],
  );

  // Draw the map
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d")!;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    const W = rect.width;
    const H = rect.height;

    // Background
    ctx.fillStyle = "#0a1628";
    ctx.fillRect(0, 0, W, H);

    // Grid lines
    ctx.strokeStyle = "#1a2744";
    ctx.lineWidth = 0.5;
    const halfLat = GL_SPAN.lat / mapState.zoom / 2;
    const halfLon = GL_SPAN.lon / mapState.zoom / 2;
    const minLat = mapState.centerLat - halfLat;
    const minLon = mapState.centerLon - halfLon;
    for (let lat = Math.ceil(minLat); lat <= mapState.centerLat + halfLat; lat++) {
      const p = toPixel(lat, minLon, W, H);
      ctx.beginPath();
      ctx.moveTo(0, p.y);
      ctx.lineTo(W, p.y);
      ctx.stroke();
      ctx.fillStyle = "#2a3a5a";
      ctx.font = "10px sans-serif";
      ctx.fillText(`${lat}N`, 4, p.y - 3);
    }
    for (let lon = Math.ceil(minLon); lon <= mapState.centerLon + halfLon; lon++) {
      const p = toPixel(minLat, lon, W, H);
      ctx.beginPath();
      ctx.moveTo(p.x, 0);
      ctx.lineTo(p.x, H);
      ctx.stroke();
      ctx.fillStyle = "#2a3a5a";
      ctx.fillText(`${Math.abs(lon)}W`, p.x + 3, H - 4);
    }

    // Color by magnetic potential
    const color = (w: Wreck) => {
      if (w.magnetic_potential === "strong") return "#3fb950";
      if (w.magnetic_potential === "moderate") return "#d29922";
      if (w.is_steel_freighter) return "#58a6ff";
      return "#555d6e";
    };

    // Draw wrecks
    for (const w of wrecks) {
      if (w.latitude == null || w.longitude == null) continue;
      const p = toPixel(w.latitude, w.longitude, W, H);
      if (p.x < -10 || p.x > W + 10 || p.y < -10 || p.y > H + 10) continue;

      const r = w.magnetic_potential === "strong" ? 5 : w.magnetic_potential === "moderate" ? 4 : 3;
      const c = color(w);

      // Halo for magnetic wrecks
      if (w.mag_mean != null && Math.abs(w.mag_mean) > 50) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, r + 6, 0, Math.PI * 2);
        ctx.fillStyle = c.replace(")", ",0.15)").replace("rgb", "rgba").replace("#", "");
        // Use hex alpha
        ctx.fillStyle = c + "22";
        ctx.fill();
      }

      ctx.beginPath();
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fillStyle = c;
      ctx.fill();

      // Dashed ring for estimated positions
      const cq = w.coord_quality;
      if (cq && cq !== "dive_verified") {
        ctx.beginPath();
        ctx.arc(p.x, p.y, r + 3, 0, Math.PI * 2);
        ctx.setLineDash([2, 2]);
        ctx.strokeStyle = cq === "lake_center" ? "#ef4444" : "#f59e0b";
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    // Draw hovered tooltip
    if (hovered && hovered.latitude && hovered.longitude) {
      const p = toPixel(hovered.latitude, hovered.longitude, W, H);
      ctx.fillStyle = "rgba(13,17,23,0.92)";
      ctx.strokeStyle = "#30363d";
      const nameLine = `${hovered.name || "Unknown"} (${hovered.date || "?"})`;
      const cqLabel = hovered.coord_quality === "dive_verified" ? "✓ verified"
        : hovered.coord_quality === "lake_center" ? "⚠ est. (lake center)"
        : hovered.coord_quality === "swayze_parsed" ? "⚠ est. (Swayze)"
        : hovered.coord_quality === "place_estimated" ? "⚠ est. (place)"
        : "pos. unknown";
      const text = `${nameLine}  [${cqLabel}]`;
      ctx.font = "12px sans-serif";
      const tw = ctx.measureText(text).width;
      const bx = p.x + 10;
      const by = p.y - 28;
      ctx.fillRect(bx - 4, by - 14, tw + 12, 22);
      ctx.strokeRect(bx - 4, by - 14, tw + 12, 22);
      ctx.fillStyle = "#e6edf3";
      ctx.fillText(text, bx + 2, by);
    }
  }, [wrecks, mapState, toPixel, hovered]);

  // Click / hover handlers
  const findNearest = useCallback(
    (ex: number, ey: number) => {
      const canvas = canvasRef.current;
      if (!canvas) return null;
      const rect = canvas.getBoundingClientRect();
      const W = rect.width;
      const H = rect.height;
      let best: Wreck | null = null;
      let bestDist = 20; // max 20px
      for (const w of wrecks) {
        if (!w.latitude || !w.longitude) continue;
        const p = toPixel(w.latitude, w.longitude, W, H);
        const d = Math.hypot(p.x - ex, p.y - ey);
        if (d < bestDist) {
          bestDist = d;
          best = w;
        }
      }
      return best;
    },
    [wrecks, toPixel],
  );

  const handleClick = (e: React.MouseEvent) => {
    const rect = (e.target as HTMLCanvasElement).getBoundingClientRect();
    const w = findNearest(e.clientX - rect.left, e.clientY - rect.top);
    if (w) onSelect(w);
  };

  const handleMove = (e: React.MouseEvent) => {
    const rect = (e.target as HTMLCanvasElement).getBoundingClientRect();
    setHovered(findNearest(e.clientX - rect.left, e.clientY - rect.top));
  };

  const handleWheel = (e: React.WheelEvent) => {
    setMapState(s => ({
      ...s,
      zoom: Math.max(0.5, Math.min(20, s.zoom * (e.deltaY < 0 ? 1.2 : 0.83))),
    }));
  };

  return (
    <div className="map-container" style={{ position: "relative" }}>
      <canvas
        ref={canvasRef}
        style={{ width: "100%", height: "100%", cursor: hovered ? "pointer" : "crosshair" }}
        onClick={handleClick}
        onMouseMove={handleMove}
        onWheel={handleWheel}
      />
      <div className="map-legend">
        <div style={{ fontWeight: 600, marginBottom: 4 }}>Legend</div>
        <div className="legend-item"><div className="legend-dot" style={{ background: "#3fb950" }} /> Strong magnetic</div>
        <div className="legend-item"><div className="legend-dot" style={{ background: "#d29922" }} /> Moderate magnetic</div>
        <div className="legend-item"><div className="legend-dot" style={{ background: "#58a6ff" }} /> Steel freighter</div>
        <div className="legend-item"><div className="legend-dot" style={{ background: "#555d6e" }} /> Other</div>
        <div className="legend-item"><div className="legend-dot" style={{ background: "transparent", border: "1.5px dashed #f59e0b", boxSizing: "border-box" }} /> Estimated position</div>
        <div style={{ marginTop: 6, fontSize: 10, color: "var(--text-dim)" }}>
          {wrecks.length.toLocaleString()} wrecks in view &middot; scroll to zoom
        </div>
      </div>
    </div>
  );
}
