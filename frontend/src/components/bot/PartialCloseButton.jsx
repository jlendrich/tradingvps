import { useState } from "react";
import { toast } from "sonner";
import { Loader2, Scissors } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { http, errMsg } from "@/lib/api";

const PCTS = [25, 50, 75];

export default function PartialCloseButton({ p, onChanged, className = "" }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(null);
  const run = async (pct) => {
    setBusy(pct);
    try {
      await http.post(`/positions/${p.id}/partial-close`, { pct });
      toast.success(`Cierre parcial del ${pct}% de ${p.symbol} ejecutado`);
      setOpen(false);
      onChanged?.();
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(null); }
  };
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button data-testid={`partial-close-${p.id}`} title="Cerrar una parte de la posición a mercado; el resto sigue abierto con su SL/TP"
          className={`inline-flex items-center gap-1 rounded-sm border border-emerald-500/40 px-2 py-1 font-mono text-[10px] uppercase text-emerald-300 transition-colors hover:bg-emerald-500/15 ${className}`}>
          <Scissors className="h-3 w-3" /> Parcial
        </button>
      </PopoverTrigger>
      <PopoverContent data-testid={`partial-close-menu-${p.id}`} align="end" className="z-[90] w-60 border-[#1E293B] bg-[#0D111A] p-3 text-slate-200">
        <p className="text-[12px] font-semibold">Cerrar parte de {p.symbol}</p>
        <p className="mt-0.5 text-[11px] text-slate-500">A mercado{p.live ? ", con dinero real" : ""}. El resto sigue abierto con su SL/TP.</p>
        <div className="mt-2.5 grid grid-cols-3 gap-1.5">
          {PCTS.map((pct) => (
            <button key={pct} data-testid={`partial-close-${p.id}-${pct}`} disabled={busy !== null} onClick={() => run(pct)}
              className="inline-flex h-8 items-center justify-center rounded-sm border border-emerald-500/40 font-mono text-xs text-emerald-300 transition-colors hover:bg-emerald-500/15 disabled:opacity-50">
              {busy === pct ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : `${pct}%`}
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
