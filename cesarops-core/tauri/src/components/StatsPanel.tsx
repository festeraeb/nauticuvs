import { useState, useEffect } from "react";
import type { StatsResponse } from "../types";
import { getStats } from "../services/api";

export default function StatsPanel() {
  const [stats, setStats] = useState<StatsResponse | null>(null);

  useEffect(() => { getStats().then(setStats); }, []);

  if (!stats) return <div className="loading">Loading stats...</div>;

  const cards: { label: string; value: number }[] = [
    { label: "Total Wrecks", value: stats.total_wrecks },
    { label: "With Coordinates", value: stats.with_coordinates },
    { label: "Steel Freighters", value: stats.steel_freighters },
    { label: "Iron Ore Carriers", value: stats.iron_ore_carriers },
    { label: "Strong Magnetic", value: stats.strong_mag_potential },
    { label: "Moderate Magnetic", value: stats.moderate_mag_potential },
    { label: "NAMAG Features", value: stats.with_namag_features },
    { label: "Hull Material Known", value: stats.with_hull_material },
  ];

  return (
    <div>
      <h2 style={{ marginBottom: 16 }}>Database Overview</h2>
      <div className="stats-grid">
        {cards.map(c => (
          <div className="stat-card" key={c.label}>
            <div className="stat-value">{c.value.toLocaleString()}</div>
            <div className="stat-label">{c.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
