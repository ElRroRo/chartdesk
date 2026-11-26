/*
 * Chart Desk: drawings while the chart moves.
 *
 * At rest every drawing is SVG (pointer targets, hover, anchors). Rebuilding
 * that SVG on each frame of a pan or zoom is what made charts with many
 * drawings stutter (measured: 30 drawings took a 10-indicator chart from 19 to
 * 39 ms at the 95th percentile; for drawings behind a series that SVG is
 * even transparent, the chart paints them). While the chart moves, the drawings that are
 * neither selected nor hovered are painted on one canvas instead, from the
 * same scenes (lightweight-charts-drawing drawScene), and their SVG is hidden
 * and left as it was. It is built again once, when the movement stops.
 */
import { createEffect, createSignal, on, onCleanup, type JSX } from "solid-js";
import type { Drawing } from "lightweight-charts-drawing/core/types";
import { sceneImage, sceneTable, sceneTextTool } from "lightweight-charts-drawing/core/scene/text-tools";
import { sceneOf } from "lightweight-charts-drawing/core/scene";
import { drawScene } from "lightweight-charts-drawing/runtime/scene-canvas";
import { screenPoints } from "lightweight-charts-drawing/core/interact/project";
import { type OverlayCx } from "./context";

/** Two coordinate changes closer than this are a movement (ms). */
const MOVE_GAP_MS = 80;
/** The chart is at rest this long after its last coordinate change (ms). */
const REST_MS = 140;
/** With fewer drawings the SVG keeps up: nothing changes for them. */
const MIN_DRAWINGS = 4;

/** What createMoving adds to the overlay context. */
export interface MovingApi {
  /** The chart is moving and the SVG of `d` is frozen (painted on the canvas). */
  frozen: (d: Drawing) => boolean;
  moving: () => boolean;
  movingCanvas: () => JSX.Element;
}

export function createMoving(cx: OverlayCx): MovingApi {
  const { anchorFillAt, notShown, props } = cx;
  const [moving, setMoving] = createSignal(false);
  let canvas: HTMLCanvasElement | undefined;
  let last = 0;
  let rest: ReturnType<typeof setTimeout> | undefined;
  let font = "";

  /** Live in the SVG whatever the chart does: the user works on it. */
  const live = (d: Drawing) => cx.selIds().includes(d.id) || cx.hoveredId() === d.id;
  const frozen = (d: Drawing) => moving() && !live(d);

  /** The canvas holds a picture (cleared at rest, so nothing is left behind). */
  let painted = false;
  function clear() {
    if (painted && canvas) canvas.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
    painted = false;
  }

  function paint() {
    // Most drawings sit behind a series and the chart paints them (sunk.ts): then there is nothing to paint here.
    const todo = props.drawings.filter((d) => frozen(d) && !cx.isSunk(d) && !notShown(d));
    if (!todo.length && !painted) return;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const { w, h } = cx.size();
    const pw = Math.round(w * dpr), ph = Math.round(h * dpr);
    if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!font && cx.svg) font = getComputedStyle(cx.svg).fontFamily;
    painted = todo.length > 0;
    for (const d of todo) {
      const f = cx.drawFrame(d);
      const pts = f && screenPoints(f.coords, d, f.dims);
      if (!f || !pts) continue;
      const scene = d.kind === "text"
        ? sceneTextTool(d, pts[0], false, false, f.coords)
        : d.kind === "table"
          ? sceneTable(d, pts[0], false, null)
          : d.kind === "image"
            ? sceneImage(d, pts[0], false)
            : sceneOf(d, pts, { w: f.dims.w, h: f.dims.h, coords: f.coords, selected: false, hovered: false });
      ctx.save();
      ctx.translate(0, f.top);
      ctx.beginPath();
      ctx.rect(0, 0, f.dims.w, f.dims.h);
      ctx.clip();
      drawScene(ctx, scene, { fontFamily: font || "sans-serif", anchors: { fillAt: (y) => anchorFillAt(y + f.top), selected: false } });
      ctx.restore();
    }
  }

  createEffect(on(() => props.coordEpoch, () => {
    const now = performance.now();
    const quick = now - last < MOVE_GAP_MS;
    last = now;
    if (!moving()) {
      if (!quick || props.drawings.length < MIN_DRAWINGS || cx.drag() || cx.armedSpec()) return;
      setMoving(true);
    }
    clearTimeout(rest);
    rest = setTimeout(() => { setMoving(false); clear(); }, REST_MS);
    paint();
  }, { defer: true }));
  onCleanup(() => clearTimeout(rest));

  const movingCanvas = () => (
    <canvas
      ref={canvas}
      class="ot-drawings-layer"
      style={{
        left: `${props.leftInset ?? 0}px`,
        width: `calc(100% - ${props.leftInset ?? 0}px)`,
        height: "100%",
        "pointer-events": "none",
      }}
    />
  );

  return { frozen, moving, movingCanvas };
}
