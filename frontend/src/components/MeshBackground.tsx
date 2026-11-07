/** Three blurred, low-saturation blobs. Static under prefers-reduced-motion (see styles/index.css). */
export function MeshBackground() {
  return (
    <div className="mesh-bg" aria-hidden="true">
      <div className="blob blob-1" />
      <div className="blob blob-2" />
      <div className="blob blob-3" />
    </div>
  );
}
