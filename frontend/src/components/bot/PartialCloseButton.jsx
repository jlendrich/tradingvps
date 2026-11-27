import { useState } from "react";
import { toast } from "sonner";
import { Loader2, Scissors } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { http, errMsg } from "@/lib/api";

const PRESETS = [25, 50, 75];

/** Manual partial close: pick a % of the open size and close it at market; the rest keeps its SL/TP. */
export function PartialCloseButton({ p, onDone, compact = false }) {
  const [open, setOpen] = useState(false);
  const [pct, setPct] = useState(50);
  const [busy, setBusy] = useState(false);
  const ok = Number(pct) > 0 && Number(pct) < 100;
  const est = (p.unrealized_pnl || 0) * (Number(pct) || 0) / 100;
  const run = async () => {
    setBusy(true);
    try {
      const { data } = await http.post(`/positions/${p.id}/close-partial`, { pct: Number(pct) });
      toast.success(`${p.symbol}: cerrado ${pct}% · PnL ${data.pnl >= 0 ? "+" : ""}${data.pnl.toFixed(2)} · resto ${data.rest_qty}`);
      setOpen(false);
      onDone?.();
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
  };
  return (
    <Popover open={open} onOpenChange={(o) => !busy && setOpen(o)}>
      <PopoverTrigger asChild>
        <button data-testid={`position-partial-btn-${p.id}`} title="Cerrar una parte de la operación"
          className={compact ? "inline-flex h-7 w-7 items-center justify-center rounded-sm border border-amber-500/40 text-amber-300 transition-colors hover:bg-amber-500/15"
            : "inline-flex items-center gap-1 rounded-sm border border-amber-500/40 px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-wider text-amber-300 transition-colors hover:bg-amber-500/10"}>
          <Scissors className="h-3.5 w-3.5" />{!compact && " Parcial"}
        </button>
      </PopoverTrigger>
      <PopoverContent data-testid="position-partial-popover" align="start" className="w-64 border-[#1E293B] bg-[#0D111A] p-3">
        <p className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Cierre parcial · {p.symbol} {p.side === "long" ? "LONG" : "SHORT"}</p>
        <div className="mt-2 flex gap-1.5">
          {PRESETS.map((v) => (
            <button key={v} data-testid={`position-partial-preset-${v}`} onClick={() => setPct(v)}
              className={`flex-1 rounded-sm border px-2 py-1 font-mono text-[11px] transition-colors ${Number(pct) === v ? "border-amber-400 bg-amber-500/15 text-amber-200" : "border-[#1E293B] text-slate-300 hover:border-amber-500/40"}`}>{v}%</button>
          ))}
        </div>
        <label className="mt-2 block">
          <span className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Porcentaje a cerrar</span>
          <input data-testid="position-partial-input" type="number" min={1} max={99} step={1} value={pct} onChange={(e) => setPct(e.target.value)}
            className="num mt-1 h-8 w-full rounded-sm border border-[#1E293B] bg-[#07090E] px-2 text-sm text-slate-100 outline-none focus:border-amber-500/60" />
        </label>
        <p data-testid="position-partial-estimate" className={`mt-2 font-mono text-[11px] ${est >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
          PnL estimado de esa parte: {est >= 0 ? "+" : ""}{est.toFixed(2)}{p.live ? " · REAL: se envía al bróker" : ""}
        </p>
        <button data-testid="position-partial-confirm" disabled={!ok || busy} onClick={run}
          className="mt-3 inline-flex w-full items-center justify-center gap-1.5 rounded-sm bg-amber-500 px-3 py-1.5 font-mono text-[11px] font-semibold uppercase tracking-wider text-black transition-colors hover:bg-amber-400 disabled:opacity-50">
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Scissors className="h-3.5 w-3.5" />} Cerrar {ok ? `${Number(pct)}%` : ""}
        </button>
      </PopoverContent>
    </Popover>
  );
}
