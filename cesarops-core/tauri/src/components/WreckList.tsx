import { useState, useEffect, useCallback } from "react";
import type { Wreck, WreckListResponse, MagPotential } from "../types";
import { listWrecks, searchWrecks } from "../services/api";

function MagBadge({ pot }: { pot: string | null }) {
  if (!pot) return null;
  const cls = pot === "strong" ? "badge-strong" : pot === "moderate" ? "badge-moderate" : "badge-weak";
  return <span className={`badge ${cls}`}>{pot}</span>;
}

function CoordBadge({ quality }: { quality: string | null }) {
  if (!quality) return <span className="badge badge-coord-none" title="No quality info">unknown</span>;
  const map: Record<string, { label: string; cls: string; title: string }> = {
    dive_verified:   { label: "✓ verified", cls: "badge-coord-verified", title: "Dive-verified position" },
    swayze_parsed:   { label: "est. (Swayze)", cls: "badge-coord-estimated", title: "Estimated — parsed from Swayze book" },
    place_estimated: { label: "est. (place)", cls: "badge-coord-estimated", title: "Estimated — derived from place name" },
    lake_center:     { label: "est. (lake)", cls: "badge-coord-warn", title: "Estimated — lake center placeholder" },
    none:            { label: "unknown", cls: "badge-coord-none", title: "No quality info" },
  };
  const info = map[quality] || { label: quality, cls: "badge-coord-none", title: quality };
  return <span className={`badge ${info.cls}`} title={info.title}>{info.label}</span>;
}

interface Props {
  searchQuery: string;
  onSelect: (wreck: Wreck) => void;
  initialSteelOnly?: boolean;
  initialMagFilter?: MagPotential | "";
}

export default function WreckList({ searchQuery, onSelect, initialSteelOnly, initialMagFilter }: Props) {
  const [data, setData] = useState<WreckListResponse | null>(null);
  const [page, setPage] = useState(1);
  const [magFilter, setMagFilter] = useState<MagPotential | "">(initialMagFilter ?? "");
  const [steelOnly, setSteelOnly] = useState(initialSteelOnly ?? false);
  const [coordsOnly, setCoordsOnly] = useState(false);

  const limit = 50;

  const load = useCallback(async () => {
    if (searchQuery.length >= 2) {
      const sr = await searchWrecks(searchQuery);
      setData({ total: sr.count, page: 1, limit: sr.count, pages: 1, results: sr.results });
    } else {
      const filters: Record<string, unknown> = {};
      if (magFilter) filters.magnetic_potential = magFilter;
      if (steelOnly) filters.is_steel = true;
      if (coordsOnly) filters.has_coords = true;

      const r = await listWrecks(page, limit, filters as any);
      setData(r);
    }
  }, [searchQuery, page, magFilter, steelOnly, coordsOnly]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(1); }, [searchQuery, magFilter, steelOnly, coordsOnly]);

  if (!data) return <div className="loading">Loading wrecks...</div>;

  return (
    <div>
      <div className="filters-bar">
        <label>
          Magnetic:
          <select value={magFilter} onChange={e => setMagFilter(e.target.value as MagPotential | "")}>
            <option value="">All</option>
            <option value="strong">Strong</option>
            <option value="moderate">Moderate</option>
            <option value="weak">Weak</option>
          </select>
        </label>
        <label>
          <input type="checkbox" checked={steelOnly} onChange={e => setSteelOnly(e.target.checked)} />
          Steel only
        </label>
        <label>
          <input type="checkbox" checked={coordsOnly} onChange={e => setCoordsOnly(e.target.checked)} />
          Has coordinates
        </label>

        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-dim)" }}>
          {data.total.toLocaleString()} wrecks
        </span>
      </div>

      <table className="wreck-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Date</th>
            <th>Lat</th>
            <th>Lon</th>
            <th>Depth</th>
            <th>Mag</th>
            <th>Position</th>
            <th>Type</th>
          </tr>
        </thead>
        <tbody>
          {data.results.map(w => (
            <tr key={w.id} onClick={() => onSelect(w)}>
              <td style={{ fontWeight: 500 }}>
                {w.name || "Unknown"}
                {w.is_steel_freighter ? <span className="badge badge-steel" style={{ marginLeft: 6 }}>steel</span> : null}
              </td>
              <td>{w.date || "—"}</td>
              <td style={{ fontSize: 11, fontFamily: "var(--mono)" }}>{w.latitude != null ? w.latitude.toFixed(4) : "—"}</td>
              <td style={{ fontSize: 11, fontFamily: "var(--mono)" }}>{w.longitude != null ? w.longitude.toFixed(4) : "—"}</td>
              <td>{w.depth != null ? `${w.depth}ft` : "—"}</td>
              <td><MagBadge pot={w.magnetic_potential} /></td>
              <td><CoordBadge quality={w.coord_quality} /></td>
              <td style={{ fontSize: 12, color: "var(--text-dim)" }}>{w.feature_type || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {data.pages > 1 && (
        <div className="pagination">
          <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Prev</button>
          <span>Page {data.page} / {data.pages}</span>
          <button disabled={page >= data.pages} onClick={() => setPage(p => p + 1)}>Next</button>
        </div>
      )}
    </div>
  );
}
