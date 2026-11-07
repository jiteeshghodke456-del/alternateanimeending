/**
 * A tiny message bus between the guided tour and the pages it drives. The tour never clicks pixels:
 * it calls the API client, navigates, and hands pages what they need through these mailboxes.
 *
 * - PDF import: the tour fetches the demo guideline PDF and asks the Brand Kit to open the import
 *   dialog with it (a mailbox, so it works even if the page mounts after the request).
 * - Import progress: the import dialog reports its phase, so the tour can tell when proposals are shown
 *   (whether the judge uploaded the PDF or the tour did).
 */

const PDF_EVENT = "bg:tour-pdf";
const IMPORT_EVENT = "bg:tour-import-phase";

let pendingPdf: File | null = null;

export function requestPdfImport(file: File): void {
  pendingPdf = file;
  try { window.dispatchEvent(new Event(PDF_EVENT)); } catch { /* no window */ }
}

/** Take the waiting PDF (if any). Returns null when nothing is waiting. */
export function takePdfImport(): File | null {
  const f = pendingPdf;
  pendingPdf = null;
  return f;
}

export function onPdfImportRequest(cb: () => void): () => void {
  window.addEventListener(PDF_EVENT, cb);
  return () => window.removeEventListener(PDF_EVENT, cb);
}

export interface ImportPhase { phase: "pick" | "working" | "review" | "done" | "error" | "closed"; proposals?: number; verified?: number; cost?: number | null }
let lastImport: ImportPhase = { phase: "closed" };

export function reportImportPhase(p: ImportPhase): void {
  lastImport = p;
  try { window.dispatchEvent(new Event(IMPORT_EVENT)); } catch { /* no window */ }
}

export function importPhase(): ImportPhase {
  return lastImport;
}

/** True while the guided tour runs: the import dialog then opens non-modal, so the tour card stays
 *  reachable (a modal dialog hides everything outside it from screen readers and traps focus). */
let tourOn = false;
export function setTourActive(on: boolean): void { tourOn = on; }
export function tourActive(): boolean { return tourOn; }
