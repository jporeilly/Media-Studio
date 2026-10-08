/**
 * THE hide rule of screen capture (T3), as a hook: true only where the shell's bridge is there AND the shell
 * grants this page the capture commands - `probeCapture` asks one cheap command once per bridge (a page the
 * shell's capability refuses has the bridge too, so the bridge alone is not enough). False until the shell
 * has answered, so nothing is offered that would then fail; the answer is kept, so later mounts start from
 * it. The Dashboard's Capture tile and everything capture on the Projects page ask this and nothing else.
 */
import { useEffect, useState } from "react";
import { knownCapture, probeCapture } from "./capture";

export function useCaptureAvailable(): boolean {
  const [available, setAvailable] = useState<boolean>(() => knownCapture() === true);
  useEffect(() => {
    let live = true;
    void probeCapture().then((ok) => { if (live) setAvailable(ok); });
    return () => { live = false; };
  }, []);
  return available;
}
