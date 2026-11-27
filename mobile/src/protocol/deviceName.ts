/** The phone's own name for the computer's list of paired phones: its maker and model, as the phone reports them.
 *
 * sls, 2026-11-27: every phone paired by QR was listed on the computer as "VOOL phone", so with more than one
 * paired (or an old one left over) "Unpair this phone" did not say which phone it was. Android reports its maker
 * and model; iOS reports neither to an app, so an iPhone or iPad is named as such. Never empty, at most 64 chars. */
export function phoneModelName(os: string, constants: Record<string, unknown> | undefined, isPad = false): string {
  const text = (value: unknown) => (typeof value === "string" ? value.replace(/[\u0000-\u001f\u007f]/g, "").trim() : "");
  if (os === "ios") return isPad ? "iPad" : "iPhone";
  const maker = text(constants?.Manufacturer) || text(constants?.Brand);
  const model = text(constants?.Model);
  const name = model && maker && !model.toLowerCase().startsWith(maker.toLowerCase()) ? `${maker} ${model}` : model || maker;
  const shown = name ? name.charAt(0).toUpperCase() + name.slice(1) : "";
  return (shown || "Android phone").slice(0, 64);
}
