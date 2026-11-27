import { useState } from "react";
import { toast } from "sonner";
import { Bot, Loader2, Sparkles } from "lucide-react";
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { http, errMsg, fmtPrice } from "@/lib/api";

const ACTION_CLS = { MANTENER: "text-emerald-300", BREAKEVEN: "text-amber-300", CERRAR: "text-rose-300", AJUSTAR: "text-sky-300" };
const BTN = "inline-flex items-center gap-1.5 rounded-sm px-3 py-1.5 font-mono text-[11px] font-semibold uppercase tracking-wider transition-colors disabled:opacity-50";

function Result({ r }) {
  return (
    <div data-testid="position-ai-result" className="space-y-2 rounded-sm border border-[#1E293B] bg-[#07090E] p-3 text-[12px]">
      <p className="font-mono text-[11px] uppercase tracking-wider">
        <span data-testid="position-ai-result-action" className={`font-bold ${ACTION_CLS[r.action] || ""}`}>{r.action}</span>
        <span className="text-slate-400"> · confianza {r.confidence}% · {r.r_now >= 0 ? "+" : ""}{r.r_now?.toFixed(2)} R</span>
      </p>
      {(r.sl || r.tp) && <p data-testid="position-ai-result-levels" className="font-mono text-[11px] text-sky-300">Propuesta: {r.sl ? `SL ${fmtPrice(r.sl)}` : ""}{r.sl && r.tp ? " · " : ""}{r.tp ? `TP ${fmtPrice(r.tp)}` : ""}</p>}
      <p className="leading-relaxed text-slate-300">{r.reasoning}</p>
      <p data-testid="position-ai-result-acted" className="font-mono text-[11px] text-slate-400">{r.acted ? `Acción aplicada: ${r.acted}` : "Solo opinión: no se ha cambiado nada en la operación."}</p>
    </div>
  );
}

/** Per-position AI analysis with an explicit confirmation (tokens): opinion only, or let the AI act (close, keep, breakeven or move SL/TP). */
export function PositionAiButton({ p, compact = false }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(null);
  const [res, setRes] = useState(null);
  const run = async (act) => {
    setBusy(act ? "act" : "opinion");
    try {
      const { data } = await http.post(`/positions/${p.id}/ai-analyze`, { act });
      setRes(data);
      toast.success(`IA · ${p.symbol}: ${data.action} (${data.confidence}%)${data.acted ? ` → ${data.acted}` : ""}`);
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(null); }
  };
  const close = (o) => { if (!o && !busy) { setOpen(false); setRes(null); } };
  return (
    <>
      <button data-testid={`position-ai-btn-${p.id}`} onClick={() => setOpen(true)} title="Analizar esta operación con IA"
        className={compact ? "inline-flex h-7 w-7 items-center justify-center rounded-sm border border-violet-400/40 text-violet-300 transition-colors hover:bg-violet-500/15"
          : "inline-flex items-center gap-1 rounded-sm border border-violet-400/40 px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-wider text-violet-300 transition-colors hover:bg-violet-500/10"}>
        <Sparkles className="h-3.5 w-3.5" />{!compact && " Analizar IA"}
      </button>
      <AlertDialog open={open} onOpenChange={close}>
        <AlertDialogContent data-testid="position-ai-dialog" className="border-[#1E293B] bg-[#0D111A]">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Analizar {p.symbol} {p.side === "long" ? "LONG" : "SHORT"} con IA</AlertDialogTitle>
            <AlertDialogDescription>
              Cada análisis consume tokens de IA. Elige si solo quieres su opinión o que actúe sobre la operación: cerrarla, mantenerla, mover el stop a breakeven o cambiar el SL/TP{p.live ? " (modo REAL: los cambios se envían al bróker)" : ""}.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {res && <Result r={res} />}
          <AlertDialogFooter className="gap-2">
            <AlertDialogCancel data-testid="position-ai-cancel" disabled={!!busy}>{res ? "Cerrar" : "Cancelar"}</AlertDialogCancel>
            {!res && <>
              <button data-testid="position-ai-opinion" disabled={!!busy} onClick={() => run(false)} className={`${BTN} border border-violet-400/50 text-violet-200 hover:bg-violet-500/15`}>
                {busy === "opinion" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />} Solo opinión
              </button>
              <button data-testid="position-ai-act" disabled={!!busy} onClick={() => run(true)} className={`${BTN} bg-violet-500 text-white hover:bg-violet-400`}>
                {busy === "act" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Bot className="h-3.5 w-3.5" />} Que la IA actúe
              </button>
            </>}
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
