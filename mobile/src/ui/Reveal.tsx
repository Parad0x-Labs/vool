import React, {createContext, useCallback, useContext, useRef} from "react";
import {ScrollView, type NativeScrollEvent, type NativeSyntheticEvent, type ScrollViewProps, type View} from "react-native";

type Reveal = (target: View | null) => void;
const RevealContext = createContext<Reveal>(() => {});

/** Brings a part of the page into view, e.g. settings that just opened below the bottom edge. */
export const useReveal = () => useContext(RevealContext);

/**
 * A scrolling page whose parts can ask to be shown with useReveal(). It scrolls only as far as needed:
 * the part's bottom comes into view, and its top never goes above the page's top edge.
 */
export function RevealScrollView({children, onScroll, ...props}: ScrollViewProps & {children?: React.ReactNode}) {
  const scroll = useRef<ScrollView>(null);
  const offset = useRef(0);
  const reveal = useCallback<Reveal>(target => {
    const page = scroll.current;
    // The scroll view's own native view (RN 0.76 hands it out; older versions are that view already).
    const host = page as unknown as {getNativeScrollRef?: () => View | null} | null;
    const frame = host?.getNativeScrollRef?.() ?? (page as unknown as View | null);
    if (!page || !frame || !target) return;
    frame.measureInWindow((_px, pageTop, _pw, pageHeight) => {
      target.measureInWindow((_x, top, _w, height) => {
        const margin = 16;
        const below = top + height + margin - (pageTop + pageHeight);
        if (below <= 0) return;
        const room = Math.max(0, top - pageTop - margin);
        page.scrollTo({y: offset.current + Math.min(below, room), animated: true});
      });
    });
  }, []);
  const track = useCallback((event: NativeSyntheticEvent<NativeScrollEvent>) => {
    offset.current = event.nativeEvent.contentOffset.y;
    onScroll?.(event);
  }, [onScroll]);
  return <RevealContext.Provider value={reveal}>
    <ScrollView ref={scroll} scrollEventThrottle={16} {...props} onScroll={track}>{children}</ScrollView>
  </RevealContext.Provider>;
}
