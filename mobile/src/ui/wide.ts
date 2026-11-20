import {useWindowDimensions} from "react-native";

/** Widest a column of settings or text gets before it starts to read badly. */
export const READ_WIDTH = 720;
/** Widest a chat's messages get. */
export const THREAD_WIDTH = 860;
/** The chat list's width beside an open chat. */
export const LIST_PANE = 360;
/** From this width the chat list stays beside the open chat. */
export const TWO_PANE = 840;

/** How much room the screen gives right now (it changes when a tablet turns or the app shares the screen). */
export function useWide(): {width: number; tablet: boolean; twoPane: boolean} {
  const {width, height} = useWindowDimensions();
  return {width, tablet: Math.min(width, height) >= 600, twoPane: width >= TWO_PANE};
}
