import {tr} from "../i18n/index.ts";

/** Where a save went ("folder|link" from the phone), as a line to show and a link that opens it there. */
export function savedPlace(answer: string, count = 0): {text: string; link: string} {
  const [place, link = ""] = answer.split("|");
  const gallery = !!link && /^(Pictures|Movies)\//.test(place);
  const folder = gallery ? tr("your gallery (VOOL album)") : place;
  const text = count ? tr("Saved {count} to {folder}.", {count, folder}) : tr("Saved to {folder}.", {folder});
  // iPhone and iPad: photos and videos go into the Photos app itself.
  return {text, link: gallery ? link : place === "Photos" ? "photos-redirect://" : ""};
}
