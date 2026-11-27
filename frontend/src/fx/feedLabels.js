// Header badge + tooltip for the market-data source of the MetaTrader bots, driven by status.data_feed (what the engine really used).
const TF = (tf) => (tf || "").toUpperCase();
const pairs = (list) => list.map((x) => x.symbol).join(", ");

export const dataLabel = (bk) => (status) => {
  const f = status.data_feed;
  if (status.mode === "REAL") {
    if (!f || !f.signals) return `Datos: ${bk} · esperando análisis`;
    if (f.blocked?.length) return `Datos: ${bk} · ${f.blocked.length} par${f.blocked.length > 1 ? "es" : ""} sin velas`;
    if (f.prices !== "broker") return `Señales: ${bk} · precios ${f.prices === "mixed" ? "incompletos" : "sin cotización"}`;
    return `Señales y precios: ${bk} · Ejecución: ${bk}`;
  }
  if (f?.signals === "broker") return `Datos: ${bk} (puente MT) · paper`;
  if (f?.signals === "mixed") return `Datos: ${bk} + Yahoo · paper`;
  return "Datos: Yahoo Finance";
};

export const dataTooltip = (bk) => (status) => {
  const f = status.data_feed;
  const vol = f?.volume ? " Incluyen el volumen de ticks del bróker, que la IA usa como actividad." : "";
  if (status.mode === "REAL") {
    const parts = [`En modo REAL todo el análisis usa los datos de tu cuenta ${bk} vía puente MetaTrader: velas ${TF(f?.tf) || "de cada temporalidad"} y superiores, precio bid/ask y spread real. Yahoo Finance no se usa para operar.${vol}`];
    if (f?.blocked?.length) parts.push(`Sin entradas nuevas en ${pairs(f.blocked)} hasta que el puente entregue sus velas (${f.blocked[0].error}).`);
    if (f?.no_quote?.length) parts.push(`Sin cotización reciente del bróker: ${pairs(f.no_quote)} · no se abren operaciones en esos pares.`);
    return parts.join(" ");
  }
  if (f?.signals === "broker") return `Paper trading con las velas y precios de tu cuenta ${bk} (puente MT conectado).${vol} Si el puente falla, se usa Yahoo Finance como respaldo.`;
  return `Precios y velas de Yahoo Finance (sin API key): pares spot y futuros de oro (GC=F) y plata (SI=F), sin volumen fiable en forex. En modo REAL las señales, los precios y las órdenes vienen de tu cuenta ${bk} vía puente MetaTrader.`;
};
