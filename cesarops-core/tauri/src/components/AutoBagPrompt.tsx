import { useState, useEffect } from "react";
import { startAutoBagPipeline, fetchNasaHls } from "../services/api";

export default function AutoBagPrompt() {
  const [isOpen, setIsOpen] = useState(false);
  const [throttleMode, setThrottleMode] = useState("unfettered");
  const [customKbps, setCustomKbps] = useState(5000);
  const [scanMode, setScanMode] = useState("masked");
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    // Show prompt when WreckHunter 2000 opens
    const hasPrompted = sessionStorage.getItem("autobag_prompted");
    if (!hasPrompted) {
      setIsOpen(true);
      sessionStorage.setItem("autobag_prompted", "true");
    }
  }, []);

  const handleStart = async () => {
    try {
      setStatus("Starting...");
      await startAutoBagPipeline(throttleMode, customKbps, scanMode);
      setIsOpen(false);
    } catch (err) {
      setStatus("Error starting pipeline: " + String(err));
    }
  };

  const handleFetchNasaHls = async () => {
    try {
      setStatus("Fetching NASA HLS data...");
      const bbox: [number, number, number, number] = [-84.0, 41.0, -83.0, 42.0]; // Example bounding box
      const bands = ["green", "red", "nir"];
      const sceneId = "example_scene_id";
      const result = await fetchNasaHls(sceneId, bands, bbox);
      console.log("NASA HLS fetch result:", result);
      setStatus("NASA HLS data fetched successfully.");
    } catch (err) {
      setStatus("Error fetching NASA HLS data: " + String(err));
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      backgroundColor: 'rgba(0,0,0,0.7)', zIndex: 9999,
      display: 'flex', alignItems: 'center', justifyContent: 'center'
    }}>
      <div className="modal-content" style={{
        background: '#1a1a2e', padding: '20px', borderRadius: '8px', 
        border: '1px solid #00ffff', width: '400px', color: 'white'
      }}>
        <h2>📡 NOAA BAG Auto Downloader</h2>
        <p>Would you like to initiate the autonomous BAG scraper in the background?</p>
        
        <div style={{ marginTop: '15px' }}>
          <label style={{ display: 'block', marginBottom: '8px' }}>
            <input 
              type="radio" 
              name="throttle" 
              value="unfettered" 
              checked={throttleMode === "unfettered"} 
              onChange={() => setThrottleMode("unfettered")} 
            />
            Unfettered Speed
          </label>
          <label style={{ display: 'block', marginBottom: '8px' }}>
            <input 
              type="radio" 
              name="throttle" 
              value="half" 
              checked={throttleMode === "half"} 
              onChange={() => setThrottleMode("half")} 
            />
            Half Bandwidth (Auto-calculated)
          </label>
          <label style={{ display: 'block', marginBottom: '8px' }}>
            <input 
              type="radio" 
              name="throttle" 
              value="custom" 
              checked={throttleMode === "custom"} 
              onChange={() => setThrottleMode("custom")} 
            />
            Custom Limit
          </label>
          
          {throttleMode === "custom" && (
            <div style={{ marginLeft: '25px', marginTop: '5px' }}>
              <input 
                type="number" 
                value={customKbps} 
                onChange={(e) => setCustomKbps(Number(e.target.value))}
                style={{ width: '80px', background: '#000', color: '#fff', border: '1px solid #444' }}
              /> <span style={{fontSize: '0.9em'}}>KB/s</span>
            </div>
          )}
        </div>

        {status && <div style={{ color: 'red', marginTop: '10px' }}>{status}</div>}

        <div style={{ marginTop: '20px', display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
          <button 
            onClick={() => setIsOpen(false)}
            style={{ background: '#333', color: 'white', border: 'none', padding: '8px 16px', cursor: 'pointer' }}
          >Skip</button>
          <button 
            onClick={handleStart}
            style={{ background: '#00ffff', color: '#000', border: 'none', padding: '8px 16px', fontWeight: 'bold', cursor: 'pointer' }}
          >Start Scraper</button>
          <button 
            onClick={handleFetchNasaHls}
            style={{ background: '#00ff00', color: '#000', border: 'none', padding: '8px 16px', fontWeight: 'bold', cursor: 'pointer' }}
          >Fetch NASA HLS</button>
        </div>
      </div>
    </div>
  );
}
