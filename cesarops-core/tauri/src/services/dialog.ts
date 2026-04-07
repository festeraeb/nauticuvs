/**
 * File/folder picker abstraction.
 * Uses Tauri dialog plugin when running inside Tauri,
 * falls back to native HTML <input> pickers in the browser.
 *
 * Key design: ensureTauri() fires eagerly at module load so that
 * by the time the user clicks a Browse button, we already know
 * whether Tauri is available — no await in the click path, which
 * would break the browser's "user gesture" trust and silently
 * block input.click().
 */

let _tauriOpen: ((opts: Record<string, unknown>) => Promise<string | string[] | null>) | null = null;
let _tauriSave: ((opts: Record<string, unknown>) => Promise<string | null>) | null = null;
let _tauriReady = false;

// Fire immediately at module load — resolved before any user click
(async () => {
  try {
    const mod = await import("@tauri-apps/plugin-dialog");
    if (mod && typeof mod.open === "function") {
      _tauriOpen = mod.open;
      _tauriSave = mod.save ?? null;
    }
  } catch {
    // Not in Tauri or plugin not installed → browser fallbacks
  } finally {
    _tauriReady = true;
  }
})();


// ── Browser fallback helpers ────────────────────────────────────────────────

function browserPickFiles(accept: string, multiple: boolean): Promise<string[]> {
  return new Promise<string[]>((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = multiple;
    if (accept) input.accept = accept;
    input.style.display = "none";
    document.body.appendChild(input);

    const cleanup = () => { try { document.body.removeChild(input); } catch {} };

    input.addEventListener("change", () => {
      const paths: string[] = [];
      if (input.files) {
        for (let i = 0; i < input.files.length; i++) {
          const f = input.files[i] as File & { path?: string };
          paths.push(f.path || f.name);
        }
      }
      cleanup();
      resolve(paths);
    });

    // "cancel" event fires in modern browsers when user closes the picker
    input.addEventListener("cancel", () => { cleanup(); resolve([]); });
    // Safety timeout — older browsers don't fire "cancel"
    const tid = setTimeout(() => { cleanup(); resolve([]); }, 120_000);
    input.addEventListener("change", () => clearTimeout(tid));
    input.addEventListener("cancel", () => clearTimeout(tid));

    input.click();
  });
}

function browserPickFolder(): Promise<string[]> {
  return new Promise<string[]>((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    // webkitdirectory lets the user pick a folder in Chrome/Edge/Firefox
    (input as any).webkitdirectory = true;
    input.style.display = "none";
    document.body.appendChild(input);

    const cleanup = () => { try { document.body.removeChild(input); } catch {} };

    input.addEventListener("change", () => {
      const dirs = new Set<string>();
      if (input.files && input.files.length > 0) {
        const first = input.files[0] as File & { path?: string };
        if (first.path) {
          const sep = first.path.includes("\\") ? "\\" : "/";
          const parts = first.path.split(sep);
          parts.pop();
          dirs.add(parts.join(sep));
        } else if (first.webkitRelativePath) {
          dirs.add(first.webkitRelativePath.split("/")[0]);
        }
      }
      cleanup();
      resolve([...dirs]);
    });

    input.addEventListener("cancel", () => { cleanup(); resolve([]); });
    const tid = setTimeout(() => { cleanup(); resolve([]); }, 120_000);
    input.addEventListener("change", () => clearTimeout(tid));
    input.addEventListener("cancel", () => clearTimeout(tid));

    input.click();
  });
}


// ── Public API ──────────────────────────────────────────────────────────────

/** Pick one or more files. Returns array of paths/names. */
export function pickFiles(opts?: {
  multiple?: boolean;
  accept?: string;
  extensions?: string[];
  title?: string;
}): Promise<string[]> {
  // If Tauri plugin loaded, use it (already resolved — no await needed)
  if (_tauriReady && _tauriOpen) {
    return _tauriOpen({
      multiple: opts?.multiple ?? true,
      filters: opts?.extensions
        ? [{ name: "Files", extensions: opts.extensions }]
        : undefined,
      title: opts?.title,
    }).then(r => {
      if (!r) return [];
      return Array.isArray(r) ? r : [r];
    });
  }

  // Browser fallback — runs synchronously from user gesture
  const accept = opts?.accept || (opts?.extensions ? opts.extensions.map(e => `.${e}`).join(",") : "");
  return browserPickFiles(accept, opts?.multiple ?? true);
}

/** Pick a folder. Returns array with one directory path (or empty). */
export function pickFolder(opts?: {
  multiple?: boolean;
  title?: string;
}): Promise<string[]> {
  if (_tauriReady && _tauriOpen) {
    return _tauriOpen({
      directory: true,
      multiple: opts?.multiple ?? false,
      title: opts?.title,
    }).then(r => {
      if (!r) return [];
      return Array.isArray(r) ? r : [r];
    });
  }

  return browserPickFolder();
}

/** Save dialog. Falls back to prompt() in browser. */
export function pickSaveLocation(opts?: {
  title?: string;
  filters?: { name: string; extensions: string[] }[];
  defaultPath?: string;
}): Promise<string | null> {
  if (_tauriReady && _tauriSave) {
    return _tauriSave(opts ?? {});
  }

  const defaultName = opts?.defaultPath || "export.kmz";
  const name = prompt("Save file as:", defaultName);
  return Promise.resolve(name || null);
}

/** Extract paths from a browser DragEvent. */
export function getDroppedPaths(e: React.DragEvent): string[] {
  const items = e.dataTransfer.files;
  if (!items || items.length === 0) return [];
  const paths: string[] = [];
  for (let i = 0; i < items.length; i++) {
    const f = items[i] as File & { path?: string };
    paths.push(f.path || f.name);
  }
  return paths;
}
