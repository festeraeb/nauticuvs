import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";

interface TaskEntry {
  id: string;
  cmd: string;
  output: string;
  error: string;
  time: string;
  status: string;
}

export default function AgentPanel() {
  const [request, setRequest] = useState("");
  const [output, setOutput] = useState("");
  const [running, setRunning] = useState(false);
  const [tasks, setTasks] = useState<TaskEntry[]>([]);

  const addTask = (cmd: string, out: string, err: string, status: string) => {
    const entry: TaskEntry = {
      id: `task-${Date.now()}`,
      cmd,
      output: out,
      error: err,
      time: new Date().toLocaleTimeString(),
      status,
    };
    setTasks((prev) => [entry, ...prev]);
  };

  const handleRunRequest = async () => {
    if (!request.trim()) return;
    setRunning(true);
    setOutput("");
    try {
      const cwd = (window as any).__TAURI__?.path?.dirname || ".";
      const result: any = await invoke("ai_direct_request", {
        request,
        workDir: cwd,
      });
      setOutput(result.stdout || "");
      addTask(
        `ai_director.py --request "${request}" --execute`,
        result.stdout || "",
        result.stderr || "",
        result.status
      );
    } catch (e: any) {
      setOutput(`Error: ${e}`);
      addTask(
        `ai_director.py --request "${request}" --execute`,
        "",
        String(e),
        "error"
      );
    }
    setRunning(false);
  };

  const handleRunProbe = async () => {
    setRunning(true);
    setOutput("");
    try {
      const cwd = (window as any).__TAURI__?.path?.dirname || ".";
      const result: any = await invoke("run_background_probe", {
        workDir: cwd,
      });
      setOutput(result.stdout || "");
      addTask("background_probe.py --once", result.stdout || "", result.stderr || "", result.status);
    } catch (e: any) {
      setOutput(`Error: ${e}`);
      addTask("background_probe.py --once", "", String(e), "error");
    }
    setRunning(false);
  };

  const handleCheckNodes = async () => {
    setRunning(true);
    setOutput("");
    try {
      const cwd = (window as any).__TAURI__?.path?.dirname || ".";
      const result: any = await invoke("check_nodes", { workDir: cwd });
      setOutput(result.stdout || "");
      addTask("orchestrator --status", result.stdout || "", result.stderr || "", result.status);
    } catch (e: any) {
      setOutput(`Error: ${e}`);
      addTask("orchestrator --status", "", String(e), "error");
    }
    setRunning(false);
  };

  return (
    <div className="agent-panel" style={{ padding: 16, height: "100%", display: "flex", flexDirection: "column", gap: 12 }}>
      <h2 style={{ margin: 0 }}>🤖 AI Director</h2>
      <p style={{ color: "#888", fontSize: 13 }}>
        Ask Qwen to pick tools, set parameters, and run scans. Results are interpreted and returned.
      </p>

      {/* Request input */}
      <div style={{ display: "flex", gap: 8 }}>
        <input
          style={{ flex: 1, padding: "8px 12px", borderRadius: 6, border: "1px solid #333", background: "#1a1a2e", color: "#fff", fontSize: 14 }}
          placeholder='e.g. "Scan Straits of Mackinac east and west for anomalies, be aggressive"'
          value={request}
          onChange={(e) => setRequest(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleRunRequest()}
        />
        <button
          onClick={handleRunRequest}
          disabled={running || !request.trim()}
          style={{ padding: "8px 16px", borderRadius: 6, border: "none", background: "#4361ee", color: "#fff", cursor: "pointer", opacity: running ? 0.5 : 1 }}
        >
          {running ? "Running…" : "Run"}
        </button>
      </div>

      {/* Quick actions */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button onClick={handleRunProbe} disabled={running} style={{ padding: "6px 14px", borderRadius: 6, border: "1px solid #333", background: "#1a1a2e", color: "#fff", cursor: "pointer" }}>
          🔍 Run Probe (All Wrecks)
        </button>
        <button onClick={handleCheckNodes} disabled={running} style={{ padding: "6px 14px", borderRadius: 6, border: "1px solid #333", background: "#1a1a2e", color: "#fff", cursor: "pointer" }}>
          📡 Check Nodes
        </button>
      </div>

      {/* Output */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: 12,
          borderRadius: 6,
          background: "#0d1117",
          fontFamily: "Consolas, 'Courier New', monospace",
          fontSize: 12,
          whiteSpace: "pre-wrap",
          color: "#c9d1d9",
        }}
      >
        {output || (running ? "⏳ Running…" : "Output will appear here…")}
      </div>

      {/* Task log */}
      {tasks.length > 0 && (
        <div style={{ maxHeight: 200, overflow: "auto", borderRadius: 6, background: "#0d1117", padding: 8 }}>
          <div style={{ fontSize: 11, color: "#8b949e", marginBottom: 4 }}>Task Log</div>
          {tasks.map((t) => (
            <div key={t.id} style={{ fontSize: 11, borderBottom: "1px solid #21262d", padding: "4px 0" }}>
              <span style={{ color: "#58a6ff" }}>{t.time}</span>{" "}
              <span style={{ color: t.status === "error" ? "#f85149" : "#3fb950" }}>{t.status}</span>{" "}
              <span style={{ color: "#c9d1d9" }}>{t.cmd}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
