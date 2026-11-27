import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const MINUTES = [5, 10, 15, 20, 25, 30];
const LAST_MIN = 55;
export const maxFollowUps = (minutes) => Math.min(10, Math.floor(LAST_MIN / minutes));
const pad = (n) => String(n).padStart(2, "0");

const Pick = ({ label, value, onChange, options, render, testId }) => (
  <div>
    <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</p>
    <Select value={String(value)} onValueChange={(v) => onChange(Number(v))}>
      <SelectTrigger data-testid={testId} className="h-9 border-[#1E293B] bg-[#07090E] font-mono text-xs"><SelectValue /></SelectTrigger>
      <SelectContent className="border-[#1E293B] bg-[#0D111A]">
        {options.map((o) => <SelectItem key={o} value={String(o)} className="font-mono text-xs">{render(o)}</SelectItem>)}
      </SelectContent>
    </Select>
  </div>
);

export default function FollowUpSettings({ form, set }) {
  if (form.timeframe !== "1h") return null;
  const minutes = form.followup_minutes ?? 5;
  const count = Math.min(form.followup_count ?? 5, maxFollowUps(minutes));
  const marks = Array.from({ length: count }, (_, k) => `:${pad((k + 1) * minutes)}:05`);
  const setMinutes = (m) => { set("followup_minutes", m); set("followup_count", Math.min(count, maxFollowUps(m))); };
  return (
    <div data-testid="settings-followups" className="space-y-2">
      <div className="grid grid-cols-2 gap-3">
        <Pick label="Seguimientos por hora" value={count} onChange={(v) => set("followup_count", v)} testId="settings-followup-count-select"
          options={Array.from({ length: maxFollowUps(minutes) + 1 }, (_, k) => k)} render={(o) => (o === 0 ? "Ninguno (solo cierre)" : `${o}`)} />
        <Pick label="Cada cuántos minutos" value={minutes} onChange={setMinutes} testId="settings-followup-minutes-select"
          options={MINUTES} render={(o) => `${o} min`} />
      </div>
      <p data-testid="settings-followups-hint" className="text-[11px] text-slate-500">
        Solo en 1h. Además del escaneo al cierre de vela (:00:05), el bot vuelve a analizar la vela en curso
        {count ? <> en <span className="font-mono text-slate-300">{marks.join(" · ")}</span> y después espera al siguiente cierre.</> : <>: ahora mismo no hay seguimientos, solo se escanea al cierre.</>}
        {" "}Cada seguimiento consume llamadas a la IA.
      </p>
    </div>
  );
}
