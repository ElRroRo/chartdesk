/*
 * Chart Desk: the stock screener's first download. A new install has only the symbols it charted; the screener needs
 * the daily history of the whole US market, which chartd downloads when asked (chartd/market.py fill()). This line
 * offers it, then shows its progress; it is gone once chartd has built the screener's data.
 */
import { Match, Show, Switch, createSignal, onCleanup, onMount } from "solid-js";
import { translate, translateWith } from "../i18n/translator";

type Progress = {
  filled: boolean;
  running: boolean;
  building: boolean;
  done: number;
  total: number;
  errors: number;
  secondsLeft: number | null;
  stateAsof: string | null;
};

const FILL = "http://127.0.0.1:8798/chartdesk/v1/screener/fill";
const fmt = (n: number) => n.toLocaleString();

export function ScreenerFill() {
  const [p, setP] = createSignal<Progress | null>(null);
  const ask = async (method: "GET" | "POST" = "GET") => {
    try {
      const r = await fetch(FILL, { method });
      if (r.ok) setP((await r.json()) as Progress);
    } catch {
      /* chartd not up yet: the next poll */
    }
  };
  let timer: number | undefined;
  onMount(() => {
    void ask();
    timer = window.setInterval(() => void ask(), 5000);
  });
  onCleanup(() => window.clearInterval(timer));
  const shown = () => {
    const x = p();
    return !!x && (!x.filled || !x.stateAsof);
  };
  return (
    <Show when={shown()}>
      <div
        class="cd-screener-fill"
        style={{
          display: "flex",
          "align-items": "center",
          gap: "12px",
          margin: "6px 12px",
          padding: "10px 12px",
          "border-radius": "6px",
          border: "1px solid var(--color-border-primary-neutral-light)",
          background: "var(--color-container-fill-primary-neutral-extra-light)",
          color: "var(--color-content-primary-neutral-bold)",
          "font-size": "13px",
          "line-height": "1.4",
        }}
      >
        <Switch>
          <Match when={p()!.running}>
            <span style={{ "font-variant-numeric": "tabular-nums" }}>
              {p()!.secondsLeft != null
                ? translateWith(
                    "Downloading the market's history for the screener: {0} of {1}, about {2} min left.",
                    fmt(p()!.done),
                    fmt(p()!.total),
                    Math.max(1, Math.round(p()!.secondsLeft! / 60)),
                  )
                : translateWith("Downloading the market's history for the screener: {0} of {1}.", fmt(p()!.done), fmt(p()!.total))}
            </span>
          </Match>
          <Match when={p()!.filled}>
            <span>{translate("The market's history is downloaded. The screener's list appears within 15 minutes.")}</span>
          </Match>
          <Match when={true}>
            <span style={{ flex: "1" }}>
              {translate(
                "The stock screener needs the daily history of every US stock and ETF: about 11,700 symbols, 2 GB on disk, roughly 45 minutes in the background. You can keep charting meanwhile.",
              )}
            </span>
            <button
              type="button"
              onClick={() => void ask("POST")}
              style={{
                padding: "6px 14px",
                "border-radius": "6px",
                border: "none",
                background: "var(--color-brand)",
                color: "var(--color-white)",
                cursor: "pointer",
                "white-space": "nowrap",
                font: "inherit",
              }}
            >
              {translate("Download")}
            </button>
          </Match>
        </Switch>
      </div>
    </Show>
  );
}
