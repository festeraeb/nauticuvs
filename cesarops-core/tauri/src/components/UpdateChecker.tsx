import { useEffect, useState } from "react";

type UpdateState =
  | { status: "idle" }
  | { status: "available"; version: string; notes: string }
  | { status: "downloading"; percent: number }
  | { status: "ready" }
  | { status: "error"; message: string };

export default function UpdateChecker() {
  const [state, setState] = useState<UpdateState>({ status: "idle" });

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const { check } = await import("@tauri-apps/plugin-updater");
        const update = await check();
        if (!alive || !update?.available) return;
        setState({
          status: "available",
          version: update.version,
          notes: update.body ?? "",
        });
      } catch {
        // Silently ignore — no endpoint configured yet or running in dev mode
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  async function handleInstall() {
    setState({ status: "downloading", percent: 0 });
    try {
      const { check } = await import("@tauri-apps/plugin-updater");
      const { relaunch } = await import("@tauri-apps/plugin-process");
      const update = await check();
      if (!update?.available) return;

      let downloaded = 0;
      let total = 1;

      await update.downloadAndInstall((event) => {
        if (event.event === "Started") {
          total = event.data.contentLength ?? 1;
        } else if (event.event === "Progress") {
          downloaded += event.data.chunkLength;
          setState({
            status: "downloading",
            percent: Math.round((downloaded / total) * 100),
          });
        } else if (event.event === "Finished") {
          setState({ status: "ready" });
        }
      });

      await relaunch();
    } catch (e: unknown) {
      setState({ status: "error", message: String(e) });
    }
  }

  if (state.status === "idle") return null;

  return (
    <div className="update-banner">
      {state.status === "available" && (
        <>
          <span className="update-msg">
            ⬆ CESAROPS {state.version} available
            {state.notes && (
              <span className="update-notes"> — {state.notes}</span>
            )}
          </span>
          <button className="update-btn" onClick={handleInstall}>
            Install &amp; Restart
          </button>
          <button
            className="update-dismiss"
            onClick={() => setState({ status: "idle" })}
          >
            ✕
          </button>
        </>
      )}
      {state.status === "downloading" && (
        <>
          <span className="update-msg">
            Downloading… {state.percent}%
          </span>
          <div className="update-progress-track">
            <div
              className="update-progress-fill"
              style={{ width: `${state.percent}%` }}
            />
          </div>
        </>
      )}
      {state.status === "ready" && (
        <span className="update-msg">Update installed — restarting…</span>
      )}
      {state.status === "error" && (
        <>
          <span className="update-msg update-err">
            Update failed: {state.message}
          </span>
          <button
            className="update-dismiss"
            onClick={() => setState({ status: "idle" })}
          >
            ✕
          </button>
        </>
      )}
    </div>
  );
}
