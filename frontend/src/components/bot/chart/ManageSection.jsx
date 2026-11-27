const INPUT = "num h-8 w-full rounded-sm border border-[#1E293B] bg-[#07090E] px-2 text-sm text-slate-100 outline-none focus:border-amber-500/60 disabled:opacity-40";
const LABEL = "mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500";

export const manageDefaults = (s = {}, ema = false) => ema ? {
  ema_be_on: !!s.ema_be_on, ema_be_atr: s.ema_be_atr ?? 1, ema_trail_on: !!s.ema_trail_on, ema_trail_atr: s.ema_trail_atr ?? 2,
  ema_partial_on: !!s.ema_partial_on, ema_partial_at_atr: s.ema_partial_at_atr ?? 1.5, ema_partial_pct: s.ema_partial_pct ?? 50,
  ai_review: !!s.ai_position_review_enabled,
} : ({
  breakeven_enabled: s.breakeven_enabled ?? true, be_start_r: s.be_start_r ?? 1,
  trailing_enabled: s.trailing_enabled ?? true, trail_start_r: s.trail_start_r ?? 1.5, trail_atr_mult: s.trail_atr_mult ?? 1,
  partial_tp_enabled: s.partial_tp_enabled ?? false, partial_tp_r: s.partial_tp_r ?? 1.5, partial_tp_pct: s.partial_tp_pct ?? 50,
  ai_review: !!s.ai_position_review_enabled,
});

const inRange = (v, lo, hi) => Number(v) >= lo && Number(v) <= hi;
export const manageValid = (m) => "ema_be_on" in m
  ? ["ema_be_atr", "ema_trail_atr", "ema_partial_at_atr"].every((k) => inRange(m[k], 0.1, 10)) && inRange(m.ema_partial_pct, 10, 90)
  : ["be_start_r", "trail_start_r", "trail_atr_mult", "partial_tp_r"].every((k) => inRange(m[k], 0.5, 3)) && inRange(m.partial_tp_pct, 25, 75);

const AI_CARD = { key: "ai_review", id: "ai-review", title: "Revisión automática de IA", desc: "La IA revisa solo esta operación (mantener, breakeven o cerrar) con el modo y la confianza mínima de Ajustes.", fields: [] };
const EMA_CARDS = [
  { key: "ema_be_on", id: "breakeven", title: "Breakeven", desc: "Mueve el stop a la entrada cuando el precio avanza N × ATR a favor.", fields: [["ema_be_atr", "Activar a (× ATR)", 0.1]] },
  { key: "ema_trail_on", id: "trailing", title: "Trailing stop", desc: "El stop sigue al precio a una distancia fija en ATR.", fields: [["ema_trail_atr", "Distancia (ATR)", 0.1]] },
  { key: "ema_partial_on", id: "partial", title: "Cierre parcial", desc: "Cierra una parte al avanzar N × ATR y deja correr el resto hasta el cruce.", fields: [["ema_partial_at_atr", "Cerrar a (× ATR)", 0.1], ["ema_partial_pct", "Parte (%)", 5]] },
  AI_CARD,
];
const CARDS = [
  { key: "breakeven_enabled", id: "breakeven", title: "Breakeven", desc: "Mueve el stop a la entrada al alcanzar el nivel.", fields: [["be_start_r", "Activar a (R)", 0.1]] },
  { key: "trailing_enabled", id: "trailing", title: "Trailing stop", desc: "El stop sigue al precio a una distancia fija en ATR.", fields: [["trail_start_r", "Desde (R)", 0.1], ["trail_atr_mult", "Distancia (ATR)", 0.1]] },
  { key: "partial_tp_enabled", id: "partial", title: "Cierre parcial", desc: "Cierra una parte al alcanzar el nivel y deja correr el resto.", fields: [["partial_tp_r", "Cerrar a (R)", 0.1], ["partial_tp_pct", "Parte (%)", 5]] },
  AI_CARD,
];

function Card({ c, m, set }) {
  const on = !!m[c.key];
  return (
    <div data-testid={`manage-card-${c.id}`} className={`rounded-sm border p-2.5 transition-colors ${on ? "border-emerald-500/40 bg-emerald-500/5" : "border-[#1E293B] bg-[#07090E]/60"}`}>
      <label className="flex cursor-pointer items-center gap-2">
        <input data-testid={`manage-${c.id}-toggle`} type="checkbox" checked={on} onChange={(e) => set(c.key, e.target.checked)} className="h-3.5 w-3.5 accent-emerald-500" />
        <span className="text-[13px] font-semibold text-slate-100">{c.title}</span>
        <span data-testid={`manage-${c.id}-state`} className={`ml-auto font-mono text-[10px] uppercase tracking-wider ${on ? "text-emerald-400" : "text-slate-500"}`}>{on ? "Activado" : "Desactivado"}</span>
      </label>
      <p className="mt-1 text-[11px] leading-snug text-slate-400">{c.desc}</p>
      {c.fields.length > 0 && <div className={`mt-2 grid gap-2 ${c.fields.length > 1 ? "grid-cols-2" : "grid-cols-1"}`}>
        {c.fields.map(([k, label, step]) => (
          <label key={k}><span className={LABEL}>{label}</span>
            <input data-testid={`manage-${k}-input`} type="number" step={step} disabled={!on} value={m[k]} onChange={(e) => set(k, e.target.value)} className={INPUT} /></label>
        ))}
      </div>}
    </div>
  );
}

export function ManageSection({ manage, onChange }) {
  const set = (k, v) => onChange({ ...manage, [k]: v });
  return (
    <div data-testid="manual-order-manage" className="space-y-2">
      <p className={LABEL}>Gestión de esta operación · cada casilla empieza como en los ajustes actuales del bot</p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {("ema_be_on" in manage ? EMA_CARDS : CARDS).map((c) => <Card key={c.id} c={c} m={manage} set={set} />)}
      </div>
      {!manageValid(manage) && <p data-testid="manual-order-manage-error" className="text-[11px] text-rose-300">{"ema_be_on" in manage ? "Niveles en ATR entre 0,1 y 10; parte del cierre parcial entre 10 y 90%." : "Niveles en R y distancia ATR entre 0,5 y 3; parte del cierre parcial entre 25 y 75%."}</p>}
    </div>
  );
}
